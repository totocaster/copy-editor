// Entry point for the document page: editor, autosave, sidebar, menus.

import { Editor, Extension } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { CharacterCount, Placeholder } from '@tiptap/extensions'
import Typography from '@tiptap/extension-typography'

import { Annotation } from './annotation.js'
import { SlashMenu } from './slash.js'
import { createBubbleMenu } from './bubble.js'
import { createSidebar } from './sidebar.js'
import { locate } from './placement.js'
import { createSaveCoordinator } from './save-coordinator.mjs'
import { createDraftStore } from './recovery.mjs'
import { gateSavedAction } from './save-gate.mjs'

const data = JSON.parse(document.getElementById('workshop-data').textContent)
const { docId } = data
const JSON_HEADERS = { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Write-CSRF': document.querySelector('meta[name="write-csrf-token"]')?.content || '' }

const editorEl = document.getElementById('editor')
const column = document.getElementById('editor-column')
const statusEl = document.getElementById('save-status')
const wordsEl = document.getElementById('word-count')
const drawer = document.getElementById('drawer')
const passMenu = document.getElementById('pass-menu')
const exportMenu = document.getElementById('export-menu')
const shortcutsDialog = document.getElementById('shortcuts')
const runStrip = document.getElementById('run-strip')
const titleEl = document.getElementById('title')
const saveAlert = document.getElementById('save-alert')
const saveAlertText = document.getElementById('save-alert-text')
const retrySave = document.getElementById('retry-save')
const replaceServer = document.getElementById('replace-server')
const loadServer = document.getElementById('load-server')
const draftPanel = document.getElementById('draft-panel')

const uid = () =>
  crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('')

// --- autosave ---------------------------------------------------------------

let coordinator
let suppressUpdates = false
let draftStore
let recoveredDraft = null
try { draftStore = createDraftStore(localStorage, docId, uid()) } catch { draftStore = null }
let backupAvailable = !!draftStore

function setStatus(stateName, label) {
  statusEl.dataset.state = stateName
  statusEl.textContent = label
}

function snapshot() {
  return { content: editor.getJSON(), title: titleEl.value.trim() || 'Untitled' }
}

function showSaveState(state, error) {
  const labels = { saved: 'Saved', saving: 'Saving…', dirty: 'Unsaved', error: 'Save failed', conflict: 'Save conflict', 'backup-error': 'Backup unavailable' }
  setStatus(state, state === 'saved' ? savedLabel : labels[state])
  saveAlert.hidden = !['error', 'conflict', 'backup-error'].includes(state)
  retrySave.hidden = state !== 'error' && state !== 'backup-error'
  replaceServer.hidden = loadServer.hidden = state !== 'conflict'
  if (state === 'error') saveAlertText.textContent = 'Your changes are still here. Check your connection, then retry the save.'
  if (state === 'backup-error') saveAlertText.textContent = 'This browser could not keep a local copy. Keep this page open and retry the save.'
  if (state === 'conflict') saveAlertText.textContent = 'This document changed elsewhere. Your edits are kept here. Choose which version to keep.'
  if (error) console.error('save failed', error)
}

function markDirty() {
  if (!suppressUpdates) coordinator?.change(snapshot())
}

async function save() { return coordinator.flush() }
async function flushSave() { return coordinator.flush() }

window.addEventListener('pagehide', () => {
  if (coordinator?.dirty) {
    try {
      if (!draftStore) throw new Error('Local storage is unavailable')
      draftStore.write(snapshot(), coordinator.version, coordinator.requiresReview)
      backupAvailable = true
    } catch (error) { backupAvailable = false; showSaveState('backup-error', error) }
  }
})
window.addEventListener('beforeunload', (event) => {
  if (coordinator?.dirty && !backupAvailable) {
    event.preventDefault()
    event.returnValue = ''
  }
})
document.addEventListener('visibilitychange', () => {
  if (document.hidden && coordinator?.dirty) void coordinator.flush()
})

// --- editor -----------------------------------------------------------------

const editor = new Editor({
  element: editorEl,
  content: data.content,
  autofocus: false,
  editorProps: { attributes: { spellcheck: 'true' } },
  extensions: [
    StarterKit.configure({
      heading: { levels: [1, 2, 3] },
      link: { openOnClick: false, autolink: true },
      dropcursor: { color: 'rgb(28 25 23 / 0.4)', width: 2 },
    }),
    Placeholder.configure({
      placeholder: ({ node }) => (node.type.name === 'heading' ? 'Heading' : "Write, or type '/' for blocks…"),
    }),
    CharacterCount,
    Typography,
    Annotation,
    SlashMenu({ container: column }),
    Extension.create({
      name: 'workshopKeys',
      addKeyboardShortcuts() {
        return {
          'Mod-Alt-m': () => {
            annotate('note')
            return true
          },
        }
      },
    }),
  ],
  onUpdate: () => markDirty(),
})

// Highlights whose annotation is closed (or never existed) are stripped on load.
const beforePrune = editor.getJSON()
editor.commands.pruneAnnotations(data.openIds)
const prunedOnLoad = JSON.stringify(beforePrune) !== JSON.stringify(editor.getJSON())
let savedLabel = 'Saved'
coordinator = createSaveCoordinator({
  snapshot: snapshot(),
  version: data.version,
  request: async (payload) => {
    const res = await fetch(`/d/${docId}/content`, {
      method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const error = new Error(`HTTP ${res.status}`)
      error.status = res.status
      throw error
    }
    return res.json()
  },
  persist: (value, version, requiresReview) => {
    try {
      if (!draftStore) throw new Error('Local storage is unavailable')
      draftStore.write(value, version, requiresReview)
      backupAvailable = true
    } catch (error) { backupAvailable = false; throw error }
  },
  clear: () => draftStore?.clear(),
  onState: showSaveState,
  onSaved: (result) => {
    wordsEl.textContent = result.word_count
    savedLabel = result.revision ? `Saved · r${result.revision.seq}` : 'Saved'
    if (result.annotation_changes?.length) void sidebar.refresh()
    if (recoveredDraft && draftStore?.discard(recoveredDraft)) recoveredDraft = null
  },
})
if (prunedOnLoad) coordinator.change(snapshot())
titleEl.addEventListener('input', () => {
  document.title = `${titleEl.value.trim() || 'Untitled'} · Copy Editor`
  markDirty()
})

async function fetchServerContent() {
  const res = await fetch(`/d/${docId}/content`, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

retrySave.addEventListener('click', () => { void flushSave() })
replaceServer.addEventListener('click', async () => {
  try {
    const latest = await fetchServerContent()
    if (!window.confirm('Replace the latest saved text and title with your edits? The saved text will remain in revision history.')) return
    coordinator.rebase(latest.version, { preserveBefore: true })
    await flushSave()
  } catch (error) {
    console.error('could not check saved version', error)
    saveAlertText.textContent = 'Could not check the saved version. Check your connection and try again.'
  }
})
loadServer.addEventListener('click', async () => {
  if (!window.confirm('Load the latest saved version and discard your unsaved edits on this page?')) return
  try {
    const latest = await fetchServerContent()
    coordinator.adopt({ content: latest.content, title: latest.title }, latest.version)
    window.location.reload()
  } catch (error) {
    console.error('could not load saved version', error)
    saveAlertText.textContent = 'Could not load the saved version. Check your connection and try again.'
  }
})

function renderDrafts() {
  draftPanel.replaceChildren()
  let drafts = []
  try { drafts = draftStore?.list().filter((d) => d.key !== draftStore.key) || [] } catch (error) { showSaveState('backup-error', error) }
  draftPanel.hidden = !drafts.length
  if (!drafts.length) return
  const heading = document.createElement('strong')
  heading.textContent = `${drafts.length} unsaved ${drafts.length === 1 ? 'draft' : 'drafts'} found`
  draftPanel.append(heading)
  for (const draft of drafts) {
    const row = document.createElement('span')
    row.textContent = `${draft.snapshot.title} · ${new Date(draft.updatedAt).toLocaleString()} `
    const recover = document.createElement('button')
    recover.type = 'button'
    recover.className = 'btn btn-sm'
    recover.textContent = 'Recover draft'
    recover.addEventListener('click', async () => {
      if (localStorage.getItem(draft.key) !== draft.raw) { renderDrafts(); return }
      if (coordinator.dirty && !await flushSave()) return
      recoveredDraft = draft
      suppressUpdates = true
      try {
        editor.commands.setContent(draft.snapshot.content)
        titleEl.value = draft.snapshot.title
        document.title = `${draft.snapshot.title || 'Untitled'} · Copy Editor`
      } finally { suppressUpdates = false }
      if (draft.version !== coordinator.version || draft.requiresReview) coordinator.holdConflict()
      coordinator.change(snapshot())
      draftPanel.hidden = true
      sidebar.scheduleLayout()
    })
    const discard = document.createElement('button')
    discard.type = 'button'
    discard.className = 'btn-ghost btn-sm'
    discard.textContent = 'Discard draft'
    discard.addEventListener('click', () => { draftStore.discard(draft); renderDrafts() })
    row.append(recover, discard)
    draftPanel.append(row)
  }
}
renderDrafts()

const sidebar = createSidebar({ editor, docId, editorEl })
const bubble = createBubbleMenu({ editor, container: column, onAnnotate: annotate })
sidebar.layout()

// --- annotations ------------------------------------------------------------

async function annotate(kind) {
  const { from, to, empty } = editor.state.selection
  if (empty) return
  const quote = editor.state.doc.textBetween(from, to, '\n')
  if (!quote.trim()) return
  const id = uid()
  editor.chain().focus(null, { scrollIntoView: false }).setAnnotation({ id, kind }).setTextSelection({ from: to, to }).run()
  bubble.hide()
  try {
    const res = await fetch(`/d/${docId}/annotations`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ id, kind, quote, anchor_from: from, anchor_to: to }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
  } catch (err) {
    console.error('could not create annotation', err)
    editor.commands.removeAnnotation(id)
    return
  }
  await sidebar.refresh({ activeId: id, editing: id })
}

/** The top-most block currently in view; the reader's visual anchor. */
function anchorBlock() {
  const headerBottom = document.querySelector('.topbar')?.getBoundingClientRect().bottom ?? 0
  for (const block of editorEl.querySelectorAll('.tiptap > *')) {
    if (block.getBoundingClientRect().bottom > headerBottom + 1) return block
  }
  return null
}

/** Run an edit we make on the reader's behalf without letting the page move under them. */
function keepAnchored(fn) {
  const block = anchorBlock()
  const before = block ? block.getBoundingClientRect().top : null
  const result = fn()
  if (block && block.isConnected && before !== null) {
    const after = block.getBoundingClientRect().top
    if (Math.abs(after - before) > 0.5) window.scrollBy({ top: after - before, behavior: 'instant' })
  }
  return result
}

function applySuggestion(id, text) {
  keepAnchored(() => editor.commands.replaceAnnotationText(id, text || ''))
}

// --- AI passes: placing findings, bulk actions ------------------------------

const attempted = new Set()

async function placeFindings(list) {
  const todo = (list || []).filter((f) => f && f.id && !attempted.has(f.id))
  if (!todo.length) return
  const failed = []
  let placed = 0
  const chain = editor.chain()
  for (const f of todo) {
    attempted.add(f.id)
    const range = locate(editor.state.doc, f)
    if (range) {
      chain.addAnnotationAt(range[0], range[1], { id: f.id, kind: f.kind })
      placed++
    } else {
      failed.push(f.id)
    }
  }
  if (placed) {
    chain.run()
    markDirty()
  }
  if (failed.length) {
    await fetch(`/d/${docId}/annotations/bulk_status`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ ids: failed, status: 'orphaned' }),
    }).catch((err) => console.error(err))
  }
  await sidebar.refresh()
}

function pendingFromStrip() {
  const el = runStrip?.querySelector('script[data-pending]')
  if (!el) return []
  try {
    return JSON.parse(el.textContent || '[]')
  } catch {
    return []
  }
}

async function refreshStrip(runId) {
  if (!runId) return
  await htmx.ajax('GET', `/d/${docId}/runs/${runId}/strip`, { target: '#run-strip', swap: 'innerHTML' })
}

async function acceptAll(runId) {
  const cards = [...document.querySelectorAll(`.ann-card[data-run="${CSS.escape(runId)}"]`)]
    .filter((c) => c.dataset.status === 'open' || c.dataset.status === 'pending')
    .filter((c) => ((c.dataset.kind === 'suggest' || c.dataset.kind === 'check') && c.dataset.text) || c.dataset.kind === 'cut')
  if (!cards.length) return
  if (!window.confirm(`Apply ${cards.length} remaining suggestion${cards.length === 1 ? '' : 's'} from this pass?`)) return
  const done = []
  keepAnchored(() => {
    for (const card of cards) {
      const id = card.dataset.card
      const text = card.dataset.kind === 'cut' ? '' : card.dataset.text
      if (editor.commands.replaceAnnotationText(id, text)) done.push(id)
    }
  })
  if (done.length) {
    markDirty()
    await fetch(`/d/${docId}/annotations/bulk_status`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ ids: done, status: 'accepted' }),
    }).catch((err) => console.error(err))
  }
  await sidebar.refresh()
  await refreshStrip(runId)
}

