import { describe, expect, it } from 'vitest'
import {
  filterVars,
  groupByCategory,
  isShadowed,
  isValidEnvName,
  sourceLabel,
  shortPath,
  splitGroupRows,
  summarize,
  validateNewName,
  type EnvVarRow,
} from './logic'

function row(partial: Partial<EnvVarRow> & { name: string }): EnvVarRow {
  return {
    isSet: false,
    source: 'unset',
    masked: null,
    secret: true,
    description: '',
    url: '',
    category: 'custom',
    owner: '',
    required: false,
    writable: true,
    restartRequired: false,
    missing: false,
    ...partial,
  }
}

describe('isValidEnvName', () => {
  it.each(['A', '_A', 'OPENAI_API_KEY', 'x1'])('accepts %s', (name) => {
    expect(isValidEnvName(name)).toBe(true)
  })

  it.each(['', '1BAD', 'A-B', 'A B', 'A.B'])('rejects %s', (name) => {
    expect(isValidEnvName(name)).toBe(false)
  })
})

describe('isShadowed', () => {
  it('flags only values coming from the process environment', () => {
    // This is the difference between "saved" and "in effect".
    expect(isShadowed(row({ name: 'A', source: 'process' }))).toBe(true)
    expect(isShadowed(row({ name: 'A', source: 'home_file' }))).toBe(false)
  })
})

describe('sourceLabel', () => {
  it('names each source in operator terms', () => {
    expect(sourceLabel('process')).toBe('process env')
    expect(sourceLabel('cwd_file')).toBe('project .env')
    expect(sourceLabel('home_file')).toBe('AgentOS .env')
  })
})

describe('filterVars', () => {
  const rows = [
    row({ name: 'SET_ONE', isSet: true, category: 'provider' }),
    row({ name: 'UNSET_ONE', category: 'skill', owner: 'onchain' }),
    row({ name: 'MY_OWN', isSet: true, category: 'custom' }),
  ]

  it('missing keeps only what is not set', () => {
    expect(filterVars(rows, 'missing', '').map((r) => r.name)).toEqual(['UNSET_ONE'])
  })

  it('set keeps only what is set', () => {
    expect(filterVars(rows, 'set', '').map((r) => r.name)).toEqual(['SET_ONE', 'MY_OWN'])
  })

  it('custom keeps only undeclared variables', () => {
    expect(filterVars(rows, 'custom', '').map((r) => r.name)).toEqual(['MY_OWN'])
  })

  it('searches name, description, and owner', () => {
    expect(filterVars(rows, 'all', 'onchain').map((r) => r.name)).toEqual(['UNSET_ONE'])
    // Substring search, so a query must be distinctive: "set_one" also
    // appears inside "UNSET_ONE".
    expect(filterVars(rows, 'all', 'my_own').map((r) => r.name)).toEqual(['MY_OWN'])
    expect(filterVars(rows, 'all', 'set_one').map((r) => r.name)).toEqual(['SET_ONE', 'UNSET_ONE'])
  })
})

describe('groupByCategory', () => {
  it('orders groups so what a new install configures first comes first', () => {
    const groups = groupByCategory([
      row({ name: 'C', category: 'custom' }),
      row({ name: 'S', category: 'skill' }),
      row({ name: 'P', category: 'provider' }),
    ])
    expect(groups.map((g) => g.category)).toEqual(['provider', 'skill', 'custom'])
  })

  it('labels groups for humans and counts what is set', () => {
    const groups = groupByCategory([
      row({ name: 'A', category: 'provider', isSet: true }),
      row({ name: 'B', category: 'provider' }),
    ])
    const providers = groups[0]!
    expect(providers.label).toBe('LLM providers')
    expect(providers.setCount).toBe(1)
    expect(providers.rows.map((r) => r.name)).toEqual(['A', 'B'])
  })

  it('sorts unknown categories last without dropping them', () => {
    const groups = groupByCategory([
      row({ name: 'X', category: 'something-new' }),
      row({ name: 'P', category: 'provider' }),
    ])
    expect(groups.map((g) => g.category)).toEqual(['provider', 'something-new'])
  })
})

describe('summarize', () => {
  it('prefers server counts over recomputing them', () => {
    const summary = summarize({
      envFilePath: '/tmp/.env',
      vars: [row({ name: 'A' })],
      setCount: 7,
      totalCount: 9,
      shadowedCount: 2,
    })
    expect(summary).toMatchObject({ setCount: 7, totalCount: 9, shadowedCount: 2 })
  })

  it('handles an absent payload', () => {
    expect(summarize(undefined)).toEqual({
      setCount: 0,
      totalCount: 0,
      shadowedCount: 0,
      missingCount: 0,
    })
  })
})

describe('validateNewName', () => {
  it('requires a name', () => {
    expect(validateNewName('  ', [])).toMatch(/Enter a variable name/)
  })

  it('explains the naming rule rather than just rejecting', () => {
    expect(validateNewName('1BAD', [])).toMatch(/letters, digits, and underscores/)
  })

  it('refuses a name the server will not write', () => {
    const known = [row({ name: 'PATH', writable: false })]
    expect(validateNewName('PATH', known)).toMatch(/cannot be written through AgentOS/)
  })

  it('accepts a valid new name', () => {
    expect(validateNewName('MY_TOKEN', [])).toBeNull()
  })
})

describe('splitGroupRows', () => {
  it('keeps set and required-missing rows visible, folds the quiet tail', () => {
    // ~22 provider keys are declared and an install uses one; flat, the 21
    // empty rows bury the ones the operator can act on.
    const [group] = groupByCategory([
      row({ name: 'CONFIGURED', category: 'provider', isSet: true }),
      row({ name: 'NEEDED', category: 'provider', required: true, missing: true }),
      row({ name: 'IDLE_ONE', category: 'provider' }),
      row({ name: 'IDLE_TWO', category: 'provider' }),
    ])
    const { primary, rest } = splitGroupRows(group!)
    expect(primary.map((r) => r.name)).toEqual(['CONFIGURED', 'NEEDED'])
    expect(rest.map((r) => r.name)).toEqual(['IDLE_ONE', 'IDLE_TWO'])
  })

  it('folds everything when nothing is set or needed', () => {
    const [group] = groupByCategory([
      row({ name: 'A', category: 'search' }),
      row({ name: 'B', category: 'search' }),
    ])
    const { primary, rest } = splitGroupRows(group!)
    expect(primary).toEqual([])
    expect(rest).toHaveLength(2)
  })
})

describe('shortPath', () => {
  it('keeps the identifying tail and marks the elision', () => {
    expect(shortPath('/very/long/path/to/state/.env')).toBe('…/state/.env')
  })

  it('leaves an already-short path alone', () => {
    expect(shortPath('/tmp/.env')).toBe('/tmp/.env')
  })

  it('handles an absent path', () => {
    expect(shortPath(undefined)).toBe('')
  })

  it('shortens a Windows path', () => {
    expect(shortPath('C:\\Users\\username\\.agentos\\.env')).toBe('…/.agentos/.env')
  })

  it('leaves an already-short Windows path alone', () => {
    expect(shortPath('C:\\.env')).toBe('C:\\.env')
  })

  it('shortens a mixed-separator path', () => {
    expect(shortPath('C:\\Users\\me/projects/app/.env')).toBe('…/app/.env')
  })
})
