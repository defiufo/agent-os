import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/views/projects/projects.css', 'utf8')

describe('Projects view CSS contract', () => {
  it('lays the master list and detail panel out as a responsive split', () => {
    expect(css).toMatch(
      /\.proj-split \{[\s\S]*?grid-template-columns: minmax\(16rem, 22rem\) minmax\(0, 1fr\);/,
    )
    expect(css).toMatch(
      /@media \(max-width: 900px\)[\s\S]*?\.proj-split \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);/,
    )
  })

  it('stacks the page header above full-width actions on mobile', () => {
    // Matches the overview header: side-by-side on mobile squeezes the
    // subtitle into a narrow column with the buttons overlapping it.
    expect(css).toMatch(
      /@media \(max-width: 700px\)[\s\S]*?\.proj-stage__header \{[\s\S]*?flex-direction: column;/,
    )
    expect(css).toMatch(
      /@media \(max-width: 700px\)[\s\S]*?\.proj-stage__actions \{[\s\S]*?width: 100%;/,
    )
  })

  it('keeps the knowledge editor vertically resizable with a minimum height', () => {
    expect(css).toMatch(
      /\.proj-knowledge-input \{[\s\S]*?resize: vertical;[\s\S]*?min-height: 6rem;/,
    )
  })

  it('keeps dialogs on a fixed, centered overlay layer', () => {
    // ModalShell portals into document.body: without fixed+inset+flex the
    // dialog renders in normal flow at the bottom of the page.
    expect(css).toMatch(
      /\.proj-modal__overlay \{[\s\S]*?position: fixed;[\s\S]*?inset: 0;[\s\S]*?display: flex;[\s\S]*?align-items: center;[\s\S]*?justify-content: center;/,
    )
    expect(css).toMatch(/\.proj-modal \{[\s\S]*?max-height: calc\(100dvh - 3rem\);/)
  })

  it('marks the selected project card and constrains card excerpts', () => {
    expect(css).toMatch(/\.proj-card\.is-selected \{[\s\S]*?border-color: var\(--border\);/)
    expect(css).toMatch(/\.proj-card__excerpt \{[\s\S]*?-webkit-line-clamp: 2;/)
  })
})