document.body.addEventListener('htmx:afterSwap', (e) => {
  if (e.target === runStrip) placeFindings(pendingFromStrip())
})
document.body.addEventListener('annotations:removed', (e) => {
  for (const id of e.detail?.ids || []) {
    editor.commands.removeAnnotation(id)
    if (sidebar.state.activeId === id) sidebar.state.activeId = null
  }
  const strip = runStrip?.querySelector('.run-strip')
  if (strip?.dataset.run) refreshStrip(strip.dataset.run)
  sidebar.scheduleLayout()
})

// Findings that were waiting when the page loaded.
placeFindings(data.pending || [])

// Server-side status/kind changes arrive as HX-Trigger events.
document.body.addEventListener('annotation:changed', (e) => {
  const { id, status, kind } = e.detail || {}
  if (!id) return
  if (status === 'open') {
    editor.commands.setAnnotationKind(id, kind)
  } else {
    editor.commands.removeAnnotation(id)
    if (sidebar.state.activeId === id) sidebar.state.activeId = null
  }
  sidebar.scheduleLayout()
})
document.body.addEventListener('annotation:deleted', (e) => {
  const { id } = e.detail || {}
  if (!id) return
  editor.commands.removeAnnotation(id)
  if (sidebar.state.activeId === id) sidebar.state.activeId = null
  sidebar.scheduleLayout()
})
document.body.addEventListener('htmx:responseError', (e) => {
  console.error('request failed', e.detail?.xhr?.status, e.detail?.pathInfo?.finalRequestPath)
  if (e.detail?.xhr?.status === 409 && e.detail?.elt?.closest?.('[data-save-gated]')) {
    if (!coordinator.dirty) coordinator.change(snapshot())
    coordinator.holdConflict()
  }
})

