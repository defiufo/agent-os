// Chat transcript — inline card-grid artifacts.
//
// A skill publishes a JSON artifact with the `application/vnd.agentos.cards+json`
// mime; the artifact renderer emits a mount placeholder for it (instead of the
// usual download chip) and this module fetches the payload and builds a grid of
// cards into that placeholder.
//
// This is the tabular/record counterpart to chart.ts: where a chart draws a time
// series, this draws a small set of labelled records — a token lookup, a holder
// list, a search result — with an optional status badge and copy button per card.
//
// Two surfaces, mirroring chart.ts / artifacts.ts:
//   1. Pure helpers (top-level exports) — mime match and payload normalization.
//      No DOM, no network. This is the unit-test surface (cards.test.ts).
//   2. `createCardsMounter(deps)` — the imperative mounter the transcript
//      composes alongside the chart mounter.
//
// SECURITY: every payload-derived string reaches the DOM through `textContent`
// or `createElement`, never `innerHTML`. Card fields carry token names, symbols
// and on-chain metadata, which are fully attacker-controlled on a permissionless
// chain — they are display data, never markup. Logo URLs are additionally
// restricted to http(s) so a payload cannot smuggle `javascript:` into `src`.

import { t } from '@/i18n'
import '@/i18n/en/chat'

import type { Artifact } from './artifacts'

/** The mime a skill publishes to get an inline card grid instead of a download chip. */
export const CARDS_ARTIFACT_MIME = 'application/vnd.agentos.cards+json'

/** Cards rendered before the grid stops growing and the rest are summarised. */
export const MAX_CARDS = 24

/* ── Payload shape ──────────────────────────────────────────────────────── */

/**
 * A card's status badge. The tone drives the colour; unknown tones fall back to
 * neutral so a skill can ship a new status without a frontend release.
 */
export type CardTone = 'positive' | 'warning' | 'danger' | 'neutral'

const TONES = new Set<CardTone>(['positive', 'warning', 'danger', 'neutral'])

/** One labelled row inside a card's body. */
export interface CardField {
  label: string
  value: string
  /** Render in a monospace cell with a copy button — addresses, hashes, ids. */
  copyable?: boolean
}

/** One card. Only `title` is required. */
export interface Card {
  title: string
  subtitle: string
  /** Small leading image — a token or company logo. http(s) only. */
  logo: string
  /** Short badge text, e.g. "verified". */
  badge: string
  badgeTone: CardTone
  fields: CardField[]
}

/** The artifact body a skill writes. Only `cards` is required. */
export interface CardsPayload {
  type: 'cards'
  title: string
  subtitle: string
  cards: Card[]
  /** Cards dropped by the MAX_CARDS cap, so the UI can say so rather than lie. */
  overflow: number
}

/* ── Pure helpers (unit-tested) ─────────────────────────────────────────── */

/** True when the artifact should render as an inline card grid. */
export function isCardsArtifact(artifact: Artifact | null | undefined): boolean {
  if (!artifact || !artifact.mime) return false
  return String(artifact.mime).toLowerCase().split(';')[0]?.trim() === CARDS_ARTIFACT_MIME
}

function textField(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

/**
 * Keep only http(s) logo URLs.
 *
 * A payload field becomes an `<img src>`, so `javascript:` and `data:` are
 * dropped rather than sanitised — a skill has no reason to inline an image, and
 * refusing is cheaper to reason about than filtering.
 */
export function safeLogoUrl(value: unknown): string {
  const raw = textField(value)
  if (!raw) return ''
  try {
    const url = new URL(raw)
    return url.protocol === 'https:' || url.protocol === 'http:' ? raw : ''
  } catch {
    return ''
  }
}

function normalizeTone(value: unknown): CardTone {
  const raw = textField(value).toLowerCase()
  return TONES.has(raw as CardTone) ? (raw as CardTone) : 'neutral'
}

function normalizeField(raw: unknown): CardField | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  const label = textField(row.label)
  const value = textField(row.value)
  // A field with no value is noise in a dense grid; a label-less value is not.
  if (!value) return null
  const field: CardField = { label, value }
  if (row.copyable === true) field.copyable = true
  return field
}

function normalizeCard(raw: unknown): Card | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  const title = textField(row.title)
  if (!title) return null
  const fields = Array.isArray(row.fields)
    ? row.fields.map(normalizeField).filter((f): f is CardField => f !== null)
    : []
  return {
    title,
    subtitle: textField(row.subtitle),
    logo: safeLogoUrl(row.logo),
    badge: textField(row.badge),
    badgeTone: normalizeTone(row.badgeTone),
    fields,
  }
}

