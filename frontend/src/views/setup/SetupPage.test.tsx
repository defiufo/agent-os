import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { SetupPage } from './SetupPage'

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const navigateSpy = vi.fn()
vi.mock('react-router', async () => {
  const actual = await vi.importActual<typeof import('react-router')>('react-router')
  return { ...actual, useNavigate: () => navigateSpy }
})

const mockRpc = {
  waitForConnection: vi.fn().mockResolvedValue(undefined),
  call: vi.fn(),
  on: vi.fn(() => () => {}),
}
vi.mock('@/app/providers', () => ({
  useRpc: () => mockRpc,
  useBootstrap: () => ({
    version: '1',
    ws_url: 'ws://127.0.0.1:18791/ws',
    auth_mode: 'none',
    base_path: '/control',
    features: {},
  }),
}))

// A representative onboarding.catalog covering every section.
const CATALOG = {
  providers: [
    {
      providerId: 'openai',
      label: 'OpenAI',
      runtimeSupported: true,
      routerSupported: true,
      whatYouNeed: ['API key via OPENAI_API_KEY or a one-time paste.'],
      fields: [
        { name: 'api_key', label: 'API key', type: 'password', secret: true, required: true },
        { name: 'api_key_env', label: 'API key env', default: 'OPENAI_API_KEY' },
        { name: 'base_url', label: 'Base URL' },
      ],
    },
    {
      providerId: 'opencap',
      label: 'OpenCAP',
      runtimeSupported: true,
      routerSupported: true,
      whatYouNeed: ['API key via OPENCAP_API_KEY or a one-time paste.'],
      fields: [
        { name: 'model', label: 'Model', default: 'minimax-m3' },
        { name: 'api_key', label: 'API key', type: 'password', secret: true, required: true },
        { name: 'api_key_env', label: 'API key env', default: 'OPENCAP_API_KEY' },
        {
          name: 'base_url',
          label: 'Base URL',
          default: 'https://gw.capminal.ai/api/inference/v1',
        },
        { name: 'proxy', label: 'HTTP proxy' },
      ],
    },
    { providerId: 'other', label: 'Other', runtimeSupported: true, fields: [] },
  ],
  routerProfiles: {
    defaultTier: 'c1',
    profiles: [
      {
        providerId: 'openai',
        tiers: {
          c0: { provider: 'openai', model: 'gpt-4o-mini' },
          c1: { provider: 'openai', model: 'gpt-4o' },
          image_model: { provider: 'openai', model: 'dall-e' },
        },
      },
    ],
    judge: { profiles: { openai: { autoModel: 'gpt-4o', models: ['gpt-4o', 'gpt-4o-mini'] } } },
  },
  channels: [
    {
      type: 'telegram',
      label: 'Telegram',
      whatYouNeed: ['Bot token from @BotFather'],
      fields: [
        { name: 'name', label: 'Name', required: true },
        { name: 'token', label: 'Bot token', type: 'password', secret: true, required: true },
      ],
    },
  ],
  searchProviders: [
    { providerId: 'duckduckgo', label: 'DuckDuckGo', runtimeSupported: true },
    {
      providerId: 'brave',
      label: 'Brave',
      runtimeSupported: true,
      requiresApiKey: true,
      envKey: 'BRAVE_API_KEY',
      whatYouNeed: ['API key via BRAVE_API_KEY or a one-time paste.'],
    },
  ],
  xSearch: [
    {
      providerId: 'x_search',
      label: 'X (Twitter) Search',
      requiresApiKey: true,
      envKey: 'XAI_API_KEY',
      whatYouNeed: ['An xAI API key via XAI_API_KEY or a one-time paste.'],
    },
  ],
  memoryEmbeddingProviders: [
    { providerId: 'auto', label: 'Auto' },
    { providerId: 'openai', label: 'OpenAI', requiresApiKey: true, envKey: 'OPENAI_API_KEY' },
    { providerId: 'local', label: 'Local BGE' },
  ],
  imageGenerationProviders: [
    {
      providerId: 'openrouter',
      label: 'OpenRouter',
      requiresApiKey: true,
      envKey: 'OPENROUTER_API_KEY',
    },
  ],
  audioProviders: [
    {
      providerId: 'elevenlabs',
      label: 'ElevenLabs',
      requiresApiKey: true,
      envKey: 'ELEVENLABS_API_KEY',
    },
  ],
}

