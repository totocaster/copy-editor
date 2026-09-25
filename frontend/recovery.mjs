const prefix = (docId) => `copy-editor:draft:${docId}:`

export function createDraftStore(storage, docId, id) {
  const key = prefix(docId) + id
  return {
    key,
    write(snapshot, version, requiresReview = false) {
      storage.setItem(key, JSON.stringify({ snapshot, version, requiresReview, updatedAt: Date.now() }))
    },
    clear() { storage.removeItem(key) },
    list() {
      const drafts = []
      for (let i = 0; i < storage.length; i++) {
        const itemKey = storage.key(i)
        if (!itemKey?.startsWith(prefix(docId))) continue
        const raw = storage.getItem(itemKey)
        try {
          const item = JSON.parse(raw)
          if (item?.snapshot?.content?.type === 'doc' && typeof item.snapshot.title === 'string' && Number.isInteger(item.version)) {
            drafts.push({ ...item, key: itemKey, raw })
          }
        } catch { /* Leave an unreadable draft untouched. */ }
      }
      return drafts.sort((a, b) => b.updatedAt - a.updatedAt)
    },
    discard(draft) {
      // A live tab may have updated this draft after the recovery panel opened.
      if (storage.getItem(draft.key) !== draft.raw) return false
      storage.removeItem(draft.key)
      return true
    },
  }
}
