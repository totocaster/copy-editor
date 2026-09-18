// Floating formatting/annotation toolbar shown over a text selection.

export function createBubbleMenu({ editor, container, onAnnotate }) {
  const el = document.getElementById('bubble-menu')
  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi)

  // Keep the editor selection when clicking a button.
  el.addEventListener('mousedown', (e) => e.preventDefault())
  el.querySelectorAll('[data-cmd]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const cmd = btn.dataset.cmd
      if (cmd === 'annotate') {
        onAnnotate(btn.dataset.kind)
        return
      }
      const chain = editor.chain().focus()
      if (typeof chain[cmd] === 'function') chain[cmd]().run()
      update()
    })
  })

  function hide() {
    el.hidden = true
  }

  function update() {
    const { state, view } = editor
    const { from, to, empty } = state.selection
    if (empty || !editor.isEditable || !view.hasFocus() || state.selection.node) return hide()
    if (!state.doc.textBetween(from, to, ' ').trim()) return hide()

    const start = view.coordsAtPos(from)
    const end = view.coordsAtPos(to, -1)
    const box = container.getBoundingClientRect()
    const sameLine = Math.abs(start.top - end.top) < 4
    const anchorX = sameLine ? (start.left + end.left) / 2 : start.left + 80

    el.hidden = false
    const w = el.offsetWidth
    const h = el.offsetHeight
    el.style.left = `${clamp(anchorX - box.left - w / 2, 0, Math.max(0, container.clientWidth - w))}px`
    el.style.top = `${Math.min(start.top, end.top) - box.top - h - 10}px`

    el.querySelectorAll('[data-mark]').forEach((b) => {
      b.classList.toggle('is-on', editor.isActive(b.dataset.mark))
    })
  }

  editor.on('selectionUpdate', update)
  editor.on('focus', update)
  editor.on('blur', () => setTimeout(() => {
    if (!editor.view.hasFocus()) hide()
  }, 100))
  editor.on('update', () => {
    if (editor.state.selection.empty) hide()
  })

  return { update, hide }
}