const CONFIG = {
  llm: { provider: 'openai', model: 'gpt-4o', api_key_env: 'OPENAI_API_KEY' },
  agentos_router: { enabled: true, strategy: 'pilot-v1', default_tier: 'c1' },
  search_provider: 'duckduckgo',
  memory: { curated_memory_char_limit: 4000, curated_user_char_limit: 2000, inject_limit: 6400 },
}

function statusFor(overrides: Record<string, unknown> = {}) {
  return {
    needsOnboarding: false,
    hasConfig: true,
    llmConfigured: true,
    configPath: '/tmp/agentos.toml',
    channelCount: 0,
    sectionDetails: {
      llm: { label: 'Provider', status: 'ok', required: true },
      router: { label: 'Router', status: 'ok', required: true },
      channels: { label: 'Channels', status: 'optional' },
      search: { label: 'Search', status: 'ok' },
    },
    ...overrides,
  }
}

// Route each read to its payload; onboarding.*.configure default to {}.
function wireCalls(status: Record<string, unknown> = statusFor()) {
  mockRpc.call.mockImplementation((method: string) => {
    if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
    if (method === 'onboarding.status') return Promise.resolve(status)
    if (method === 'config.get') return Promise.resolve(CONFIG)
    if (method === 'doctor.memory.status') return Promise.resolve(null)
    return Promise.resolve({})
  })
}

function renderPage(props: ComponentProps<typeof SetupPage> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = (nextProps: ComponentProps<typeof SetupPage>) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SetupPage {...nextProps} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const result = render(page(props))
  return {
    ...result,
    rerenderPage: (nextProps: ComponentProps<typeof SetupPage>) => result.rerender(page(nextProps)),
  }
}