/**
 * Validate and normalize an artifact body into a renderable payload, or return
 * null when there is nothing to draw.
 *
 * Cards past `MAX_CARDS` are dropped and counted in `overflow` rather than
 * rendered: an unbounded grid from a wide query would push the rest of the
 * transcript off-screen, and silently truncating would misreport the result.
 */
export function normalizeCardsPayload(raw: unknown): CardsPayload | null {
  if (!raw || typeof raw !== 'object') return null
  const body = raw as Record<string, unknown>
  if (!Array.isArray(body.cards)) return null

  const all = body.cards.map(normalizeCard).filter((c): c is Card => c !== null)
  if (all.length === 0) return null

  return {
    type: 'cards',
    title: textField(body.title),
    subtitle: textField(body.subtitle),
    cards: all.slice(0, MAX_CARDS),
    overflow: Math.max(0, all.length - MAX_CARDS),
  }
}

/* ── DOM building ───────────────────────────────────────────────────────── */

function el(tag: string, className: string, text?: string): HTMLElement {
  const node = document.createElement(tag)
  node.className = className
  // textContent, never innerHTML — see the security note at the top.
  if (text !== undefined) node.textContent = text
  return node
}

function buildField(field: CardField, onCopy: (value: string) => void): HTMLElement {
  const row = el('div', 'msg-artifact-cards__field')
  if (field.label) row.append(el('span', 'msg-artifact-cards__field-label', field.label))

  const valueClass = field.copyable
    ? 'msg-artifact-cards__field-value msg-artifact-cards__field-value--mono'
    : 'msg-artifact-cards__field-value'
  const value = el('span', valueClass, field.value)
  value.title = field.value
  row.append(value)

  if (field.copyable) {
    const button = el('button', 'msg-artifact-cards__copy')
    button.setAttribute('type', 'button')
    button.setAttribute('aria-label', t('chat.cardsCopy'))
    button.title = t('chat.cardsCopy')
    button.textContent = '⧉'
    button.addEventListener('click', () => onCopy(field.value))
    row.append(button)
  }
  return row
}

/** Up to two leading alphanumerics of the title, for the monogram. */
export function monogram(title: string): string {
  const cleaned = title.replace(/[^\p{L}\p{N}]/gu, '')
  return cleaned.slice(0, 2).toUpperCase()
}

function buildCard(card: Card, onCopy: (value: string) => void): HTMLElement {
  const host = el('article', 'msg-artifact-cards__card')

  const header = el('header', 'msg-artifact-cards__head')
  // A ticker monogram is the base, not a fallback. The console's CSP allows
  // images from 'self', data: and raw.githubusercontent.com only, so a logo on
  // a token-list CDN never loads (skills/hub/bankr.py hit the same wall and made
  // the same call). Fetching one would also tell that CDN which tickers the user
  // is researching, from their IP -- a real leak for a finance surface. The img
  // below still covers a logo the CSP does happen to allow.
  const mark = el('span', 'msg-artifact-cards__mark', monogram(card.title))
  mark.setAttribute('aria-hidden', 'true')
  header.append(mark)

  if (card.logo) {
    const img = document.createElement('img')
    img.className = 'msg-artifact-cards__logo'
    img.src = card.logo
    img.alt = ''
    img.loading = 'lazy'
    // Only take over from the monogram once the bytes actually arrived.
    img.addEventListener('load', () => mark.remove())
    img.addEventListener('error', () => img.remove())
    header.append(img)
  }

  const heading = el('div', 'msg-artifact-cards__heading')
  heading.append(el('span', 'msg-artifact-cards__title', card.title))
  if (card.subtitle) {
    const subtitle = el('span', 'msg-artifact-cards__subtitle', card.subtitle)
    subtitle.title = card.subtitle
    heading.append(subtitle)
  }
  header.append(heading)
  host.append(header)

  if (card.badge) {
    const badge = el('span', 'msg-artifact-cards__badge', card.badge)
    badge.dataset.tone = card.badgeTone
    host.append(badge)
  }

  if (card.fields.length > 0) {
    const body = el('div', 'msg-artifact-cards__fields')
    card.fields.forEach((field) => body.append(buildField(field, onCopy)))
    host.append(body)
  }
  return host
}

