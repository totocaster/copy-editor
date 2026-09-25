// The editor and title share one ordered, versioned save stream.
export function createSaveCoordinator({ snapshot, version, request, persist, clear, onState = () => {}, onSaved = () => {}, delay = 900 }) {
  let current = snapshot
  let baseVersion = version
  let sequence = 0
  let savedSequence = 0
  let timer = null
  let inFlight = null
  let problem = null
  let preserveBefore = false

  const dirty = () => sequence !== savedSequence
  const state = () => problem || (inFlight ? 'saving' : dirty() ? 'dirty' : 'saved')
  const notify = () => onState(state())
  const cancelTimer = () => { if (timer !== null) clearTimeout(timer); timer = null }

  function change(next) {
    current = next
    sequence++
    // This is synchronous so pagehide and an in-flight request cannot erase newer edits.
    try {
      persist(current, baseVersion, problem === 'conflict' || preserveBefore)
      if (problem === 'backup-error') problem = null
    } catch (error) { problem = 'backup-error'; onState(problem, error) }
    if (problem !== 'conflict') {
      if (problem !== 'backup-error') problem = null
      cancelTimer()
      timer = setTimeout(() => { void flush() }, delay)
    }
    notify()
  }

  async function flush() {
    cancelTimer()
    if (problem === 'conflict') { notify(); return false }
    while (dirty()) {
      cancelTimer()
      if (inFlight) {
        await inFlight
        if (problem === 'conflict' || problem === 'error') return false
        continue
      }
      const sent = current
      const sentSequence = sequence
      const sentVersion = baseVersion
      problem = null
      const operation = (async () => {
        let result
        try {
          result = await request({ ...sent, base_version: sentVersion, ...(preserveBefore ? { preserve_before: true } : {}) })
        } catch (error) {
          problem = error?.status === 409 ? 'conflict' : 'error'
          onState(problem, error)
          return false
        }
        baseVersion = result.version
        preserveBefore = false
        savedSequence = sentSequence
        onSaved(result)
        try {
          if (dirty()) persist(current, baseVersion, false)
          else clear()
        } catch (error) {
          if (dirty()) { problem = 'backup-error'; onState(problem, error) }
          // The server has confirmed a clean save; a failed local cleanup cannot undo it.
        }
        return true
      })()
      inFlight = operation
      notify()
      const ok = await operation
      inFlight = null
      notify()
      if (!ok) return false
    }
    return true
  }

  function rebase(version, { preserveBefore: preserve = false } = {}) {
    if (!Number.isInteger(version)) throw new Error('Missing server version')
    baseVersion = version
    preserveBefore = preserve
    problem = null
    if (dirty()) {
      try { persist(current, baseVersion, preserve) } catch (error) { onState('backup-error', error) }
    }
    notify()
  }

  function holdConflict() {
    cancelTimer()
    problem = 'conflict'
    if (dirty()) {
      try { persist(current, baseVersion, true) } catch (error) { onState('backup-error', error) }
    }
    notify()
  }

  function adopt(snapshot, version) {
    if (!Number.isInteger(version)) throw new Error('Missing server version')
    cancelTimer()
    current = snapshot
    baseVersion = version
    savedSequence = ++sequence
    problem = null
    try { clear() } catch (error) { onState('backup-error', error) }
    notify()
  }

  notify()
  return { change, flush, rebase, holdConflict, adopt, get dirty() { return dirty() }, get state() { return state() }, get version() { return baseVersion }, get requiresReview() { return problem === 'conflict' || preserveBefore }, get snapshot() { return current } }
}