describe('SetupPage', () => {
  beforeEach(() => {
    mockRpc.call.mockReset()
    mockRpc.waitForConnection.mockReset().mockResolvedValue(undefined)
    navigateSpy.mockReset()
    vi.mocked(toast.info).mockClear()
    vi.mocked(toast.error).mockClear()
    vi.mocked(toast.warning).mockClear()
  })
  afterEach(() => vi.clearAllTimers())

  it('loads guided settings without duplicating channel setup', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    const methods = mockRpc.call.mock.calls.map((c) => c[0])
    expect(methods).toContain('onboarding.catalog')
    expect(methods).toContain('onboarding.status')
    expect(methods).toContain('config.get')
    expect(methods).not.toContain('channels.status')
    expect(mockRpc.waitForConnection).toHaveBeenCalled()
    expect(screen.getByRole('navigation', { name: 'Setup steps' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Provider:/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Channels:/ })).not.toBeInTheDocument()
  })

  it('shows the error banner when a setup read fails', async () => {
    mockRpc.call.mockImplementation((method: string) =>
      method === 'onboarding.catalog' ? Promise.reject(new Error('boom')) : Promise.resolve({}),
    )
    renderPage()
    await waitFor(() =>
      expect(screen.getByText(/Failed to load setup catalog: boom/)).toBeInTheDocument(),
    )
  })

  it('keeps channel readiness visible but sends its setup outside Settings', async () => {
    wireCalls(statusFor({ sectionDetails: { channels: { status: 'missing', label: 'Channels' } } }))
    renderPage()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finish' })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /^Channels:/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Channels setup needed'))
    expect(navigateSpy).toHaveBeenCalledWith('/channels?view=setup')
  })

  it('provider save calls onboarding.provider.configure with masked secret + advances', async () => {
    wireCalls(statusFor({ needsOnboarding: true, sectionDetails: { llm: { status: 'missing' } } }))
    // Start on provider (llm missing).
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Provider')).toBeInTheDocument())

    // The API key input is a password field (masked) and never carries a value.
    const keyInput = screen.getByLabelText('API key') as HTMLInputElement
    expect(keyInput.type).toBe('password')
    fireEvent.change(keyInput, { target: { value: 'sk-secret' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save Provider' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith(
        'onboarding.provider.configure',
        expect.objectContaining({ providerId: 'openai', apiKey: 'sk-secret' }),
      ),
    )
  })

  it('configures OpenCAP defaults without submitting two credential sources', async () => {
    wireCalls(statusFor({ needsOnboarding: true, sectionDetails: { llm: { status: 'missing' } } }))
    renderPage()
    const provider = await screen.findByLabelText('Provider')

    fireEvent.change(provider, { target: { value: 'opencap' } })

    expect(screen.getByLabelText('Model')).toHaveValue('minimax-m3')
    expect(screen.getByLabelText('API key env')).toHaveValue('OPENCAP_API_KEY')
    expect(screen.getByText('Advanced provider connection').closest('details')).not.toHaveAttribute(
      'open',
    )

    fireEvent.change(screen.getByLabelText('API key'), {
      target: { value: 'opencap-browser-regression-key' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save Provider' }))

    await waitFor(() => {
      const call = mockRpc.call.mock.calls.find(
        (entry) => entry[0] === 'onboarding.provider.configure',
      )
      expect(call?.[1]).toMatchObject({
        providerId: 'opencap',
        model: 'minimax-m3',
        apiKey: 'opencap-browser-regression-key',
        baseUrl: 'https://gw.capminal.ai/api/inference/v1',
      })
      expect(call?.[1]).not.toHaveProperty('apiKeyEnv')
    })
  })

  it('uses the shared select and checkbox controls throughout Guided setup', async () => {
    wireCalls()
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /^Provider:/ }))
    const provider = await screen.findByLabelText('Provider')
    expect(provider.parentElement).toHaveClass('setup-select')

    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    expect(screen.getByLabelText('Search provider').parentElement).toHaveClass('setup-select')
    const imageToggle = screen.getByLabelText('Image generation enabled')
    const imageProvider = screen.getByLabelText('Image provider')
    expect(imageToggle).toHaveClass('setup-check__input')
    expect(
      imageToggle.compareDocumentPosition(imageProvider) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    const audioToggle = screen.getByLabelText('Voice audio enabled')
    fireEvent.click(audioToggle)
    expect(screen.getByText('Save to make voice audio available to agents.')).toBeInTheDocument()
    const audioProvider = screen.getByLabelText('Audio provider')
    expect(
      audioToggle.compareDocumentPosition(audioProvider) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /^Finish:/ }))
    expect(screen.getByLabelText('Notify on new release')).toHaveClass('setup-check__input')
  })

  it('router save calls onboarding.router.configure with the assembled payload', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Router Tiers:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save Router' })).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))
    await waitFor(() => {
      const call = mockRpc.call.mock.calls.find((c) => c[0] === 'onboarding.router.configure')
      expect(call).toBeTruthy()
      expect(call![1]).toMatchObject({
        mode: 'recommended',
        strategy: 'pilot-v1',
        defaultTier: 'c1',
      })
      // image_model row stamped.
      expect((call![1] as { tiers: Record<string, unknown> }).tiers.image_model).toMatchObject({
        image_only: true,
        supportsImage: true,
      })
    })
  })

  it('router preview uses the drafted provider chosen (unsaved) in the Provider step', async () => {
    // No configured provider yet: config.llm carries no provider, needsOnboarding.
    const noProviderConfig = { ...CONFIG, llm: {} }
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
      if (method === 'onboarding.status')
        return Promise.resolve(
          statusFor({ needsOnboarding: true, sectionDetails: { llm: { status: 'missing' } } }),
        )
      if (method === 'config.get') return Promise.resolve(noProviderConfig)
      return Promise.resolve({})
    })
    renderPage()
    // Starts on the provider step (llm missing).
    await waitFor(() => expect(screen.getByLabelText('Provider')).toBeInTheDocument())

    // Router step, before any pick: no configured provider → "Choose a provider first".
    fireEvent.click(screen.getByRole('button', { name: /^Router Tiers:/ }))
    await waitFor(() =>
      expect(screen.getByText(/Choose a provider first to preview/)).toBeInTheDocument(),
    )

    // Back to provider, pick openai (draft only — do NOT save).
    fireEvent.click(screen.getByRole('button', { name: /^Provider:/ }))
    await waitFor(() => expect(screen.getByLabelText('Provider')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })

    // Router step now previews the drafted provider's tiers (no save happened).
    fireEvent.click(screen.getByRole('button', { name: /^Router Tiers:/ }))
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Router Tiers' })).toBeInTheDocument(),
    )
    // The tier table renders (drafted openai profile), not the empty-provider warning.
    expect(screen.queryByText(/Choose a provider first to preview/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('c1 model')).toBeInTheDocument()
    // Summary line reflects the drafted provider.
    expect(screen.getByText('openai / Route c1')).toBeInTheDocument()
    // Save is still gated on the provider being saved (draft ≠ configured).
    expect(screen.getByText(/Save the provider before saving router tiers/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save Router' })).toBeDisabled()

    // No provider.configure was sent — the draft never triggered a save.
    expect(mockRpc.call.mock.calls.map((c) => c[0])).not.toContain('onboarding.provider.configure')
  })

  it('search save calls onboarding.search.configure; brave reveals the masked key field', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save web search' })).toBeInTheDocument(),
    )
    // switch to brave → API key field appears, masked.
    fireEvent.change(screen.getByLabelText('Search provider'), { target: { value: 'brave' } })
    const key = screen.getByLabelText('Search API key') as HTMLInputElement
    expect(key.type).toBe('password')
    fireEvent.click(screen.getByRole('button', { name: 'Save web search' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith(
        'onboarding.search.configure',
        expect.objectContaining({ providerId: 'brave' }),
      ),
    )
  })

  it('x search save posts the card fields; a blank key is omitted so the stored one survives', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save X Search' })).toBeInTheDocument(),
    )
    const key = screen.getByLabelText('xAI API key') as HTMLInputElement
    expect(key.type).toBe('password')
    expect((screen.getByLabelText('xAI API key env') as HTMLInputElement).value).toBe('XAI_API_KEY')
    fireEvent.change(screen.getByLabelText('X Search Grok model'), {
      target: { value: 'grok-4.20-multi-agent' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save X Search' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith(
        'onboarding.x_search.configure',
        expect.objectContaining({
          enabled: true,
          model: 'grok-4.20-multi-agent',
          apiKeyEnv: 'XAI_API_KEY',
        }),
      ),
    )
    const params = mockRpc.call.mock.calls.find(
      (c: unknown[]) => c[0] === 'onboarding.x_search.configure',
    )?.[1] as Record<string, unknown>
    expect(params).not.toHaveProperty('apiKey')
  })

  it('x search card reports which xAI credential is in play', async () => {
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
      if (method === 'onboarding.status') return Promise.resolve(statusFor())
      if (method === 'config.get') return Promise.resolve(CONFIG)
      if (method === 'auth.status')
        return Promise.resolve({ xai: { logged_in: true, has_refresh_token: true } })
      if (method === 'doctor.memory.status') return Promise.resolve(null)
      return Promise.resolve({})
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(
        screen.getByText('Signed in to xAI — x_search will use that subscription.'),
      ).toBeInTheDocument(),
    )
  })

  it('x search card names the api key and offers sign-in when not signed in', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() => expect(screen.getByText(/Not signed in to xAI/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Sign in with xAI' })).toBeInTheDocument()
  })

  it('reads the xAI login even when mounted from a Settings snapshot', async () => {
    // How the real app mounts this page. Provider logins are not part of the
    // config snapshot, so gating that read alongside the config reads made
    // every signed-in user render as signed out.
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'auth.status')
        return Promise.resolve({ xai: { logged_in: true, has_refresh_token: true } })
      if (method === 'doctor.memory.status') return Promise.resolve(null)
      return Promise.resolve({})
    })
    renderPage({
      embedded: true,
      externalSnapshot: { catalog: CATALOG, status: statusFor(), config: CONFIG },
    })
    await waitFor(() =>
      expect(
        screen.getByText('Signed in to xAI — x_search will use that subscription.'),
      ).toBeInTheDocument(),
    )
    expect(mockRpc.call.mock.calls.map((c: unknown[]) => c[0])).toContain('auth.status')
  })

  it('xai sign-in shows the code, polls, and refreshes the credential line', async () => {
    let signedIn = false
    let polls = 0
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
      if (method === 'onboarding.status') return Promise.resolve(statusFor())
      if (method === 'config.get') return Promise.resolve(CONFIG)
      if (method === 'auth.status')
        return Promise.resolve({ xai: { logged_in: signedIn, has_refresh_token: signedIn } })
      if (method === 'auth.xai.login.start')
        return Promise.resolve({
          loginId: 'login-1',
          verificationUri: 'https://accounts.x.ai/oauth2/device?code=ABCD',
          userCode: 'ABCD-EFGH',
          interval: 0,
        })
      if (method === 'auth.xai.login.poll') {
        polls += 1
        // Approval never lands on the first poll in practice.
        if (polls < 2) return Promise.resolve({ status: 'pending', interval: 0 })
        signedIn = true
        return Promise.resolve({ status: 'complete' })
      }
      return Promise.resolve({})
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with xAI' }))

    // The operator needs both halves before they can approve anything.
    expect(await screen.findByText('ABCD-EFGH')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the xAI approval page' })).toHaveAttribute(
      'href',
      'https://accounts.x.ai/oauth2/device?code=ABCD',
    )

    await waitFor(
      () =>
        expect(
          screen.getByText('Signed in to xAI — x_search will use that subscription.'),
        ).toBeInTheDocument(),
      { timeout: 5000 },
    )
    expect(polls).toBeGreaterThanOrEqual(2)
  })

  it('xai sign-in cancel abandons the in-flight poll so an expiry does not paint an error', async () => {
    // Cancel used to only reset the visible phase — the poll loop kept running,
    // so a later server-side expiry painted an error on the idle card the
    // operator had already dismissed.
    let resolvePoll: ((value: { status: string }) => void) | undefined
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
      if (method === 'onboarding.status') return Promise.resolve(statusFor())
      if (method === 'config.get') return Promise.resolve(CONFIG)
      if (method === 'auth.status')
        return Promise.resolve({ xai: { logged_in: false, has_refresh_token: false } })
      if (method === 'auth.xai.login.start')
        return Promise.resolve({
          loginId: 'login-1',
          verificationUri: 'https://accounts.x.ai/oauth2/device?code=ABCD',
          userCode: 'ABCD-EFGH',
          interval: 0,
        })
      if (method === 'auth.xai.login.poll')
        return new Promise((resolve) => {
          resolvePoll = resolve
        })
      return Promise.resolve({})
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with xAI' }))
    expect(await screen.findByText('ABCD-EFGH')).toBeInTheDocument()
    await waitFor(() => expect(resolvePoll).toBeDefined())

    fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }))
    expect(screen.getByRole('button', { name: 'Sign in with xAI' })).toBeInTheDocument()
    expect(screen.queryByText('ABCD-EFGH')).not.toBeInTheDocument()

    resolvePoll!({ status: 'expired' })
    await new Promise((resolve) => window.setTimeout(resolve, 50))
    expect(screen.queryByText(/The sign-in code expired/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Sign-in failed:/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in with xAI' })).toBeInTheDocument()
  })

  it('xai sign-in cancel then restart is not clobbered by the abandoned poll', async () => {
    // A restarted sign-in used to race the old loop's completion, which wiped
    // the fresh awaiting UI (code + approval link) mid-login.
    const polls: Array<(value: { status: string }) => void> = []
    let starts = 0
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
      if (method === 'onboarding.status') return Promise.resolve(statusFor())
      if (method === 'config.get') return Promise.resolve(CONFIG)
      if (method === 'auth.status')
        return Promise.resolve({ xai: { logged_in: false, has_refresh_token: false } })
      if (method === 'auth.xai.login.start') {
        starts += 1
        return Promise.resolve({
          loginId: `login-${starts}`,
          verificationUri: `https://accounts.x.ai/oauth2/device?code=${starts}`,
          userCode: starts === 1 ? 'AAAA-AAAA' : 'BBBB-BBBB',
          interval: 0,
        })
      }
      if (method === 'auth.xai.login.poll')
        return new Promise((resolve) => {
          polls.push(resolve)
        })
      return Promise.resolve({})
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with xAI' }))
    expect(await screen.findByText('AAAA-AAAA')).toBeInTheDocument()
    await waitFor(() => expect(polls.length).toBe(1))

    fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with xAI' }))
    expect(await screen.findByText('BBBB-BBBB')).toBeInTheDocument()
    await waitFor(() => expect(polls.length).toBe(2))

    polls[0]!({ status: 'complete' })
    await new Promise((resolve) => window.setTimeout(resolve, 50))
    expect(screen.getByText('BBBB-BBBB')).toBeInTheDocument()
    expect(screen.queryByText('AAAA-AAAA')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel sign-in' })).toBeInTheDocument()
    expect(vi.mocked(toast.info)).not.toHaveBeenCalled()
  })

  it('xai sign-in surfaces a start failure instead of hanging on a spinner', async () => {
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
      if (method === 'onboarding.status') return Promise.resolve(statusFor())
      if (method === 'config.get') return Promise.resolve(CONFIG)
      if (method === 'auth.xai.login.start')
        return Promise.reject(new Error('xAI OIDC discovery failed'))
      return Promise.resolve({})
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with xAI' }))

    await waitFor(() => expect(screen.getByText(/xAI OIDC discovery failed/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Sign in with xAI' })).toBeEnabled()
  })

  it('a failed sign-out is not reported as a failed sign-in', async () => {
    // Both paths shared one label, so a stale gateway rejecting the logout RPC
    // told the operator their sign-in had failed.
    mockRpc.call.mockImplementation((method: string) => {
      if (method === 'onboarding.catalog') return Promise.resolve(CATALOG)
      if (method === 'onboarding.status') return Promise.resolve(statusFor())
      if (method === 'config.get') return Promise.resolve(CONFIG)
      if (method === 'auth.status')
        return Promise.resolve({ xai: { logged_in: true, has_refresh_token: true } })
      if (method === 'auth.xai.logout')
        return Promise.reject(new Error('Method not found: auth.xai.logout'))
      return Promise.resolve({})
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out of xAI' }))

    await waitFor(() => expect(screen.getByText(/Sign-out failed:/)).toBeInTheDocument())
    expect(screen.queryByText(/Sign-in failed:/)).not.toBeInTheDocument()
  })

  it('switching search provider re-seeds api_key_env to the new provider envKey', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save web search' })).toBeInTheDocument(),
    )
    // Start on duckduckgo (no key). Switch to brave WITHOUT typing an env name.
    fireEvent.change(screen.getByLabelText('Search provider'), { target: { value: 'brave' } })
    // The env field is re-seeded to BRAVE_API_KEY (legacy _syncSearchProviderKeyControls).
    const envInput = screen.getByLabelText('Search API key env') as HTMLInputElement
    expect(envInput.value).toBe('BRAVE_API_KEY')
    fireEvent.click(screen.getByRole('button', { name: 'Save web search' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith(
        'onboarding.search.configure',
        expect.objectContaining({ providerId: 'brave', apiKeyEnv: 'BRAVE_API_KEY' }),
      ),
    )
  })

  it('a hand-typed search api_key_env is preserved across a provider switch', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save web search' })).toBeInTheDocument(),
    )
    // Reveal the key fields, type a custom env name, then switch provider.
    fireEvent.change(screen.getByLabelText('Search provider'), { target: { value: 'brave' } })
    const envInput = screen.getByLabelText('Search API key env') as HTMLInputElement
    fireEvent.change(envInput, { target: { value: 'MY_CUSTOM_KEY' } })
    // Switch away and back — the user's typed value is NOT clobbered by the reseed.
    fireEvent.change(screen.getByLabelText('Search provider'), { target: { value: 'duckduckgo' } })
    fireEvent.change(screen.getByLabelText('Search provider'), { target: { value: 'brave' } })
    expect((screen.getByLabelText('Search API key env') as HTMLInputElement).value).toBe(
      'MY_CUSTOM_KEY',
    )
  })

  it('memory embedding save calls onboarding.memory_embedding.configure', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save memory embedding' })).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save memory embedding' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith(
        'onboarding.memory_embedding.configure',
        expect.objectContaining({ providerId: 'auto' }),
      ),
    )
  })

  it('memory settings save calls config.patch with the memory patches', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save memory settings' })).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save memory settings' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith('config.patch', {
        patches: expect.objectContaining({
          'memory.curated_memory_char_limit': 4000,
          'memory.inject_limit': 6400,
        }),
      }),
    )
  })

  it('image save calls onboarding.imageGeneration.configure with enabled + masked key', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save image generation' })).toBeInTheDocument(),
    )
    const key = screen.getByLabelText('Image API key') as HTMLInputElement
    expect(key.type).toBe('password')
    fireEvent.click(screen.getByRole('button', { name: 'Save image generation' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith(
        'onboarding.imageGeneration.configure',
        expect.objectContaining({ providerId: 'openrouter', enabled: true }),
      ),
    )
  })

  it('audio save calls onboarding.audio.configure', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Capabilities:/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save voice audio' })).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save voice audio' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith(
        'onboarding.audio.configure',
        expect.objectContaining({ providerId: 'elevenlabs', enabled: false }),
      ),
    )
  })

  it('finish shows the summary + CLI recipes and saves the update preference', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Finish:/ }))
    // CLI recipe command present (config-arg quoted path).
    await waitFor(() =>
      expect(
        screen.getByText('agentos onboard catalog providers --config /tmp/agentos.toml'),
      ).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save update preference' }))
    await waitFor(() =>
      expect(mockRpc.call).toHaveBeenCalledWith('config.patch', {
        patches: { 'updates.notify': true },
      }),
    )
  })

  it('blocks a dirty guided draft when a previously revisionless snapshot advances', async () => {
    wireCalls()
    const status = statusFor({
      needsOnboarding: true,
      sectionDetails: { llm: { status: 'missing', label: 'Provider', required: true } },
    })
    const firstProps: ComponentProps<typeof SetupPage> = {
      embedded: true,
      externalSnapshot: { catalog: CATALOG, status, config: CONFIG },
    }
    const view = renderPage(firstProps)
    const providerPanel = (await screen.findByRole('heading', { name: 'Provider' })).closest(
      'section',
    )!
    const keyInput = within(providerPanel).getByLabelText('API key') as HTMLInputElement
    fireEvent.change(keyInput, { target: { value: 'stale-secret' } })

    view.rerenderPage({
      ...firstProps,
      externalSnapshot: {
        catalog: CATALOG,
        status,
        config: CONFIG,
        revision: 'revision-b',
      },
    })

    expect(
      await screen.findByText(/configuration changed while a guided draft was open/i),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save Provider' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save Provider' }))
    expect(mockRpc.call).not.toHaveBeenCalledWith(
      'onboarding.provider.configure',
      expect.anything(),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Discard stale drafts' }))
    await waitFor(() => {
      const latestProviderPanel = screen
        .getByRole('heading', { name: 'Provider' })
        .closest('section')!
      expect(within(latestProviderPanel).getByLabelText('API key')).toHaveValue('')
    })
    expect(screen.getByRole('button', { name: 'Save Provider' })).toBeEnabled()
  })

  it('clears the saved secret even when refresh fails and preserves another capability draft', async () => {
    wireCalls()
    const onSnapshotReload = vi.fn().mockRejectedValue(new Error('refresh offline'))
    renderPage({
      embedded: true,
      externalSnapshot: {
        catalog: CATALOG,
        status: statusFor({
          needsOnboarding: true,
          sectionDetails: { llm: { status: 'missing', label: 'Provider', required: true } },
        }),
        config: CONFIG,
        revision: 'revision-a',
      },
      onSnapshotReload,
    })

    fireEvent.click(await screen.findByRole('button', { name: /^Capabilities:/ }))
    const searchDraft = (await screen.findByLabelText('Search max results')) as HTMLInputElement
    fireEvent.change(searchDraft, { target: { value: '9' } })
    fireEvent.click(screen.getByRole('button', { name: /^Provider:/ }))
    const providerPanel = (await screen.findByRole('heading', { name: 'Provider' })).closest(
      'section',
    )!
    const providerSecret = within(providerPanel).getByLabelText('API key') as HTMLInputElement
    fireEvent.change(providerSecret, { target: { value: 'one-time-secret' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save Provider' }))
    await waitFor(() => expect(onSnapshotReload).toHaveBeenCalled())
    await waitFor(() => {
      const latestProviderPanel = screen
        .getByRole('heading', { name: 'Provider' })
        .closest('section')!
      expect(within(latestProviderPanel).getByLabelText('API key')).toHaveValue('')
    })
    expect(screen.getByLabelText('Search max results')).toHaveValue(9)
    expect(toast.warning).toHaveBeenCalledWith(
      expect.stringContaining('could not be refreshed'),
      expect.anything(),
    )
  })

  it('disables guided writes when the shared snapshot is write-blocked', async () => {
    wireCalls()
    renderPage({
      embedded: true,
      externalSnapshot: {
        catalog: CATALOG,
        status: statusFor({
          needsOnboarding: true,
          sectionDetails: { llm: { status: 'missing', label: 'Provider', required: true } },
        }),
        config: CONFIG,
        diskDiverged: true,
        writeBlocked: true,
      },
    })

    expect(await screen.findByRole('button', { name: 'Save Provider' })).toBeDisabled()
  })

  it('exit setup navigates to overview', async () => {
    wireCalls()
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Exit setup and return to Overview' }))
    expect(navigateSpy).toHaveBeenCalledWith('/overview')
  })

  it('clicking a reason row jumps to its step', async () => {
    wireCalls(
      statusFor({
        needsOnboarding: true,
        llmSource: 'missing_env',
        sectionDetails: { channels: { status: 'missing', label: 'Channels' } },
      }),
    )
    renderPage()
    await waitFor(() => expect(screen.getByText('Setup')).toBeInTheDocument())
    // A blocking reason for the missing env key is shown.
    const reasons = screen.getByRole('list', { name: /Setup actions needed|Optional improvements/ })
    expect(within(reasons).getByText(/is not visible/)).toBeInTheDocument()
    fireEvent.click(within(reasons).getByText('Channels setup needed'))
    expect(navigateSpy).toHaveBeenCalledWith('/channels?view=setup')
  })
})