/**
 * Replace `host`'s grid with the payload's cards.
 *
 * Exported for the unit tests, which assert on the built DOM without standing up
 * a mounter or a fetch.
 */
export function renderCards(
  host: HTMLElement,
  payload: CardsPayload,
  onCopy: (value: string) => void,
): void {
  const grid = host.querySelector<HTMLElement>('.msg-artifact-cards__grid')
  if (!grid) return

  if (payload.title) {
    const label = host.querySelector<HTMLElement>('.msg-artifact-cards__name')
    if (label) {
      label.textContent = payload.title
      if (payload.subtitle) label.title = payload.subtitle
    }
  }

  grid.replaceChildren(...payload.cards.map((card) => buildCard(card, onCopy)))

  const overflow = host.querySelector<HTMLElement>('.msg-artifact-cards__overflow')
  if (overflow) {
    overflow.textContent =
      payload.overflow > 0 ? t('chat.cardsOverflow', { count: String(payload.overflow) }) : ''
    overflow.hidden = payload.overflow === 0
  }
}

/* ── Mounter ────────────────────────────────────────────────────────────── */

export interface CardsMounterDeps {
  /** Fetch a cards artifact body from its (authenticated) URL. */
  fetchPayload: (url: string) => Promise<unknown>
  /** Copy a field value to the clipboard. Default: the Clipboard API. */
  copyText?: (value: string) => void
  /** chat.js `_chatDiag` — the diagnostics ring. Default: no-op. */
  diag?: (event: string, detail: Record<string, unknown>) => void
}

/**
 * Create the cards mounter bound to the transcript's fetch surface.
 *
 * `mountCards(root)` is idempotent: it only picks up placeholders that have not
 * been mounted yet, so the streaming path may call it after every artifact
 * append and the history path after a bulk replay, without double-rendering.
 *
 * Unlike charts there is nothing to dispose — a card grid is plain DOM with no
 * canvas, observer or library handle — so the mounter only tracks claimed hosts
 * and drops the ones that leave the document.
 */
export function createCardsMounter(deps: CardsMounterDeps) {
  const diag = deps.diag ?? ((): void => {})
  const copyText =
    deps.copyText ??
    ((value: string): void => {
      void navigator.clipboard?.writeText(value)
    })
  /** Hosts this mounter has picked up — payload in flight or cards rendered. */
  const claimed = new Set<HTMLElement>()

  function pruneDetached(): void {
    for (const host of [...claimed]) {
      if (!host.isConnected) claimed.delete(host)
    }
  }

  function setStatus(host: HTMLElement, message: string): void {
    const status = host.querySelector<HTMLElement>('.msg-artifact-cards__status')
    if (!status) return
    status.textContent = message
    status.hidden = message === ''
  }

  async function mountOne(host: HTMLElement): Promise<void> {
    const url = host.dataset.cardsSrc || ''
    if (!url) {
      setStatus(host, t('chat.cardsUnavailable'))
      return
    }
    try {
      diag('cards.mount.start', { url })
      const raw = await deps.fetchPayload(url)
      const payload = normalizeCardsPayload(raw)
      if (!payload) {
        setStatus(host, t('chat.cardsUnreadable'))
        diag('cards.mount.empty', { url })
        return
      }
      // The row can be rebuilt while the payload is in flight — cards rendered
      // into a detached host have nobody to show them.
      if (!host.isConnected) {
        claimed.delete(host)
        return
      }
      renderCards(host, payload, copyText)
      setStatus(host, '')
      diag('cards.mount.done', { url, cards: payload.cards.length })
    } catch (error) {
      setStatus(host, t('chat.cardsFailed'))
      diag('cards.mount.error', { url, error: String(error) })
    }
  }

  /** Mount every not-yet-mounted card placeholder inside `root`. */
  function mountCards(root: HTMLElement | null | undefined): void {
    if (!root) return
    pruneDetached()
    const hosts = root.querySelectorAll<HTMLElement>('[data-cards-src]')
    hosts.forEach((host) => {
      if (claimed.has(host)) return
      claimed.add(host)
      void mountOne(host)
    })
  }

  /** Forget every claimed host (route unmount). */
  function destroyAll(): void {
    claimed.clear()
  }

  return { mountCards, destroyAll, pruneDetached }
}

export type CardsMounter = ReturnType<typeof createCardsMounter>
