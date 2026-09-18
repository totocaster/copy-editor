// Commentary sidebar.
//
// The panel scrolls on its own. Cards sit in a plain list in document order,
// so a cluster of notes never drifts far below its text. Alignment happens on
// demand: activating from the text scrolls the panel so the card sits level
// with its highlight; activating from a card scrolls the page so the text
// comes to the card. While a pair is active the panel follows page scrolling
// to keep them level, until you scroll the panel yourself. A connector line
// links the active pair; hovering either side draws a fainter one.

import { annotationRanges } from './annotation.js'
import { createConnectors } from './connectors.js'

export function createSidebar({ editor, docId, editorEl }) {
  const panel = document.getElementById('sidebar')
  const connectors = createConnectors({
    svg: document.getElementById('connectors'),
    panel,
    barSelector: '#sidebar-bar',
  })
  const state = { activeId: null, order: [], filter: 'all', lockedId: null, hoverId: null }
  const markType = editor.schema.marks.annotation
  let layoutRaf = 0
  let drawRaf = 0
  let pendingPanelScrolls = 0
  let suppressFollowUntil = 0

  const layer = () => document.getElementById('annotation-layer')
  const marksFor = (id) => editorEl.querySelectorAll(`mark[data-annotation-id="${CSS.escape(id)}"]`)
  const cardFor = (id) => document.getElementById(`ann-${id}`)
  const markPos = (id) => annotationRanges(editor.state.doc, markType, id)[0]?.from ?? Infinity

  // --- filters --------------------------------------------------------------

  function matchesFilter(card) {
    const f = state.filter
    if (f === 'all') return true
    if (f === 'you') return card.dataset.author !== 'ai'
    if (f.startsWith('run:')) return card.dataset.run === f.slice(4)
    return true
  }

  function setFilter(filter) {
    state.filter = filter || 'all'
    applyFilterChips()
    if (state.activeId) {
      const card = cardFor(state.activeId)
      if (card && !matchesFilter(card)) state.activeId = null
    }
    setActive(state.activeId)
  }

  function applyFilterChips() {
    const chips = document.querySelectorAll('#ann-filters [data-filter]')
    let found = false
    chips.forEach((chip) => {
      const on = chip.dataset.filter === state.filter
      chip.classList.toggle('is-on', on)
      found = found || on
    })
    if (!found && chips.length) {
      state.filter = 'all'
      chips.forEach((chip) => chip.classList.toggle('is-on', chip.dataset.filter === 'all'))
    }
  }

  // --- layout: visibility + document order ----------------------------------

  function layout() {
    const host = layer()
    if (!host) return
    const cards = [...host.querySelectorAll('.ann-card.is-positioned')]
    const items = []
    for (const card of cards) {
      const id = card.dataset.card
      const visible = marksFor(id).length > 0 && matchesFilter(card)
      card.hidden = !visible
      if (visible) items.push({ id, card, pos: markPos(id) })
    }
    items.sort((a, b) => a.pos - b.pos)
    const visibleNow = cards.filter((c) => !c.hidden)
    const misordered = items.some((it, i) => visibleNow[i] !== it.card)
    if (misordered && !host.contains(document.activeElement)) {
      for (const it of items) host.appendChild(it.card)
    }
    state.order = items.map((it) => it.id)
    updateCounter()
    scheduleDraw()
  }

  function scheduleLayout() {
    cancelAnimationFrame(layoutRaf)
    layoutRaf = requestAnimationFrame(layout)
  }

  function updateCounter() {
    const n = state.order.length
    const idx = state.order.indexOf(state.activeId)
    const counter = document.getElementById('ann-counter')
    if (counter) counter.textContent = n ? `${idx >= 0 ? idx + 1 : '–'} / ${n}` : '0'
    for (const id of ['ann-prev', 'ann-next']) {
      const btn = document.getElementById(id)
      if (btn) btn.disabled = n === 0
    }
  }

  // --- alignment ------------------------------------------------------------

  function panelMinTop() {
    const bar = document.getElementById('sidebar-bar')
    const top = bar && !bar.hidden ? bar.getBoundingClientRect().bottom : panel.getBoundingClientRect().top
    return top + 8
  }

  function panelScrollTo(top) {
    const max = panel.scrollHeight - panel.clientHeight
    const next = Math.max(0, Math.min(top, max))
    if (Math.abs(next - panel.scrollTop) < 0.5) return
    pendingPanelScrolls++
    panel.scrollTop = next
  }

  const content = () => document.getElementById('sidebar-content')
  const currentPad = () => parseFloat(content()?.style.paddingTop) || 0
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')

  function setListOffset(px) {
    const el = content()
    if (!el) return
    if (Math.abs(currentPad() - px) > 0.5) el.style.paddingTop = px > 0.5 ? `${Math.round(px)}px` : ''
  }

  /**
   * Where the panel must scroll, and how much the list must be padded, for the
   * card to sit level with a highlight whose (final) viewport top is `markTop`.
   */
  function panelTargetsFor(card, markTop) {
    const want = Math.max(markTop, panelMinTop())
    const base = card.getBoundingClientRect().top - currentPad() + panel.scrollTop
    let pad = 0
    let scroll = base - want
    if (scroll < 0) {
      pad = -scroll
      scroll = 0
    }
    return { scroll, pad }
  }

  let anim = null

  function cancelAnim() {
    if (anim) cancelAnimationFrame(anim.raf)
    anim = null
    suppressFollowUntil = 0
  }

  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

  /**
   * Move the card by `delta` px upwards (negative: downwards) using panel scroll
   * first and the list offset only when the panel cannot scroll any further.
   */
  function nudgeCard(delta) {
    if (Math.abs(delta) < 0.25) return
    let pad = currentPad()
    if (delta > 0) {
      const fromPad = Math.min(pad, delta)
      pad -= fromPad
      delta -= fromPad
      setListOffset(pad)
      if (delta > 0) panelScrollTo(panel.scrollTop + delta)
    } else {
      let down = -delta
      const fromScroll = Math.min(panel.scrollTop, down)
      if (fromScroll > 0) panelScrollTo(panel.scrollTop - fromScroll)
      down -= fromScroll
      if (down > 0) setListOffset(pad + down)
    }
  }

  /**
   * Bring the pair level on one eased timeline. The page glides to `pageY`
   * (default: stays put) while the panel is steered every frame so the gap
   * between card and highlight closes along the same curve. Measuring each
   * frame means unfolding cards, rounding and clamping are all absorbed, and
   * the motion ends exactly level with nothing left to correct.
   */
  function bringLevel(id, { pageY = window.scrollY } = {}) {
    cancelAnim()
    if (!marksFor(id).length || !cardFor(id) || cardFor(id).hidden) return
    const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight)
    const startY = window.scrollY
    const endY = Math.min(Math.max(pageY, 0), maxY)
    const dY = endY - startY
    // Re-query every frame: the editor re-renders highlight elements when their class changes,
    // and the sidebar may swap cards, so references captured up front go stale.
    const gapNow = () => {
      const mark = marksFor(id)[0]
      const card = cardFor(id)
      if (!mark || !card) return null
      const m = mark.getBoundingClientRect()
      if (!m.width && !m.height) return null
      return card.getBoundingClientRect().top - Math.max(m.top, panelMinTop())
    }
    const initial = gapNow()
    if (initial === null) return
    // The measured gap already reflects the page's progress each frame, so the panel's job is
    // simply to shrink the initial gap along the curve; the page's share arrives on its own.
    const distance = Math.max(Math.abs(dY), Math.abs(initial + dY))
    const finish = () => {
      anim = null
      suppressFollowUntil = 0
      const g = gapNow()
      if (g !== null) nudgeCard(g)
      draw()
      setTimeout(() => { pendingPanelScrolls = 0 }, 150)
    }
    if (reducedMotion.matches || distance < 1) {
      if (Math.abs(dY) >= 1) window.scrollTo(0, endY)
      finish()
      return
    }
    const duration = Math.min(650, 280 + distance * 0.3)
    const t0 = performance.now()
    suppressFollowUntil = Infinity
    const frame = (now) => {
      const t = Math.min(1, (now - t0) / duration)
      const e = ease(t)
      if (dY) window.scrollTo(0, startY + dY * e)
      const g = gapNow()
      if (g === null) return finish() // highlight vanished mid-flight
      nudgeCard(g - initial * (1 - e))
      draw()
      if (t < 1) anim.raf = requestAnimationFrame(frame)
      else finish()
    }
    anim = { raf: requestAnimationFrame(frame) }
  }

  /** Page target for a step: leave the page alone unless the highlight is outside the reading band. */
  function pageTargetFor(mark) {
    const r = mark.getBoundingClientRect()
    const headerBottom = document.querySelector('.topbar')?.getBoundingClientRect().bottom ?? 0
    const bandTop = headerBottom + 72
    const bandBottom = window.innerHeight - 120
    if (r.top >= bandTop && r.bottom <= bandBottom) return window.scrollY
    return window.scrollY + r.top - window.innerHeight * 0.38
  }

  /**
   * Instantly bring the card level with its highlight (used for lockstep
   * following and final corrections). Scrolling the panel can only move the
   * list up, so when the card must come down we pad the top of the list.
   */
  function alignPanelTo(id) {
    const mark = marksFor(id)[0]
    const card = cardFor(id)
    if (!mark || !card || card.hidden || !content()) return
    const { scroll, pad } = panelTargetsFor(card, mark.getBoundingClientRect().top)
    setListOffset(pad)
    panelScrollTo(scroll)
  }

  // --- activation -----------------------------------------------------------

  function setActive(id, { from = 'code' } = {}) {
    state.activeId = id
    editorEl.querySelectorAll('mark.annotation.is-active').forEach((m) => m.classList.remove('is-active'))
    document.querySelectorAll('.ann-card.is-active').forEach((c) => {
      c.classList.remove('is-active')
      c.querySelectorAll('.ann-reveal.is-open').forEach((r) => r.classList.remove('is-open'))
    })
    if (id) {
      marksFor(id).forEach((m) => m.classList.add('is-active'))
      cardFor(id)?.classList.add('is-active')
      state.lockedId = id
      const mark = marksFor(id)[0]
      const card = cardFor(id)
      if (from === 'card' && mark && card) {
        // Bring the text to the card.
        bringLevel(id, { pageY: window.scrollY + mark.getBoundingClientRect().top - card.getBoundingClientRect().top })
      } else if (from === 'step' && mark) {
        // Scroll the text only if the highlight is out of the reading band; otherwise just the list moves.
        bringLevel(id, { pageY: pageTargetFor(mark) })
      } else if (from === 'text') {
        bringLevel(id)
      } else {
        alignPanelTo(id)
      }
    } else {
      cancelAnim()
      state.lockedId = null
      setListOffset(0)
    }
    layout()
  }

  function step(delta) {
    const { order } = state
    if (!order.length) return
    let idx = order.indexOf(state.activeId)
    if (idx < 0) idx = delta > 0 ? 0 : order.length - 1
    else idx = (idx + delta + order.length) % order.length
    setActive(order[idx], { from: 'step' })
  }

  async function refresh({ activeId, editing } = {}) {
    const params = new URLSearchParams()
    if (editing) params.set('editing', editing)
    const qs = params.toString()
    await htmx.ajax('GET', `/d/${docId}/annotations${qs ? `?${qs}` : ''}`, {
      target: '#sidebar-content',
      swap: 'innerHTML',
    })
    if (activeId !== undefined) state.activeId = activeId
    setActive(state.activeId)
    focusEditor()
  }

  function focusEditor() {
    const field = document.querySelector('#sidebar [data-autofocus]')
    if (field) {
      field.focus({ preventScroll: true })
      if (typeof field.setSelectionRange === 'function') {
        const n = field.value.length
        field.setSelectionRange(n, n)
      }
    }
  }

  // --- connectors -----------------------------------------------------------

  function colorOf(card) {
    const k = getComputedStyle(card).getPropertyValue('--k').trim()
    return k ? `rgb(${k})` : 'rgb(120 113 108)'
  }

  function draw() {
    const pairs = []
    const add = (id, emphasis) => {
      const mark = marksFor(id)[0]
      const card = cardFor(id)
      if (!mark || !card || card.hidden) return
      pairs.push({ mark, card, color: colorOf(card), emphasis })
    }
    if (state.hoverId && state.hoverId !== state.activeId) add(state.hoverId, false)
    if (state.activeId) add(state.activeId, true)
    connectors.draw(pairs)
  }

  function scheduleDraw() {
    cancelAnimationFrame(drawRaf)
    drawRaf = requestAnimationFrame(draw)
  }

  function setHover(id) {
    if (id === state.hoverId) return
    if (state.hoverId) marksFor(state.hoverId).forEach((m) => m.classList.remove('is-hover'))
    state.hoverId = id
    if (id) marksFor(id).forEach((m) => m.classList.add('is-hover'))
    scheduleDraw()
  }

  // --- wiring -------------------------------------------------------------

  editorEl.addEventListener('click', (e) => {
    const mark = e.target.closest('mark[data-annotation-id]')
    if (mark && editor.state.selection.empty) setActive(mark.dataset.annotationId, { from: 'text' })
  })

  document.addEventListener('click', (e) => {
    if (e.target.closest('#ann-prev')) return step(-1)
    if (e.target.closest('#ann-next')) return step(1)
    const chip = e.target.closest('#ann-filters [data-filter]')
    if (chip) {
      setFilter(chip.dataset.filter)
      return
    }
    const card = e.target.closest('.ann-card')
    if (!card || !panel.contains(card)) return
    if (e.target.closest('button, a, textarea, input, select, label, summary')) return
    if (card.dataset.card !== state.activeId) setActive(card.dataset.card, { from: 'card' })
  })

  document.addEventListener('mouseover', (e) => {
    if (!e.target.closest) return
    const card = e.target.closest('.ann-card.is-positioned')
    const mark = e.target.closest('mark[data-annotation-id]')
    setHover(card ? card.dataset.card : mark ? mark.dataset.annotationId : null)
  })

  document.body.addEventListener('htmx:afterSettle', (e) => {
    if (!e.target.closest || !e.target.closest('#sidebar')) return
    applyFilterChips()
    setActive(state.activeId)
    focusEditor()
  })

  window.addEventListener('scroll', () => {
    if (anim) return
    if (state.lockedId && performance.now() > suppressFollowUntil) alignPanelTo(state.lockedId)
    scheduleDraw()
  }, { passive: true })

  panel.addEventListener('scroll', () => {
    if (pendingPanelScrolls > 0) pendingPanelScrolls--
    else if (!anim) state.lockedId = null // the reader took over the panel
    scheduleDraw()
  }, { passive: true })
  panel.addEventListener('wheel', () => {
    cancelAnim()
    pendingPanelScrolls = 0
    state.lockedId = null // the reader took over the panel
  }, { passive: true })
  window.addEventListener('wheel', (e) => {
    if (anim && !panel.contains(e.target)) cancelAnim()
  }, { passive: true })

  // Once a card has unfolded, let its content overflow so menus inside can open.
  document.addEventListener('transitionend', (e) => {
    if (e.propertyName !== 'grid-template-rows') return
    const reveal = e.target.closest?.('.ann-reveal')
    if (reveal && reveal.closest('.ann-card')?.classList.contains('is-active')) reveal.classList.add('is-open')
  })

  window.addEventListener('resize', scheduleLayout)
  document.fonts?.ready.then(scheduleLayout)
  editor.on('update', scheduleLayout)

  return { state, layout, scheduleLayout, setActive, step, refresh, setFilter }
}