document.body.addEventListener('htmx:confirm', (e) => {
  const gated = e.detail?.elt?.closest?.('[data-save-gated]')
  if (!gated) return
  e.preventDefault()
  void (async () => {
    await gateSavedAction(coordinator, () => {
      if (gated.dataset.saveGated === 'restore' &&
          !window.confirm(`Restore revision ${gated.dataset.restoreSeq}? The current text is saved as a revision first.`)) return
      e.detail.issueRequest(true)
    })
  })()
})
document.body.addEventListener('htmx:configRequest', (e) => {
  if (e.detail?.elt?.closest?.('[data-save-gated]')) e.detail.parameters.base_version = coordinator.version
})
document.body.addEventListener('htmx:afterRequest', (e) => {
  if (e.detail?.successful && e.detail?.elt?.closest?.('[data-save-gated="run"]')) closeMenu()
})

// --- drawer, pass menu, global keys ----------------------------------------

function openDrawer() {
  drawer.hidden = false
}
function closeDrawer() {
  drawer.hidden = true
  editor.commands.focus(null, { scrollIntoView: false })
}
function openMenu() {
  passMenu.hidden = false
  exportMenu.hidden = true
}
function closeMenu() {
  passMenu.hidden = true
}
function toggleExport() {
  exportMenu.hidden = !exportMenu.hidden
  if (!exportMenu.hidden) passMenu.hidden = true
}
function closeDismissMenus(except = null) {
  document.querySelectorAll('details.dismiss-menu[open]').forEach((d) => {
    if (d !== except) d.open = false
  })
}
document.addEventListener('click', (e) => {
  if (!passMenu.hidden && !e.target.closest('#pass-menu') && !e.target.closest('#pass-menu-btn')) closeMenu()
  if (!exportMenu.hidden && !e.target.closest('#export-menu') && !e.target.closest('#export-btn')) exportMenu.hidden = true
  if (!e.target.closest('details.dismiss-menu')) closeDismissMenus()
})
// `toggle` does not bubble; listen in the capture phase. One reason menu open at a time,
// and make sure it is visible when it opens near the bottom of the panel.
document.addEventListener('toggle', (e) => {
  const d = e.target
  if (!(d instanceof HTMLDetailsElement) || !d.classList.contains('dismiss-menu') || !d.open) return
  closeDismissMenus(d)
  d.closest('.ann-reveal')?.classList.add('is-open')
  requestAnimationFrame(() => d.querySelector('.reason-menu')?.scrollIntoView({ block: 'nearest' }))
}, true)

