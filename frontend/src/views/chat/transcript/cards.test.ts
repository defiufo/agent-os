import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  CARDS_ARTIFACT_MIME,
  MAX_CARDS,
  createCardsMounter,
  isCardsArtifact,
  monogram,
  normalizeCardsPayload,
  renderCards,
  safeLogoUrl,
} from './cards'

function host(): HTMLElement {
  const node = document.createElement('div')
  node.className = 'msg-artifact-cards'
  node.innerHTML = `
    <div class="msg-artifact-cards__header"><span class="msg-artifact-cards__name">lookup.json</span></div>
    <div class="msg-artifact-cards__grid"></div>
    <p class="msg-artifact-cards__overflow" hidden></p>
    <p class="msg-artifact-cards__status">Loading results…</p>`
  document.body.append(node)
  return node
}

const AAPL = {
  title: 'AAPL',
  subtitle: 'Apple',
  badge: 'verified',
  badgeTone: 'positive',
  logo: 'https://assets.example/aapl.png',
  fields: [
    { label: 'Address', value: '0xaf3d76f1834a1d425780943c99ea8a608f8a93f9', copyable: true },
  ],
}

beforeEach(() => {
  document.body.replaceChildren()
})

describe('isCardsArtifact', () => {
  it('matches the cards mime, with or without parameters', () => {
    expect(isCardsArtifact({ mime: CARDS_ARTIFACT_MIME })).toBe(true)
    expect(isCardsArtifact({ mime: `${CARDS_ARTIFACT_MIME}; charset=utf-8` })).toBe(true)
    expect(isCardsArtifact({ mime: 'APPLICATION/VND.AGENTOS.CARDS+JSON' })).toBe(true)
  })

  it('ignores every other artifact', () => {
    expect(isCardsArtifact({ mime: 'application/json' })).toBe(false)
    expect(isCardsArtifact({ mime: 'application/vnd.agentos.chart+json' })).toBe(false)
    expect(isCardsArtifact(null)).toBe(false)
    expect(isCardsArtifact({})).toBe(false)
  })
})

describe('safeLogoUrl', () => {
  it('keeps http(s) urls', () => {
    expect(safeLogoUrl('https://a.example/x.png')).toBe('https://a.example/x.png')
    expect(safeLogoUrl('http://a.example/x.png')).toBe('http://a.example/x.png')
  })

  it('drops anything that could execute or inline', () => {
    // A payload field becomes an <img src>, so these must never survive.
    expect(safeLogoUrl('javascript:alert(1)')).toBe('')
    expect(safeLogoUrl('data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=')).toBe('')
    expect(safeLogoUrl('not a url')).toBe('')
    expect(safeLogoUrl(null)).toBe('')
  })
})

describe('monogram', () => {
  it('takes up to two leading alphanumerics, uppercased', () => {
    expect(monogram('AAPL')).toBe('AA')
    expect(monogram('gme')).toBe('GM')
    expect(monogram('X')).toBe('X')
  })

  it('skips punctuation and copes with an empty title', () => {
    expect(monogram('e.l.f. Beauty')).toBe('EL')
    expect(monogram('...')).toBe('')
    expect(monogram('')).toBe('')
  })
})

