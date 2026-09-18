// Notion-style "/" block menu built on @tiptap/suggestion.

import { Extension } from '@tiptap/core'
import Suggestion from '@tiptap/suggestion'

const ITEMS = [
  { title: 'Text', hint: 'Plain paragraph', keywords: 'paragraph body',
    run: (e, r) => e.chain().focus().deleteRange(r).setParagraph().run() },
  { title: 'Heading 1', hint: 'Section title', keywords: 'h1 title',
    run: (e, r) => e.chain().focus().deleteRange(r).setNode('heading', { level: 1 }).run() },
  { title: 'Heading 2', hint: 'Subsection', keywords: 'h2 subtitle',
    run: (e, r) => e.chain().focus().deleteRange(r).setNode('heading', { level: 2 }).run() },
  { title: 'Heading 3', hint: 'Minor heading', keywords: 'h3',
    run: (e, r) => e.chain().focus().deleteRange(r).setNode('heading', { level: 3 }).run() },
  { title: 'Bulleted list', hint: 'Unordered list', keywords: 'ul bullet list',
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBulletList().run() },
  { title: 'Numbered list', hint: 'Ordered list', keywords: 'ol numbered list',
    run: (e, r) => e.chain().focus().deleteRange(r).toggleOrderedList().run() },
  { title: 'Quote', hint: 'Block quotation', keywords: 'blockquote quote',
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBlockquote().run() },
  { title: 'Code block', hint: 'Preformatted text', keywords: 'code pre',
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCodeBlock().run() },
  { title: 'Divider', hint: 'Horizontal rule', keywords: 'hr rule separator break',
    run: (e, r) => e.chain().focus().deleteRange(r).setHorizontalRule().run() },
]

function filterItems(query) {
  const q = query.trim().toLowerCase()
  if (!q) return ITEMS
  return ITEMS.filter((it) => `${it.title} ${it.keywords}`.toLowerCase().includes(q))
}

export function SlashMenu({ container }) {
  return Extension.create({
    name: 'slashMenu',
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          char: '/',
          allowSpaces: false,
          startOfLine: false,
          items: ({ query }) => filterItems(query),
          command: ({ editor, range, props }) => props.run(editor, range),
          render: () => {
            const el = document.getElementById('slash-menu')
            let items = []
            let index = 0
            let props = null

            const draw = () => {
              if (!items.length) {
                el.innerHTML = '<div class="slash-empty">No matching block</div>'
                return
              }
              el.innerHTML = items
                .map(
                  (it, i) =>
                    `<button type="button" class="slash-item${i === index ? ' is-selected' : ''}" data-i="${i}">` +
                    `${it.title}<span>${it.hint}</span></button>`,
                )
                .join('')
              el.querySelector('.is-selected')?.scrollIntoView({ block: 'nearest' })
            }

            const place = (p) => {
              const rect = p.clientRect?.()
              if (!rect) return
              const box = container.getBoundingClientRect()
              el.style.left = `${Math.max(0, rect.left - box.left)}px`
              el.style.top = `${rect.bottom - box.top + 6}px`
            }

            const choose = (i) => {
              if (items[i]) props.command(items[i])
            }

            return {
              onStart: (p) => {
                props = p
                items = p.items
                index = 0
                el.hidden = false
                el.onmousedown = (ev) => ev.preventDefault()
                el.onclick = (ev) => {
                  const b = ev.target.closest('[data-i]')
                  if (b) choose(Number(b.dataset.i))
                }
                draw()
                place(p)
              },
              onUpdate: (p) => {
                props = p
                items = p.items
                index = Math.min(index, Math.max(items.length - 1, 0))
                draw()
                place(p)
              },
              onKeyDown: ({ event }) => {
                if (event.key === 'ArrowDown') {
                  index = items.length ? (index + 1) % items.length : 0
                  draw()
                  return true
                }
                if (event.key === 'ArrowUp') {
                  index = items.length ? (index - 1 + items.length) % items.length : 0
                  draw()
                  return true
                }
                if (event.key === 'Enter' || event.key === 'Tab') {
                  choose(index)
                  return true
                }
                if (event.key === 'Escape') {
                  el.hidden = true
                  return true
                }
                return false
              },
              onExit: () => {
                el.hidden = true
                el.innerHTML = ''
                el.onclick = null
                el.onmousedown = null
              },
            }
          },
        }),
      ]
    },
  })
}
