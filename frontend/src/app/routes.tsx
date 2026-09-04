import { lazy as reactLazy, Suspense, useEffect } from 'react'
import { type RouteObject, useLocation } from 'react-router'
import { t, type MessageKey } from '@/i18n'
import { RouteErrorBoundary } from './RouteErrorBoundary'

type LazyRoute = NonNullable<RouteObject['lazy']>

interface ViewRoute {
  path: string
  titleKey: MessageKey
  lazy: LazyRoute
}

const loadOverview: LazyRoute = async () => ({
  Component: (await import('@/views/overview/OverviewPage')).OverviewPage,
})
const loadHealth: LazyRoute = async () => ({
  Component: (await import('@/views/health/HealthPage')).HealthPage,
})
const loadChat: LazyRoute = async () => ({
  Component: (await import('@/views/chat/ChatPage')).ChatPage,
})
const loadSessions: LazyRoute = async () => ({
  Component: (await import('@/views/sessions/SessionsPage')).SessionsPage,
})
const loadProjects: LazyRoute = async () => ({
  Component: (await import('@/views/projects/ProjectsPage')).ProjectsPage,
})
const loadAgents: LazyRoute = async () => ({
  Component: (await import('@/views/agents/AgentsPage')).AgentsPage,
})
const loadCron: LazyRoute = async () => ({
  Component: (await import('@/views/cron/CronPage')).CronPage,
})
const loadUsage: LazyRoute = async () => ({
  Component: (await import('@/views/usage/UsagePage')).UsagePage,
})
const loadSettings: LazyRoute = async () => ({
  Component: (await import('@/views/settings/SettingsPage')).SettingsPage,
})
const loadChannels: LazyRoute = async () => ({
  Component: (await import('@/views/channels/ChannelsPage')).ChannelsPage,
})
const loadMcp: LazyRoute = async () => ({
  Component: (await import('@/views/mcp/McpPage')).McpPage,
})
const loadApprovals: LazyRoute = async () => ({
  Component: (await import('@/views/approvals/ApprovalsPage')).ApprovalsPage,
})
const loadSkills: LazyRoute = async () => ({
  Component: (await import('@/views/skills/SkillsPage')).SkillsPage,
})
const loadLogs: LazyRoute = async () => ({
  Component: (await import('@/views/logs/LogsPage')).LogsPage,
})
const loadMemory: LazyRoute = async () => ({
  Component: (await import('@/views/memory/MemoryPage')).MemoryPage,
})
const loadEnv: LazyRoute = async () => ({
  Component: (await import('@/views/env/EnvPage')).EnvPage,
})

// The table holds catalog *keys*, never resolved strings: a route table is a
// module constant, so a title resolved here would freeze at module-evaluation
// time and survive a later locale change (#258).
const VIEW_ROUTES: ReadonlyArray<ViewRoute> = [
  { path: 'overview', titleKey: 'shell.viewOverview', lazy: loadOverview },
  { path: 'health', titleKey: 'shell.viewHealth', lazy: loadHealth },
  { path: 'chat', titleKey: 'shell.viewChat', lazy: loadChat },
  { path: 'projects', titleKey: 'shell.viewProjects', lazy: loadProjects },
  { path: 'sessions', titleKey: 'shell.viewSessions', lazy: loadSessions },
  { path: 'agents', titleKey: 'shell.viewAgents', lazy: loadAgents },
  { path: 'cron', titleKey: 'shell.viewCron', lazy: loadCron },
  { path: 'usage', titleKey: 'shell.viewUsage', lazy: loadUsage },
  { path: 'memory', titleKey: 'shell.viewMemory', lazy: loadMemory },
  { path: 'settings', titleKey: 'shell.viewSettings', lazy: loadSettings },
  { path: 'config', titleKey: 'shell.viewConfig', lazy: loadSettings },
  { path: 'setup', titleKey: 'shell.viewSetup', lazy: loadSettings },
  { path: 'channels', titleKey: 'shell.viewChannels', lazy: loadChannels },
  { path: 'mcp', titleKey: 'shell.viewMcp', lazy: loadMcp },
  { path: 'approvals', titleKey: 'shell.viewApprovals', lazy: loadApprovals },
  { path: 'skills', titleKey: 'shell.viewSkills', lazy: loadSkills },
  { path: 'env', titleKey: 'shell.viewEnv', lazy: loadEnv },
  { path: 'logs', titleKey: 'shell.viewLogs', lazy: loadLogs },
]

/** Resolved per call, so the titles follow the active locale. */
export function getViews(): ReadonlyArray<{ path: string; title: string }> {
  return VIEW_ROUTES.map(({ path, titleKey }) => ({ path, title: t(titleKey) }))
}

/**
 * Parity: js/router.js:32 — evaluated per resolve, not once at module load.
 * Mobile (<=768px) lands on chat, desktop on overview. Legacy re-reads
 * matchMedia inside `_resolve()` on every navigation, so a viewport change
 * that crosses the breakpoint before the index is (re)visited is honored.
 */
export function defaultViewPath(): string {
  try {
    return window.matchMedia('(max-width: 768px)').matches ? 'chat' : 'overview'
  } catch {
    return 'overview'
  }
}

// The index route must choose again whenever it is entered, so it cannot use a
// route.lazy function whose resolved module React Router caches. React.lazy
// still keeps both heavy views outside the entry bundle while preserving the
// legacy per-navigation desktop/mobile decision.
const IndexOverview = reactLazy(async () => ({
  default: (await import('@/views/overview/OverviewPage')).OverviewPage,
}))
const IndexChat = reactLazy(async () => ({
  default: (await import('@/views/chat/ChatPage')).ChatPage,
}))

function IndexView() {
  const Component = defaultViewPath() === 'chat' ? IndexChat : IndexOverview
  return (
    <Suspense fallback={<RoutePending />}>
      <Component />
    </Suspense>
  )
}

function NotFound() {
  // Parity: js/router.js:48-55 — path rendered as text, never HTML.
  // useLocation().pathname is basename-relative under react-router.
  const { pathname } = useLocation()
  useEffect(() => {
    document.title = t('shell.routeNotFoundTitle')
  }, [])
  return (
    <div className="p-8 text-muted-foreground">
      {t('shell.routeNotFoundBody', { path: pathname })}
    </div>
  )
}

function RoutePending() {
  return (
    <div className="p-8 text-muted-foreground" aria-hidden="true">
      {t('shell.routePending')}
    </div>
  )
}

function guarded(route: RouteObject): RouteObject {
  return {
    ...route,
    HydrateFallback: route.lazy ? RoutePending : undefined,
    errorElement: <RouteErrorBoundary />,
  }
}

export const routeChildren: RouteObject[] = [
  guarded({ index: true, Component: IndexView }),
  ...VIEW_ROUTES.map(({ path, lazy }) => guarded({ path, lazy })),
  guarded({ path: 'mcp/oauth/callback', lazy: loadMcp }),
  guarded({ path: '*', Component: NotFound }),
]