describe('normalizeCardsPayload', () => {
  it('normalizes a well-formed payload', () => {
    const payload = normalizeCardsPayload({ title: 'Results', cards: [AAPL] })
    expect(payload).not.toBeNull()
    expect(payload!.title).toBe('Results')
    expect(payload!.cards).toHaveLength(1)
    expect(payload!.cards[0]!.badgeTone).toBe('positive')
    expect(payload!.cards[0]!.fields[0]!.copyable).toBe(true)
    expect(payload!.overflow).toBe(0)
  })

  it('returns null when there is nothing to draw', () => {
    expect(normalizeCardsPayload(null)).toBeNull()
    expect(normalizeCardsPayload({})).toBeNull()
    expect(normalizeCardsPayload({ cards: 'nope' })).toBeNull()
    expect(normalizeCardsPayload({ cards: [] })).toBeNull()
    // A card with no title has nothing to identify it by.
    expect(normalizeCardsPayload({ cards: [{ subtitle: 'x' }] })).toBeNull()
  })

  it('drops fields with no value but keeps label-less ones', () => {
    const payload = normalizeCardsPayload({
      cards: [{ title: 'X', fields: [{ label: 'A', value: '' }, { value: 'bare' }] }],
    })
    expect(payload!.cards[0]!.fields).toEqual([{ label: '', value: 'bare' }])
  })

  it('falls back to a neutral tone for an unknown badge tone', () => {
    const payload = normalizeCardsPayload({ cards: [{ title: 'X', badgeTone: 'chartreuse' }] })
    expect(payload!.cards[0]!.badgeTone).toBe('neutral')
  })

  it('caps the grid and reports the overflow rather than truncating silently', () => {
    const cards = Array.from({ length: MAX_CARDS + 7 }, (_v, i) => ({ title: `T${i}` }))
    const payload = normalizeCardsPayload({ cards })
    expect(payload!.cards).toHaveLength(MAX_CARDS)
    expect(payload!.overflow).toBe(7)
  })
})

describe('renderCards', () => {
  it('builds one card per entry with badge, subtitle and fields', () => {
    const node = host()
    renderCards(node, normalizeCardsPayload({ title: 'Lookup', cards: [AAPL] })!, () => {})

    expect(node.querySelectorAll('.msg-artifact-cards__card')).toHaveLength(1)
    expect(node.querySelector('.msg-artifact-cards__title')!.textContent).toBe('AAPL')
    expect(node.querySelector('.msg-artifact-cards__subtitle')!.textContent).toBe('Apple')
    const badge = node.querySelector<HTMLElement>('.msg-artifact-cards__badge')!
    expect(badge.textContent).toBe('verified')
    expect(badge.dataset.tone).toBe('positive')
    expect(node.querySelector('.msg-artifact-cards__field-value')!.textContent).toBe(
      '0xaf3d76f1834a1d425780943c99ea8a608f8a93f9',
    )
    // The payload title wins over the artifact filename.
    expect(node.querySelector('.msg-artifact-cards__name')!.textContent).toBe('Lookup')
  })

  it('shows a ticker monogram, because the CSP blocks token-list logo CDNs', () => {
    const node = host()
    renderCards(node, normalizeCardsPayload({ cards: [AAPL] })!, () => {})

    const mark = node.querySelector<HTMLElement>('.msg-artifact-cards__mark')!
    expect(mark.textContent).toBe('AA')
    expect(mark.getAttribute('aria-hidden')).toBe('true')
    // The img is still attached; it only replaces the monogram once it loads.
    expect(node.querySelector('.msg-artifact-cards__logo')).not.toBeNull()
  })

  it('keeps the monogram when the logo fails to load', () => {
    const node = host()
    renderCards(node, normalizeCardsPayload({ cards: [AAPL] })!, () => {})

    const img = node.querySelector<HTMLImageElement>('.msg-artifact-cards__logo')!
    img.dispatchEvent(new Event('error'))
    expect(node.querySelector('.msg-artifact-cards__logo')).toBeNull()
    expect(node.querySelector('.msg-artifact-cards__mark')).not.toBeNull()
  })

  it('drops the monogram once a logo actually loads', () => {
    const node = host()
    renderCards(node, normalizeCardsPayload({ cards: [AAPL] })!, () => {})

    node
      .querySelector<HTMLImageElement>('.msg-artifact-cards__logo')!
      .dispatchEvent(new Event('load'))
    expect(node.querySelector('.msg-artifact-cards__mark')).toBeNull()
  })

  it('a card with no logo still gets a monogram', () => {
    const node = host()
    renderCards(node, normalizeCardsPayload({ cards: [{ title: 'GME' }] })!, () => {})
    expect(node.querySelector('.msg-artifact-cards__mark')!.textContent).toBe('GM')
    expect(node.querySelector('.msg-artifact-cards__logo')).toBeNull()
  })

  it('never lets a payload string reach the DOM as markup', () => {
    const node = host()
    const evil = '<img src=x onerror="alert(1)">'
    renderCards(node, normalizeCardsPayload({ cards: [{ title: evil }] })!, () => {})

    const title = node.querySelector('.msg-artifact-cards__title')!
    expect(title.textContent).toBe(evil)
    expect(title.querySelector('img')).toBeNull()
    expect(node.querySelectorAll('img')).toHaveLength(0)
  })

  it('copies the field value on click', () => {
    const node = host()
    const onCopy = vi.fn()
    renderCards(node, normalizeCardsPayload({ cards: [AAPL] })!, onCopy)

    node.querySelector<HTMLButtonElement>('.msg-artifact-cards__copy')!.click()
    expect(onCopy).toHaveBeenCalledWith('0xaf3d76f1834a1d425780943c99ea8a608f8a93f9')
  })

  it('offers no copy button for a plain field', () => {
    const node = host()
    renderCards(
      node,
      normalizeCardsPayload({ cards: [{ title: 'X', fields: [{ label: 'A', value: 'b' }] }] })!,
      () => {},
    )
    expect(node.querySelector('.msg-artifact-cards__copy')).toBeNull()
  })

  it('announces dropped cards instead of hiding them', () => {
    const node = host()
    const cards = Array.from({ length: MAX_CARDS + 3 }, (_v, i) => ({ title: `T${i}` }))
    renderCards(node, normalizeCardsPayload({ cards })!, () => {})

    const overflow = node.querySelector<HTMLElement>('.msg-artifact-cards__overflow')!
    expect(overflow.hidden).toBe(false)
    expect(overflow.textContent).toContain('3')
  })

  it('replaces a previous render rather than appending to it', () => {
    const node = host()
    renderCards(node, normalizeCardsPayload({ cards: [AAPL] })!, () => {})
    renderCards(node, normalizeCardsPayload({ cards: [AAPL] })!, () => {})
    expect(node.querySelectorAll('.msg-artifact-cards__card')).toHaveLength(1)
  })
})

