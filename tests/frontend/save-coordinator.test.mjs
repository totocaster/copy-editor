import test from 'node:test'
import assert from 'node:assert/strict'
import { createSaveCoordinator } from '../../frontend/save-coordinator.mjs'
import { createDraftStore } from '../../frontend/recovery.mjs'
import { gateSavedAction } from '../../frontend/save-gate.mjs'

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function makeCoordinator(request) {
  let backup = null
  const states = []
  const save = createSaveCoordinator({
    snapshot: { title: 'First', content: { type: 'doc' } }, version: 3,
    request, persist: (snapshot, version) => { backup = { snapshot, version } },
    clear: () => { backup = null }, onState: (state) => states.push(state), delay: 60_000,
  })
  return { save, states, get backup() { return backup } }
}

test('serializes edits made during an in-flight save and advances the base version', async () => {
  const first = deferred()
  const sent = []
  const context = makeCoordinator((payload) => {
    sent.push(payload)
    return sent.length === 1 ? first.promise : Promise.resolve({ version: 5, word_count: 2 })
  })
  context.save.change({ title: 'Changed', content: { type: 'doc', text: 'one' } })
  const pending = context.save.flush()
  context.save.change({ title: 'Changed again', content: { type: 'doc', text: 'two' } })
  assert.equal(context.backup.snapshot.content.text, 'two')
  first.resolve({ version: 4, word_count: 1 })
  assert.equal(await pending, true)
  assert.deepEqual(sent.map((payload) => [payload.title, payload.base_version]), [['Changed', 3], ['Changed again', 4]])
  assert.equal(context.save.state, 'saved')
  assert.equal(context.backup, null)
})

test('failed save remains unsaved and blocks a dependent action until retry succeeds', async () => {
  let fail = true
  const context = makeCoordinator(() => {
    if (fail) throw new Error('offline')
    return { version: 4, word_count: 1 }
  })
  context.save.change({ title: 'Draft', content: { type: 'doc', text: 'draft' } })
  assert.equal(await context.save.flush(), false)
  assert.equal(context.save.state, 'error')
  assert.equal(context.backup.snapshot.title, 'Draft')
  fail = false
  assert.equal(await context.save.flush(), true)
  assert.equal(context.save.state, 'saved')
})

test('409 holds edits until explicit rebase with a preserved prior snapshot', async () => {
  const sent = []
  const context = makeCoordinator((payload) => {
    sent.push(payload)
    if (sent.length === 1) throw Object.assign(new Error('conflict'), { status: 409 })
    return { version: 8, word_count: 1 }
  })
  context.save.change({ title: 'Mine', content: { type: 'doc', text: 'mine' } })
  assert.equal(await context.save.flush(), false)
  assert.equal(context.save.state, 'conflict')
  assert.equal(await context.save.flush(), false)
  assert.equal(sent.length, 1)
  context.save.rebase(7, { preserveBefore: true })
  assert.equal(await context.save.flush(), true)
  assert.equal(sent[1].base_version, 7)
  assert.equal(sent[1].preserve_before, true)
})

test('per-page local drafts keep large in-flight text separate', () => {
  const values = new Map()
  const storage = {
    get length() { return values.size }, key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value) },
    removeItem: (key) => { values.delete(key) },
  }
  const a = createDraftStore(storage, 'doc', 'tab-a')
  const b = createDraftStore(storage, 'doc', 'tab-b')
  const content = { type: 'doc', text: 'x'.repeat(128 * 1024) }
  a.write({ title: 'A', content }, 1)
  b.write({ title: 'B', content: { type: 'doc', text: 'other' } }, 2)
  assert.equal(a.list().length, 2)
  assert.equal(a.list().find((item) => item.key === a.key).snapshot.content.text.length, 128 * 1024)
  a.clear()
  assert.equal(b.list().length, 1)
  const old = b.list()[0]
  b.write({ title: 'B newer', content }, 2)
  assert.equal(b.discard(old), false)
  assert.equal(b.list()[0].snapshot.title, 'B newer')
})

test('a recovered conflict remains marked for review across pagehide writes', () => {
  const values = new Map()
  const storage = {
    get length() { return values.size }, key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value) },
    removeItem: (key) => { values.delete(key) },
  }
  const drafts = createDraftStore(storage, 'doc', 'new-page')
  const context = createSaveCoordinator({
    snapshot: { title: 'Server', content: { type: 'doc' } }, version: 5,
    request: () => { throw new Error('must not save before review') },
    persist: (...args) => drafts.write(...args), clear: () => drafts.clear(), delay: 60_000,
  })
  context.holdConflict()
  context.change({ title: 'Older draft', content: { type: 'doc', text: 'mine' } })
  assert.equal(context.requiresReview, true)
  assert.equal(context.state, 'conflict')
  drafts.write(context.snapshot, context.version, context.requiresReview)
  assert.equal(drafts.list()[0].requiresReview, true)
})

test('concurrent dependent actions share the in-flight save', async () => {
  const pending = deferred()
  let calls = 0
  const context = makeCoordinator(() => { calls++; return pending.promise })
  context.save.change({ title: 'Now', content: { type: 'doc', text: 'latest' } })
  const actions = []
  const first = gateSavedAction(context.save, (version) => { actions.push(['pass', version]) })
  const second = gateSavedAction(context.save, (version) => { actions.push(['export', version]) })
  assert.equal(calls, 1)
  assert.deepEqual(actions, [])
  pending.resolve({ version: 4, word_count: 1 })
  assert.deepEqual(await Promise.all([first, second]), [true, true])
  assert.deepEqual(actions, [['pass', 4], ['export', 4]])
})

test('failed save and unavailable local backup do not run dependent actions', async () => {
  const states = []
  const save = createSaveCoordinator({
    snapshot: { title: 'Original', content: { type: 'doc' } }, version: 1,
    request: () => { throw new Error('offline') },
    persist: () => { throw new Error('storage quota') }, clear: () => {},
    onState: (state) => states.push(state), delay: 60_000,
  })
  save.change({ title: 'Unsaved', content: { type: 'doc', text: 'mine' } })
  assert.ok(states.includes('backup-error'))
  let ran = false
  assert.equal(await gateSavedAction(save, () => { ran = true }), false)
  assert.equal(ran, false)
  assert.equal(save.dirty, true)
})