// --- export -----------------------------------------------------------------

function fallbackCopy(text) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } finally {
    ta.remove()
  }
  return ok
}

function flash(el, label) {
  const original = el.textContent
  el.textContent = label
  setTimeout(() => {
    el.textContent = original
    exportMenu.hidden = true
  }, 1100)
}

async function exportDoc(format, mode, el) {
  if (!await flushSave()) { flash(el, 'Save first'); return }
  const url = `/d/${docId}/export.${format}?inline=1&base_version=${coordinator.version}`
  try {
    const res = await fetch(url)
    if (!res.ok) {
      if (res.status === 409) {
        coordinator.change(snapshot())
        coordinator.holdConflict()
      }
      throw new Error(`HTTP ${res.status}`)
    }
    if (mode === 'download') {
      const blobUrl = URL.createObjectURL(await res.blob())
      const link = document.createElement('a')
      link.href = blobUrl
      link.download = `${titleEl.value.trim() || 'document'}.${format}`
      document.body.append(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000)
      exportMenu.hidden = true
      return
    }
    const text = await res.text()
    let ok = false
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      ok = fallbackCopy(text)
    }
    flash(el, ok ? 'Copied' : 'Copy failed')
  } catch (err) {
    console.error('export failed', err)
    flash(el, coordinator.state === 'conflict' ? 'Version changed' : 'Export failed')
  }
}

