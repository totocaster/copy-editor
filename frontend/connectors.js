// Connector lines between a highlight in the text and its card in the sidebar.
//
// Drawn in a fixed, full-viewport SVG so both endpoints can be taken straight
// from getBoundingClientRect(). A level pair gets a straight line; otherwise a
// gentle S-curve. When the card is scrolled out of the panel the line ends at
// the panel's edge, clamped to the visible range, so it still points the way.

const NS = 'http://www.w3.org/2000/svg'

export function createConnectors({ svg, panel, barSelector }) {
  function clear() {
    while (svg.firstChild) svg.removeChild(svg.firstChild)
  }

  function el(name, attrs) {
    const node = document.createElementNS(NS, name)
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v))
    return node
  }

  function draw(pairs) {
    clear()
    if (!pairs.length) return
    const vw = window.innerWidth
    const vh = window.innerHeight
    svg.setAttribute('viewBox', `0 0 ${vw} ${vh}`)
    svg.setAttribute('width', vw)
    svg.setAttribute('height', vh)

    const headerBottom = document.querySelector('.topbar')?.getBoundingClientRect().bottom ?? 0
    const panelRect = panel.getBoundingClientRect()
    const bar = panel.querySelector(barSelector)
    const topLimit = (bar && !bar.hidden ? bar.getBoundingClientRect().bottom : panelRect.top) + 6
    const bottomLimit = panelRect.bottom - 6

    for (const { mark, card, color, emphasis } of pairs) {
      const rects = mark.getClientRects()
      const m = rects.length ? rects[0] : mark.getBoundingClientRect()
      if (!m.width && !m.height) continue
      if (m.bottom < headerBottom || m.top > vh) continue

      const c = card.getBoundingClientRect()
      const x1 = m.right + 3
      const y1 = m.top + m.height / 2
      const ideal = c.top + 15
      const clipped = ideal < topLimit || ideal > bottomLimit
      const y2 = Math.min(Math.max(ideal, topLimit), bottomLimit)
      const x2 = (clipped ? panelRect.left : c.left) - 3
      if (x2 <= x1) continue

      const xm = (x1 + x2) / 2
      const d = Math.abs(y1 - y2) < 1.5
        ? `M${x1} ${y1} L${x2} ${y2}`
        : `M${x1} ${y1} C${xm} ${y1}, ${xm} ${y2}, ${x2} ${y2}`
      const path = el('path', {
        d, fill: 'none', stroke: color,
        'stroke-width': emphasis ? 1.5 : 1.25,
        opacity: emphasis ? 0.9 : 0.5,
      })
      if (!emphasis) path.setAttribute('stroke-dasharray', '3 3')
      svg.appendChild(path)
      svg.appendChild(el('circle', { cx: x1, cy: y1, r: 2.5, fill: color, opacity: emphasis ? 0.95 : 0.6 }))
      if (clipped) {
        const dir = ideal < topLimit ? -1 : 1
        svg.appendChild(el('path', {
          d: `M${x2 - 4} ${y2 + 4 * dir * -1} L${x2} ${y2 + 3 * dir} L${x2 + 4} ${y2 + 4 * dir * -1}`,
          fill: 'none', stroke: color, 'stroke-width': 1.5, opacity: 0.9,
        }))
      }
    }
  }

  return { draw, clear }
}
