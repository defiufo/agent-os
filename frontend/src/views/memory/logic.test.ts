import { describe, expect, it } from 'vitest'
import {
  buildCuratedMarkdown,
  filterCuratedEntries,
  filterMemoryFiles,
  formatBytes,
  formatScore,
  formatUsagePercent,
  isNearBudget,
  parseCuratedEntries,
} from './logic'

describe('memory logic helpers', () => {
  it('formats bytes properly', () => {
    expect(formatBytes()).toBe('-')
    expect(formatBytes(500)).toBe('500 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  })

  it('calculates usage percentages and budget threshold', () => {
    expect(formatUsagePercent(2000, 4000)).toBe(50)
    expect(formatUsagePercent(4500, 4000)).toBe(100)
    expect(formatUsagePercent(0, 0)).toBe(0)

    expect(isNearBudget(3000, 4000)).toBe(false)
    expect(isNearBudget(3600, 4000)).toBe(true)
  })

  it('filters memory files by query and source', () => {
    const files = [
      { path: 'MEMORY.md', source: 'memory' },
      { path: 'memory/2026-08.md', source: 'memory' },
      { path: 'knowledge_base/handbook.pdf', source: 'knowledge_base' },
      { path: 'sessions/main/s1.md', source: 'sessions' },
    ]

    expect(filterMemoryFiles(files, '', 'all')).toHaveLength(4)
    expect(filterMemoryFiles(files, '', 'knowledge_base')).toHaveLength(1)
    expect(filterMemoryFiles(files, 'handbook', 'all')).toHaveLength(1)
    expect(filterMemoryFiles(files, '2026', 'memory')).toHaveLength(1)
  })

  it('filters curated entries by query', () => {
    const entries = ['Prefers dark mode', 'Use metric units', 'No unsolicited apologies']
    expect(filterCuratedEntries(entries, '')).toHaveLength(3)
    expect(filterCuratedEntries(entries, 'metric')).toEqual(['Use metric units'])
  })

  it('parses and builds curated markdown blocks', () => {
    const md = 'First rule\n§\nSecond rule\n'
    const parsed = parseCuratedEntries(md)
    expect(parsed).toEqual(['First rule', 'Second rule'])

    const built = buildCuratedMarkdown(parsed)
    expect(built).toBe('First rule\n§\nSecond rule\n')
  })

  it('formats search score percentages', () => {
    expect(formatScore(0.852)).toBe('85%')
    expect(formatScore(1.0)).toBe('100%')
  })
})