exportMenu.addEventListener('click', (e) => {
  const item = e.target.closest('[data-export]')
  if (!item) return
  e.preventDefault()
  exportDoc(item.dataset.export, item.dataset.mode, item)
})

// --- keyboard shortcuts ------------------------------------------------------

const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform)
if (!IS_MAC) {
  shortcutsDialog.querySelectorAll('[data-mod]').forEach((k) => (k.textContent = 'Ctrl'))
  shortcutsDialog.querySelectorAll('[data-alt]').forEach((k) => (k.textContent = 'Alt'))
}

function isTyping(target) {
  return !!target?.closest?.('input, textarea, select, [contenteditable="true"]')
}
function activeCard() {
  return document.querySelector('.ann-card.is-active')
}
function clickCardAction(labels) {
  const card = activeCard()
  if (!card) return false
  const btn = [...card.querySelectorAll('.ann-actions button')].find((b) => labels.includes(b.textContent.trim()))
  if (!btn) return false
  btn.click()
  return true
}
function acceptActive() {
  return clickCardAction(['Accept', 'Correct', 'Cut it'])
}
function resolveActive() {
  return clickCardAction(['Resolve'])
}
function editActive() {
  return clickCardAction(['Edit'])
}
function dismissActive() {
  const card = activeCard()
  const menu = card?.querySelector('details.dismiss-menu')
  if (!menu) return clickCardAction(['Dismiss'])
  menu.open = true
  requestAnimationFrame(() => menu.querySelector('.reason-menu button')?.focus())
  return true
}
function toggleShortcuts() {
  if (shortcutsDialog.open) shortcutsDialog.close()
  else shortcutsDialog.showModal()
}

