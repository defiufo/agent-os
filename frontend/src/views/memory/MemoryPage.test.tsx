import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryPage } from './MemoryPage'

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}))

const mockRpc = {
  call: vi.fn(),
}
vi.mock('@/app/providers', () => ({
  useRpc: () => mockRpc,
}))

function renderMemoryPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <MemoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('MemoryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockRpc.call.mockImplementation(async (method: string, params?: Record<string, unknown>) => {
      if (method === 'memory.index') {
        return { chunkCount: 42, vecAvailable: true, ftsAvailable: true }
      }
      if (method === 'memory.curated.get') {
        if (params?.target === 'memory') {
          return {
            agentId: 'main',
            target: 'memory',
            entries: ['Follow strict type checking', 'Use conventional commits'],
            usage: '60/4,000',
            charCount: 60,
            charLimit: 4000,
            loadFailed: false,
          }
        }
        if (params?.target === 'user') {
          return {
            agentId: 'main',
            target: 'user',
            entries: ['Prefers concise responses'],
            usage: '26/2,000',
            charCount: 26,
            charLimit: 2000,
            loadFailed: false,
          }
        }
      }
      if (method === 'memory.knowledge_base.list') {
        return {
          count: 1,
          documents: [
            {
              path: 'knowledge_base/architecture.md',
              source: 'knowledge_base',
              sizeBytes: 1024,
              lineCount: 35,
              modifiedAt: '2026-08-23T00:00:00Z',
            },
          ],
        }
      }
      if (method === 'memory.list') {
        return {
          count: 2,
          files: [
            {
              path: 'MEMORY.md',
              source: 'memory',
              sizeBytes: 250,
              lineCount: 10,
              modifiedAt: '2026-08-23T00:00:00Z',
            },
            {
              path: 'knowledge_base/architecture.md',
              source: 'knowledge_base',
              sizeBytes: 1024,
              lineCount: 35,
              modifiedAt: '2026-08-23T00:00:00Z',
            },
          ],
        }
      }
      if (method === 'memory.search') {
        return {
          results: [
            {
              chunkId: 'c1',
              path: 'knowledge_base/architecture.md',
              source: 'knowledge_base',
              snippet: 'Pilot router architecture details',
              score: 0.92,
              startLine: 1,
              endLine: 5,
            },
          ],
        }
      }
      if (method === 'memory.curated.add') {
        return { message: 'Entry added.' }
      }
      if (method === 'memory.knowledge_base.remove') {
        return { removed: true }
      }
      return {}
    })
  })

  it('renders title, stats, and curated entries', async () => {
    renderMemoryPage()

    expect(screen.getByRole('heading', { level: 1, name: 'Memory' })).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('Follow strict type checking')).toBeInTheDocument()
      expect(screen.getByText('Prefers concise responses')).toBeInTheDocument()
      expect(screen.getByText('Vector search enabled')).toBeInTheDocument()
    })
  })

  it('switches to Knowledge Base tab and displays documents', async () => {
    renderMemoryPage()

    const kbTab = screen.getByRole('button', { name: /Knowledge Base/i })
    fireEvent.click(kbTab)

    await waitFor(() => {
      expect(screen.getByText('knowledge_base/architecture.md')).toBeInTheDocument()
      expect(screen.getByText('1.0 KB')).toBeInTheDocument()
    })
  })

  it('switches to Raw Sources tab and lists all files', async () => {
    renderMemoryPage()

    const rawTab = screen.getByRole('button', { name: /Raw Sources/i })
    fireEvent.click(rawTab)

    await waitFor(() => {
      expect(screen.getByText('MEMORY.md')).toBeInTheDocument()
      expect(screen.getByText('knowledge_base/architecture.md')).toBeInTheDocument()
    })
  })

  it('searches for queries on the Search tab', async () => {
    renderMemoryPage()

    const searchTab = screen.getByRole('button', { name: /Search/i })
    fireEvent.click(searchTab)

    const input = screen.getByPlaceholderText(/Search across memory/i)
    fireEvent.change(input, { target: { value: 'Pilot router' } })

    await waitFor(() => {
      expect(mockRpc.call).toHaveBeenCalledWith(
        'memory.search',
        expect.objectContaining({
          query: 'Pilot router',
        }),
      )
      expect(screen.getByText('Pilot router architecture details')).toBeInTheDocument()
      expect(screen.getByText('Score: 92%')).toBeInTheDocument()
    })
  })

  it('opens add entry dialog and submits', async () => {
    renderMemoryPage()

    const addButtons = await screen.findAllByRole('button', { name: /Add Entry/i })
    expect(addButtons[0]).toBeDefined()
    fireEvent.click(addButtons[0]!)

    expect(screen.getByRole('heading', { name: 'Add Curated Entry' })).toBeInTheDocument()

    const textarea = screen.getByPlaceholderText(/Write the convention or preference here/i)
    fireEvent.change(textarea, { target: { value: 'New convention added' } })

    const saveBtn = screen.getByRole('button', { name: 'Save Entry' })
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockRpc.call).toHaveBeenCalledWith(
        'memory.curated.add',
        expect.objectContaining({
          content: 'New convention added',
        }),
      )
    })
  })
})
