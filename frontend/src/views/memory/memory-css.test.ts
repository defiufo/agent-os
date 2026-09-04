import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/views/memory/memory.css', 'utf8')

describe('Memory view CSS contract', () => {
  it('contains layout and panel styling for curated grids and stat cards', () => {
    expect(css).toMatch(/\.mem-view\s*\{[\s\S]*?max-width:\s*1200px;/)
    expect(css).toMatch(/\.mem-curated-grid\s*\{[\s\S]*?display:\s*grid;/)
    expect(css).toMatch(/\.mem-stat-card\s*\{[\s\S]*?border-radius:\s*var\(--radius-card/)
  })

  it('provides progress bars and status indicators', () => {
    expect(css).toMatch(/\.mem-budget-bar\s*\{[\s\S]*?height:\s*6px;/)
    expect(css).toMatch(/\.mem-budget-fill\.is-near-limit\s*\{[\s\S]*?background:/)
  })

  it('includes responsive rules and reduced motion fallbacks', () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*768px\)\s*\{[\s\S]*?\.mem-curated-grid\s*\{[\s\S]*?grid-template-columns:\s*1fr;/,
    )
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?transition:\s*none;/,
    )
  })
})