document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey
  if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 's') {
    e.preventDefault()
    void save()
    return
  }
  // Work anywhere, including while typing.
  if (e.altKey && !mod && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    e.preventDefault()
    sidebar.step(e.key === 'ArrowDown' ? 1 : -1)
    return
  }
  if (e.altKey && !mod && e.key === 'Enter') {
    e.preventDefault()
    acceptActive()
    return
  }
  if (e.ctrlKey && !e.metaKey && !e.altKey && (e.key === '.' || e.key === ',')) {
    e.preventDefault()
    sidebar.step(e.key === '.' ? 1 : -1)
    return
  }
  if (e.key === 'Escape') {
    if (shortcutsDialog.open) return shortcutsDialog.close()
    if (document.querySelector('details.dismiss-menu[open]')) return closeDismissMenus()
    if (!exportMenu.hidden) return (exportMenu.hidden = true)
    if (!passMenu.hidden) return closeMenu()
    if (!drawer.hidden) return closeDrawer()
    if (sidebar.state.activeId && !e.target.closest('#sidebar form')) sidebar.setActive(null)
    return
  }
  // Arrow keys inside a dismiss-reason menu move between reasons.
  const reasonMenu = e.target.closest?.('.reason-menu')
  if (reasonMenu && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    const items = [...reasonMenu.querySelectorAll('button')]
    const i = items.indexOf(document.activeElement)
    items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus()
    e.preventDefault()
    return
  }
  // Single letters, only when not typing.
  if (isTyping(e.target) || mod || e.altKey || shortcutsDialog.open) return
  switch (e.key) {
    case 'j': sidebar.step(1); break
    case 'k': sidebar.step(-1); break
    case 'a': acceptActive(); break
    case 'r': resolveActive(); break
    case 'x': dismissActive(); break
    case 'e': editActive(); break
    case 'p': document.getElementById('pass-menu-btn')?.click(); break
    case 'h': document.getElementById('history-btn')?.click(); break
    case '?': toggleShortcuts(); break
    default: return
  }
  e.preventDefault()
})

window.Workshop = {
  editor, sidebar, save, annotate, applySuggestion, openDrawer, closeDrawer,
  openMenu, closeMenu, placeFindings, acceptAll, refreshStrip, toggleExport, exportDoc, flushSave,
  toggleShortcuts, acceptActive, resolveActive, dismissActive, editActive,
}
