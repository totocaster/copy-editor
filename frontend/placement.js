// Placing AI findings in the live document.
//
// A finding names a paragraph number and a verbatim quote. We try the server's
// anchors first (valid when the text hasn't moved), then the hinted paragraph,
// then a unique match anywhere, then a loose first-words/last-words match.

const NORMALISE = {
  '‘': "'", '’': "'", '‚': "'", '‛': "'",
  '“': '"', '”': '"', '„': '"', '‟': '"',
  '–': '-', '—': '-', '‒': '-', '−': '-',
  ' ': ' ', ' ': ' ', ' ': ' ', '…': '.',
}

export function normalise(s) {
  let out = ''
  for (const ch of s) out += NORMALISE[ch] || ch
  return out
}

/** Text blocks in document order, numbered the way the server numbers them. */
export function textblocks(doc) {
  const out = []
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return true
    let text = ''
    node.forEach((child) => {
      if (child.isText) text += child.text
      else if (child.type.name === 'hardBreak') text += '\n'
    })
    out.push({ index: out.length + 1, from: pos + 1, text })
    return false
  })
  return out
}

function findIn(block, quote, { unique = false, loose = false } = {}) {
  const hay = loose ? normalise(block.text) : block.text
  const needle = loose ? normalise(quote) : quote
  const i = hay.indexOf(needle)
  if (i < 0) return null
  if (unique && hay.indexOf(needle, i + 1) >= 0) return null
  return [block.from + i, block.from + i + needle.length]
}

export function locate(doc, finding) {
  const quote = (finding.quote || '').trim()
  if (!quote) return null
  const size = doc.content.size

  if (finding.from < finding.to && finding.to <= size) {
    const current = doc.textBetween(finding.from, finding.to, '\n')
    if (normalise(current) === normalise(quote)) return [finding.from, finding.to]
  }

  const blocks = textblocks(doc)
  const hinted = finding.paragraph ? blocks[finding.paragraph - 1] : null
  if (hinted) {
    const r = findIn(hinted, quote) || findIn(hinted, quote, { loose: true })
    if (r) return r
  }
  for (const opts of [{ unique: true }, { unique: true, loose: true }]) {
    const hits = blocks.map((b) => findIn(b, quote, opts)).filter(Boolean)
    if (hits.length === 1) return hits[0]
  }

  const words = normalise(quote).toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length < 2) return null
  const n = Math.min(3, words.length)
  const head = words.slice(0, n).join(' ')
  const tail = words.slice(-n).join(' ')
  const ordered = hinted ? [hinted, ...blocks.filter((b) => b !== hinted)] : blocks
  for (const b of ordered) {
    const t = normalise(b.text).toLowerCase()
    const i = t.indexOf(head)
    if (i < 0) continue
    const j = t.indexOf(tail, i)
    if (j < 0) continue
    const end = j + tail.length
    const len = end - i
    if (len >= quote.length * 0.5 && len <= quote.length * 1.6) return [b.from + i, b.from + end]
  }
  return null
}
