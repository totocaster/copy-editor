// Annotation mark: a highlight tied to a sidebar card by id.
//
// The mark lives in the document JSON, so highlights move with the text as it
// is edited and survive revision snapshots. The server derives anchors from it.

import { Mark, mergeAttributes } from '@tiptap/core'

/** Contiguous ranges in `doc` carrying the annotation mark with `id`. */
export function annotationRanges(doc, markType, id) {
  const ranges = []
  let current = null
  doc.descendants((node, pos) => {
    if (!node.isText) {
      current = null
      return
    }
    const mark = node.marks.find((m) => m.type === markType && m.attrs.id === id)
    if (!mark) {
      current = null
      return
    }
    const end = pos + node.nodeSize
    if (current && current.to === pos) {
      current.to = end
    } else {
      current = { from: pos, to: end, mark }
      ranges.push(current)
    }
  })
  return ranges
}

export const Annotation = Mark.create({
  name: 'annotation',
  inclusive: false, // typing at the edge of a highlight does not extend it
  excludes: '', // highlights may overlap one another and other marks

  addAttributes() {
    return {
      id: {
        default: null,
        parseHTML: (el) => el.getAttribute('data-annotation-id'),
        renderHTML: (attrs) => (attrs.id ? { 'data-annotation-id': attrs.id } : {}),
      },
      kind: {
        default: 'note',
        parseHTML: (el) => el.getAttribute('data-kind') || 'note',
        renderHTML: (attrs) => ({ 'data-kind': attrs.kind || 'note' }),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'mark[data-annotation-id]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['mark', mergeAttributes({ class: 'annotation' }, HTMLAttributes), 0]
  },

  addCommands() {
    return {
      setAnnotation:
        (attrs) =>
        ({ commands }) =>
          commands.setMark(this.name, attrs),

      removeAnnotation:
        (id) =>
        ({ tr, state, dispatch }) => {
          const ranges = annotationRanges(state.doc, this.type, id)
          if (!ranges.length) return false
          if (dispatch) for (const r of ranges) tr.removeMark(r.from, r.to, r.mark)
          return true
        },

      setAnnotationKind:
        (id, kind) =>
        ({ tr, state, dispatch }) => {
          const ranges = annotationRanges(state.doc, this.type, id)
          if (!ranges.length) return false
          if (dispatch) {
            const fresh = this.type.create({ id, kind })
            for (const r of ranges) {
              tr.removeMark(r.from, r.to, r.mark)
              tr.addMark(r.from, r.to, fresh)
            }
          }
          return true
        },

      /** Replace the highlighted text (accepting a suggestion), or delete it when text is empty. */
      replaceAnnotationText:
        (id, text) =>
        ({ tr, state, dispatch }) => {
          const ranges = annotationRanges(state.doc, this.type, id)
          if (!ranges.length) return false
          if (dispatch) {
            // Walk backwards so earlier positions stay valid.
            for (let i = ranges.length - 1; i >= 0; i--) {
              const r = ranges[i]
              if (i === 0 && text) {
                const first = state.doc.nodeAt(r.from)
                const marks = (first?.marks || []).filter((m) => m.type !== this.type)
                tr.replaceWith(r.from, r.to, state.schema.text(text, marks))
              } else {
                tr.delete(r.from, r.to)
              }
            }
          }
          return true
        },

      /** Add a highlight over an explicit range (used to place AI findings). */
      addAnnotationAt:
        (from, to, attrs) =>
        ({ tr, state, dispatch }) => {
          if (!(from < to) || to > state.doc.content.size) return false
          if (dispatch) tr.addMark(from, to, this.type.create(attrs))
          return true
        },

      /** Drop any highlight whose annotation is no longer open. Not undoable. */
      pruneAnnotations:
        (keepIds) =>
        ({ tr, state, dispatch }) => {
          const keep = new Set(keepIds)
          let changed = false
          state.doc.descendants((node, pos) => {
            if (!node.isText) return
            for (const m of node.marks) {
              if (m.type === this.type && !keep.has(m.attrs.id)) {
                if (dispatch) tr.removeMark(pos, pos + node.nodeSize, m)
                changed = true
              }
            }
          })
          if (changed && dispatch) tr.setMeta('addToHistory', false)
          return changed
        },
    }
  },
})