describe('createCardsMounter', () => {
  it('fetches the payload and clears the status on success', async () => {
    const node = host()
    node.dataset.cardsSrc = '/preview/lookup.json'
    const fetchPayload = vi.fn().mockResolvedValue({ cards: [AAPL] })

    const mounter = createCardsMounter({ fetchPayload })
    mounter.mountCards(document.body)
    await vi.waitFor(() => {
      expect(node.querySelectorAll('.msg-artifact-cards__card')).toHaveLength(1)
    })

    expect(fetchPayload).toHaveBeenCalledWith('/preview/lookup.json')
    expect(node.querySelector<HTMLElement>('.msg-artifact-cards__status')!.hidden).toBe(true)
  })

  it('claims each host once, so repeated mounts do not double-render', async () => {
    const node = host()
    node.dataset.cardsSrc = '/preview/lookup.json'
    const fetchPayload = vi.fn().mockResolvedValue({ cards: [AAPL] })

    const mounter = createCardsMounter({ fetchPayload })
    mounter.mountCards(document.body)
    mounter.mountCards(document.body)
    await vi.waitFor(() => expect(fetchPayload).toHaveBeenCalledTimes(1))
  })

  it('reports an unreadable payload without throwing', async () => {
    const node = host()
    node.dataset.cardsSrc = '/preview/lookup.json'
    const mounter = createCardsMounter({ fetchPayload: vi.fn().mockResolvedValue({ cards: [] }) })

    mounter.mountCards(document.body)
    await vi.waitFor(() => {
      expect(node.querySelector('.msg-artifact-cards__status')!.textContent).toContain('could not')
    })
  })

  it('reports a failed fetch without throwing', async () => {
    const node = host()
    node.dataset.cardsSrc = '/preview/lookup.json'
    const mounter = createCardsMounter({
      fetchPayload: vi.fn().mockRejectedValue(new Error('offline')),
    })

    mounter.mountCards(document.body)
    await vi.waitFor(() => {
      expect(node.querySelector('.msg-artifact-cards__status')!.textContent).toContain('failed')
    })
  })

  it('says so when the placeholder carries an empty source', async () => {
    // artifacts.ts always emits the attribute; it is empty when the artifact
    // has no preview URL, which is what this covers.
    const node = host()
    node.dataset.cardsSrc = ''
    const fetchPayload = vi.fn()
    const mounter = createCardsMounter({ fetchPayload })

    mounter.mountCards(document.body)
    await vi.waitFor(() => {
      expect(node.querySelector('.msg-artifact-cards__status')!.textContent).toContain(
        'unavailable',
      )
    })
    expect(fetchPayload).not.toHaveBeenCalled()
  })

  it('ignores a node that is not a cards placeholder at all', () => {
    host() // no data-cards-src attribute
    const fetchPayload = vi.fn()
    createCardsMounter({ fetchPayload }).mountCards(document.body)
    expect(fetchPayload).not.toHaveBeenCalled()
  })

  it('does not render into a host that left the document mid-fetch', async () => {
    const node = host()
    node.dataset.cardsSrc = '/preview/lookup.json'
    let release: (value: unknown) => void = () => {}
    const pending = new Promise((resolve) => {
      release = resolve
    })
    const mounter = createCardsMounter({ fetchPayload: () => pending })

    mounter.mountCards(document.body)
    node.remove()
    release({ cards: [AAPL] })
    await pending

    expect(node.querySelectorAll('.msg-artifact-cards__card')).toHaveLength(0)
  })

  it('mounts the fresh host when a row rebuild replaces the placeholder', async () => {
    // "Load earlier" and a session switch rebuild rows wholesale, so the old
    // host is detached and a new element takes its place.
    const first = host()
    first.dataset.cardsSrc = '/preview/lookup.json'
    const fetchPayload = vi.fn().mockResolvedValue({ cards: [AAPL] })
    const mounter = createCardsMounter({ fetchPayload })

    mounter.mountCards(document.body)
    await vi.waitFor(() => expect(fetchPayload).toHaveBeenCalledTimes(1))

    first.remove()
    const second = host()
    second.dataset.cardsSrc = '/preview/lookup.json'
    mounter.mountCards(document.body)
    await vi.waitFor(() => expect(fetchPayload).toHaveBeenCalledTimes(2))
    expect(second.querySelectorAll('.msg-artifact-cards__card')).toHaveLength(1)
  })

  it('re-attaching the same host does not re-render it', async () => {
    // The node still holds its cards, so refetching would be pure waste.
    const node = host()
    node.dataset.cardsSrc = '/preview/lookup.json'
    const fetchPayload = vi.fn().mockResolvedValue({ cards: [AAPL] })
    const mounter = createCardsMounter({ fetchPayload })

    mounter.mountCards(document.body)
    await vi.waitFor(() => expect(fetchPayload).toHaveBeenCalledTimes(1))

    node.remove()
    document.body.append(node)
    mounter.mountCards(document.body)
    expect(fetchPayload).toHaveBeenCalledTimes(1)
  })

  it('destroyAll releases claimed hosts', async () => {
    const node = host()
    node.dataset.cardsSrc = '/preview/lookup.json'
    const fetchPayload = vi.fn().mockResolvedValue({ cards: [AAPL] })
    const mounter = createCardsMounter({ fetchPayload })

    mounter.mountCards(document.body)
    await vi.waitFor(() => expect(fetchPayload).toHaveBeenCalledTimes(1))

    mounter.destroyAll()
    mounter.mountCards(document.body)
    await vi.waitFor(() => expect(fetchPayload).toHaveBeenCalledTimes(2))
  })
})
