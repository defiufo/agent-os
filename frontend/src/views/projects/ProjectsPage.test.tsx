import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { ProjectsPage } from './ProjectsPage'

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}))

const navigateSpy = vi.fn()
vi.mock('react-router', async () => {
  const actual = await vi.importActual<typeof import('react-router')>('react-router')
  return { ...actual, useNavigate: () => navigateSpy }
})

function makeRpc() {
  return {
    waitForConnection: vi.fn().mockResolvedValue(undefined),
    call: vi.fn(),
    on: vi.fn(() => () => {}),
  }
}
let mockRpc = makeRpc()

vi.mock('@/app/providers', () => ({
  useRpc: () => mockRpc,
}))

const PROJECTS = [
  {
    project_id: 'proj-1',
    agent_id: 'main',
    name: 'Token research',
    knowledge: 'Solana pools context.',
    updated_at: 300,
    session_count: 1,
  },
  {
    project_id: 'proj-2',
    agent_id: 'main',
    name: 'Docs',
    knowledge: '',
    updated_at: 100,
    session_count: 0,
  },
]

const SESSIONS = [
  { key: 'agent:main:webchat:aaa', project_id: 'proj-1', updated_at: 100, status: 'running' },
  { key: 'agent:main:webchat:bbb', updated_at: 200, status: 'running' },
]

const AGENTS = [{ id: 'main', name: 'Main' }]

function wireRpc(
  opts: {
    projects?: unknown[]
    createReject?: boolean
    updateReject?: boolean | 'conflict'
  } = {},
) {
  mockRpc.call.mockImplementation((method: string) => {
    switch (method) {
      case 'projects.list':
        return Promise.resolve({ projects: opts.projects ?? PROJECTS })
      case 'sessions.list':
        return Promise.resolve({ sessions: SESSIONS })
      case 'agents.list':
        return Promise.resolve({ agents: AGENTS })
      case 'projects.create':
        return opts.createReject
          ? Promise.reject(new Error('create failed'))
          : Promise.resolve({ project: { project_id: 'proj-new', name: 'New one' } })
      case 'projects.update':
        if (opts.updateReject === 'conflict') {
          const err = new Error('changed since it was read') as Error & { code?: string }
          err.code = 'project.conflict'
          return Promise.reject(err)
        }
        return opts.updateReject
          ? Promise.reject(new Error('update failed'))
          : Promise.resolve({ project: {} })
      case 'projects.delete':
        return Promise.resolve({ deleted: true, sessionsCleared: 1 })
      case 'sessions.create':
        return Promise.resolve({ key: 'agent:main:webchat:new' })
      default:
        return Promise.resolve({})
    }
  })
}

function renderPage(initialPath = '/projects') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ProjectsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    mockRpc = makeRpc()
    navigateSpy.mockClear()
    vi.mocked(toast.success).mockClear()
    vi.mocked(toast.error).mockClear()
  })

  it('lists projects with session counts', async () => {
    wireRpc()
    renderPage()
    await waitFor(() => expect(mockRpc.call).toHaveBeenCalledWith('projects.list', {}))
    expect(await screen.findByText('Token research')).toBeInTheDocument()
    expect(screen.getByText('Docs')).toBeInTheDocument()
    expect(screen.getByText(/1 session ·/)).toBeInTheDocument()
  })

  it('shows the empty state when there are no projects', async () => {
    wireRpc({ projects: [] })
    renderPage()
    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
  })

  it('creates a project with name and knowledge', async () => {
    wireRpc()
    renderPage()
    await screen.findByText('Token research')
    fireEvent.click(screen.getByRole('button', { name: /New project/i }))
    fireEvent.change(screen.getByPlaceholderText(/Token launch research/i), {
      target: { value: 'My proj' },
    })
    fireEvent.change(
      screen.getByPlaceholderText(/Instructions and facts shared with every session/i),
      { target: { value: 'Shared facts' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith('projects.create', {
        agentId: 'main',
        name: 'My proj',
        knowledge: 'Shared facts',
      }),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  it('selects a project and shows its sessions and knowledge', async () => {
    wireRpc()
    renderPage('/projects?project=proj-1')
    expect(await screen.findByDisplayValue('Solana pools context.')).toBeInTheDocument()
    expect(screen.getByText('agent:main:webchat:aaa')).toBeInTheDocument()
    expect(screen.queryByText('agent:main:webchat:bbb')).not.toBeInTheDocument()
  })

  it('saves edited knowledge via projects.update', async () => {
    wireRpc()
    renderPage('/projects?project=proj-1')
    const textarea = await screen.findByDisplayValue('Solana pools context.')
    fireEvent.change(textarea, { target: { value: 'Updated knowledge' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save knowledge' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith('projects.update', {
        projectId: 'proj-1',
        knowledge: 'Updated knowledge',
        expectedUpdatedAt: 300,
      }),
    )
  })

  it('subscribes to projects.changed and sessions.changed for live updates', async () => {
    wireRpc()
    renderPage()
    await screen.findByText('Token research')
    const events = (mockRpc.on.mock.calls as unknown[][]).map((c) => c[0])
    expect(events).toContain('projects.changed')
    expect(events).toContain('sessions.changed')
  })

  it('shows a conflict toast and keeps the draft when the save loses the CAS', async () => {
    wireRpc({ updateReject: 'conflict' })
    renderPage('/projects?project=proj-1')
    const textarea = await screen.findByDisplayValue('Solana pools context.')
    fireEvent.change(textarea, { target: { value: 'Mine' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save knowledge' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('changed elsewhere'),
        expect.anything(),
      ),
    )
    // The user's draft survives the conflict for re-apply.
    expect(screen.getByDisplayValue('Mine')).toBeTruthy()
  })

  it('shows the load error state instead of the empty state', async () => {
    mockRpc.call.mockImplementation((method: string) =>
      method === 'projects.list' ? Promise.reject(new Error('gateway down')) : Promise.resolve({}),
    )
    renderPage()
    await screen.findByText('Projects failed to load')
    expect(screen.queryByText('No projects yet')).toBeNull()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeTruthy()
  })

  it('starts a new chat in the project and navigates to it', async () => {
    wireRpc()
    renderPage('/projects?project=proj-1')
    fireEvent.click(await screen.findByRole('button', { name: /New chat in project/i }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith('sessions.create', {
        agentId: 'main',
        projectId: 'proj-1',
      }),
    )
    await waitFor(() =>
      expect(navigateSpy).toHaveBeenCalledWith(
        '/chat?session=' + encodeURIComponent('agent:main:webchat:new'),
      ),
    )
  })

  it('deletes a project after confirmation', async () => {
    wireRpc()
    renderPage('/projects?project=proj-1')
    fireEvent.click(await screen.findByRole('button', { name: /Delete project/i }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete project' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith('projects.delete', { projectId: 'proj-1' }),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })
})
