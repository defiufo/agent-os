import './projects.css'
import { useEffect, useId, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  FolderKanbanIcon,
  MessageSquareIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from 'lucide-react'
import { toast } from 'sonner'
import { ModalShell } from '@/components/ModalShell'
import { Button } from '@/components/ui/button'
import { useRpc } from '@/app/providers'
import { t, tPlural } from '@/i18n'
import '@/i18n/en/projects'
import type { AgentEntry, RawSession } from '@/views/sessions/logic'
import { relTimeLabel, sessionName } from '@/views/sessions/logic'
import {
  groupProjectSessionsByAgent,
  knowledgeExcerpt,
  projectAgentId,
  projectId as projectIdOf,
  projectName,
  projectSessionCount,
  sessionsInProject,
  sortProjects,
  type RawProject,
} from './logic'

interface ProjectsList {
  projects?: RawProject[]
}
interface SessionsList {
  sessions?: RawSession[]
}
interface AgentsList {
  agents?: AgentEntry[]
}

// Mirrors PROJECT_NAME_MAX_CHARS / PROJECT_KNOWLEDGE_MAX_CHARS in
// src/agentos/session/manager.py — the gateway rejects past these, so stop
// the inputs there instead of failing on save. The knowledge cap equals the
// per-turn injection ceiling: everything that saves reaches the prompt.
const PROJECT_NAME_MAX = 200
const PROJECT_KNOWLEDGE_MAX = 24_000

// ── Create-project dialog ────────────────────────────────────────────────────
function CreateProjectDialog({
  agents,
  submitting,
  onCancel,
  onSubmit,
}: {
  agents: AgentEntry[]
  submitting: boolean
  onCancel: () => void
  onSubmit: (vars: { agentId: string; name: string; knowledge: string }) => void
}) {
  const titleId = useId()
  const [name, setName] = useState('')
  const [agentId, setAgentId] = useState(() =>
    agents.some((a) => a.id === 'main') || agents.length === 0 ? 'main' : (agents[0]?.id ?? 'main'),
  )
  const [knowledge, setKnowledge] = useState('')
  const canSubmit = name.trim().length > 0 && !submitting

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    onSubmit({ agentId, name: name.trim(), knowledge })
  }

  return (
    <ModalShell
      role="dialog"
      labelledBy={titleId}
      onClose={onCancel}
      overlayClassName="proj-modal__overlay"
      className="proj-modal panel"
    >
      <form className="proj-dialog" onSubmit={submit}>
        <header className="proj-dialog__head">
          <span className="t-label">{t('projects.eyebrow')}</span>
          <h2 id={titleId} className="proj-dialog__title">
            {t('projects.createTitle')}
          </h2>
        </header>
        <div className="proj-dialog__body">
          <label className="proj-field">
            <span className="t-label">{t('projects.nameLabel')}</span>
            <input
              className="proj-input"
              autoFocus
              value={name}
              maxLength={PROJECT_NAME_MAX}
              placeholder={t('projects.namePlaceholder')}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="proj-field">
            <span className="t-label">{t('projects.agentLabel')}</span>
            <select
              className="proj-input"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
            >
              {(agents.length > 0 ? agents : [{ id: 'main', name: 'main' }]).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name || a.id}
                </option>
              ))}
            </select>
            <small className="proj-field__hint">{t('projects.agentHint')}</small>
          </label>
          <label className="proj-field">
            <span className="t-label">{t('projects.knowledgeLabel')}</span>
            <textarea
              className="proj-input proj-knowledge-input"
              rows={6}
              value={knowledge}
              maxLength={PROJECT_KNOWLEDGE_MAX}
              placeholder={t('projects.knowledgePlaceholder')}
              onChange={(e) => setKnowledge(e.target.value)}
            />
            <small className="proj-field__hint">{t('projects.knowledgeHint')}</small>
          </label>
        </div>
        <footer className="proj-dialog__foot">
          <Button type="button" variant="ghost" disabled={submitting} onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" disabled={!canSubmit}>
            {submitting ? t('projects.createSubmitBusy') : t('projects.createSubmit')}
          </Button>
        </footer>
      </form>
    </ModalShell>
  )
}

// ── Delete confirm ───────────────────────────────────────────────────────────
function DeleteProjectDialog({
  name,
  busy,
  onCancel,
  onConfirm,
}: {
  name: string
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const titleId = useId()
  const bodyId = useId()
  return (
    <ModalShell
      role="alertdialog"
      labelledBy={titleId}
      describedBy={bodyId}
      onClose={busy ? () => {} : onCancel}
      overlayClassName="proj-modal__overlay"
      className="proj-modal panel"
    >
      <header className="proj-dialog__head">
        <h2 id={titleId} className="proj-dialog__title">
          {t('projects.deleteTitle')}
        </h2>
      </header>
      <div id={bodyId} className="proj-dialog__body">
        <p>
          <strong>{name}</strong>
        </p>
        <p className="proj-dim">{t('projects.deleteBody')}</p>
      </div>
      <footer className="proj-dialog__foot">
        <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button type="button" variant="destructive" disabled={busy} onClick={onConfirm}>
          {t('projects.deleteConfirm')}
        </Button>
      </footer>
    </ModalShell>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
type Dialog = { kind: 'none' } | { kind: 'create' } | { kind: 'delete'; id: string; name: string }

export function ProjectsPage() {
  const rpc = useRpc()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = searchParams.get('project') ?? ''

  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' })
  const [nameDraft, setNameDraft] = useState<string | null>(null)
  const [knowledgeDraft, setKnowledgeDraft] = useState<string | null>(null)

  useEffect(() => {
    document.title = t('projects.documentTitle')
  }, [])

  // Live updates: the gateway broadcasts projects.changed on every project
  // CRUD (and on session moves) and sessions.changed on membership edits.
  // Without these, a second client's rename/delete stays invisible until a
  // manual Refresh. Unsubscribed on unmount (StrictMode-safe).
  useEffect(() => {
    const unsubProjects = rpc.on('projects.changed', () => {
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
    })
    const unsubSessions = rpc.on('sessions.changed', () => {
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    })
    return () => {
      unsubProjects()
      unsubSessions()
    }
  }, [rpc, queryClient])

  const projectsQuery = useQuery<RawProject[]>({
    queryKey: ['projects'],
    queryFn: async () => {
      await rpc.waitForConnection()
      const data = await rpc.call<ProjectsList>('projects.list', {})
      return data.projects ?? []
    },
    refetchOnWindowFocus: false,
  })

  // Shared cache key with SessionsPage: both views read the same list.
  const sessionsQuery = useQuery<RawSession[]>({
    queryKey: ['sessions'],
    queryFn: async () => {
      await rpc.waitForConnection()
      const data = await rpc.call<SessionsList>('sessions.list', { limit: 200 })
      return data.sessions ?? []
    },
    refetchOnWindowFocus: false,
  })

  const agentsQuery = useQuery<AgentEntry[]>({
    queryKey: ['sessions', 'agents'],
    queryFn: async () => {
      await rpc.waitForConnection()
      const data = await rpc.call<AgentsList>('agents.list', {})
      return data.agents ?? []
    },
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (projectsQuery.isError) {
      const err = projectsQuery.error
      const message = err instanceof Error ? err.message : String(err)
      toast.error(t('projects.toastLoadFailed', { message }), { id: 'projects-load-err' })
    }
  }, [projectsQuery.isError, projectsQuery.error])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['projects'] })
    void queryClient.invalidateQueries({ queryKey: ['sessions'] })
  }

  const projects = useMemo(() => sortProjects(projectsQuery.data ?? []), [projectsQuery.data])
  const allSessions = sessionsQuery.data ?? []
  const selected = projects.find((p) => projectIdOf(p) === selectedId) ?? null
  const selectedSessions = selected ? sessionsInProject(allSessions, selectedId) : []

  const selectedName = selected ? projectName(selected) : ''
  const selectedKnowledge = selected ? String(selected.knowledge ?? '') : ''

  // Drafts reset whenever the selected project changes — keyed on the URL
  // param, not on selectProject(), so browser back/forward (which changes
  // ?project= without going through the click handler) cannot leak project
  // A's draft into project B's editor. Adjusted during render (React's
  // documented pattern) rather than in an effect.
  const [draftProjectId, setDraftProjectId] = useState(selectedId)
  if (draftProjectId !== selectedId) {
    setDraftProjectId(selectedId)
    setNameDraft(null)
    setKnowledgeDraft(null)
  }

  function selectProject(id: string) {
    setSearchParams(id ? { project: id } : {}, { replace: false })
  }

  // ── Mutations ──────────────────────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: (vars: { agentId: string; name: string; knowledge: string }) =>
      rpc.call<{ project?: RawProject }>('projects.create', {
        agentId: vars.agentId,
        name: vars.name,
        knowledge: vars.knowledge,
      }),
    onSuccess: (data) => {
      toast.success(t('projects.toastCreated'), { id: 'projects-create' })
      setDialog({ kind: 'none' })
      invalidate()
      const id = data?.project ? projectIdOf(data.project) : ''
      if (id) selectProject(id)
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : String(err)
      toast.error(t('projects.toastCreateFailed', { message }), { id: 'projects-create-err' })
    },
  })

  const updateMutation = useMutation({
    mutationFn: (vars: { id: string; name?: string; knowledge?: string }) =>
      rpc.call<{ project?: RawProject }>('projects.update', {
        projectId: vars.id,
        ...(vars.name !== undefined ? { name: vars.name } : {}),
        ...(vars.knowledge !== undefined ? { knowledge: vars.knowledge } : {}),
        // Compare-and-swap: the gateway refuses the write (project.conflict)
        // if the row changed since this client last read it, instead of
        // silently clobbering another editor's save.
        ...(selected
          ? { expectedUpdatedAt: Number(selected.updated_at ?? selected.updatedAt) }
          : {}),
      }),
    onSuccess: (data, vars) => {
      toast.success(
        vars.knowledge !== undefined ? t('projects.knowledgeSaved') : t('projects.toastUpdated'),
        { id: 'projects-update' },
      )
      // Patch the cached row from the response before clearing drafts, so
      // the editor doesn't flash the stale pre-save value until the
      // invalidated refetch lands.
      const fresh = data?.project
      if (fresh) {
        queryClient.setQueryData<RawProject[]>(['projects'], (rows) =>
          rows?.map((p) => (projectIdOf(p) === vars.id ? { ...p, ...fresh } : p)),
        )
      }
      setNameDraft(null)
      setKnowledgeDraft(null)
      invalidate()
    },
    onError: (err) => {
      const code = (err as { code?: string }).code
      if (code === 'project.conflict') {
        toast.error(t('projects.toastUpdateConflict'), { id: 'projects-update-err' })
        invalidate()
        return
      }
      const message = err instanceof Error ? err.message : String(err)
      toast.error(t('projects.toastUpdateFailed', { message }), { id: 'projects-update-err' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => rpc.call('projects.delete', { projectId: id }),
    onSuccess: () => {
      toast.success(t('projects.toastDeleted'), { id: 'projects-delete' })
      setDialog({ kind: 'none' })
      selectProject('')
      invalidate()
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : String(err)
      toast.error(t('projects.toastDeleteFailed', { message }), { id: 'projects-delete-err' })
      setDialog({ kind: 'none' })
    },
  })

  // Mirrors SessionsPage's create mutation: create in project, then open chat.
  const createSessionMutation = useMutation({
    mutationFn: async (vars: { agentId: string; projectId: string }) => {
      const res = await rpc.call<{ key?: string }>('sessions.create', {
        agentId: vars.agentId,
        projectId: vars.projectId,
      })
      return { key: res?.key }
    },
    onSuccess: (res) => {
      toast.success(t('projects.toastSessionCreated'), { id: 'projects-session-create' })
      invalidate()
      if (res.key) navigate('/chat?session=' + encodeURIComponent(res.key))
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : String(err)
      toast.error(t('projects.toastSessionCreateFailed', { message }), {
        id: 'projects-session-create-err',
      })
    },
  })

  const hasProjects = projects.length > 0
  const isLoading = projectsQuery.isLoading
  const isError = projectsQuery.isError && !hasProjects

  return (
    <div className="proj-stage">
      <header className="proj-stage__header">
        <div className="proj-stage__title-block">
          <span className="t-label">{t('projects.eyebrow')}</span>
          <h1 className="t-display">{t('projects.title')}</h1>
          <p className="proj-stage__subtitle">{t('projects.subtitle')}</p>
        </div>
        <div className="proj-stage__actions">
          <Button
            variant="outline"
            title={t('projects.refresh')}
            className="text-xs uppercase tracking-[0.14em]"
            disabled={projectsQuery.isFetching}
            onClick={invalidate}
          >
            <RefreshCwIcon className={projectsQuery.isFetching ? 'proj-refresh-spin' : undefined} />
            <span>
              {projectsQuery.isFetching ? t('projects.refreshBusy') : t('projects.refresh')}
            </span>
          </Button>
          <Button
            className="text-xs uppercase tracking-[0.14em]"
            onClick={() => setDialog({ kind: 'create' })}
          >
            <PlusIcon />
            <span>{t('projects.newProject')}</span>
          </Button>
        </div>
      </header>

      {isLoading ? (
        // Distinct from the empty state: a load in flight (or a failed load
        // below) must not read as "no projects yet" with a create CTA.
        <div className="proj-empty" aria-busy="true">
          <RefreshCwIcon className="proj-empty__icon proj-refresh-spin" aria-hidden="true" />
          <div className="proj-empty__title">{t('projects.loadingLabel')}</div>
        </div>
      ) : isError ? (
        <div className="proj-empty" role="alert">
          <FolderKanbanIcon className="proj-empty__icon" aria-hidden="true" />
          <div className="proj-empty__title">{t('projects.errorTitle')}</div>
          <p className="proj-empty__msg">
            {projectsQuery.error instanceof Error
              ? projectsQuery.error.message
              : String(projectsQuery.error)}
          </p>
          <Button onClick={invalidate}>
            <RefreshCwIcon />
            <span>{t('projects.errorRetry')}</span>
          </Button>
        </div>
      ) : !hasProjects ? (
        <div className="proj-empty">
          <FolderKanbanIcon className="proj-empty__icon" aria-hidden="true" />
          <div className="proj-empty__title">{t('projects.emptyTitle')}</div>
          <p className="proj-empty__msg">{t('projects.emptyBody')}</p>
          <Button onClick={() => setDialog({ kind: 'create' })}>
            <PlusIcon />
            <span>{t('projects.emptyAction')}</span>
          </Button>
        </div>
      ) : (
        <div className="proj-split">
          <section className="proj-list" aria-label={t('projects.listTitle')}>
            <h2 className="proj-list__title t-label">{t('projects.listTitle')}</h2>
            <ul className="proj-list__items">
              {projects.map((p) => {
                const id = projectIdOf(p)
                const knowledge = String(p.knowledge ?? '')
                return (
                  <li key={id}>
                    <button
                      type="button"
                      className={`proj-card${id === selectedId ? ' is-selected' : ''}`}
                      onClick={() => selectProject(id)}
                    >
                      <span className="proj-card__name">{projectName(p)}</span>
                      <span className="proj-card__meta t-data">
                        {tPlural('projects.sessionCount', projectSessionCount(p))}
                        {' · '}
                        {p.agent_id || p.agentId || ''}
                      </span>
                      {knowledge ? (
                        <span className="proj-card__excerpt">{knowledgeExcerpt(knowledge)}</span>
                      ) : null}
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="proj-detail">
            {!selected ? (
              <div className="proj-detail__placeholder proj-dim">{t('projects.noSelection')}</div>
            ) : (
              <>
                <div className="proj-detail__head">
                  <label className="proj-field proj-field--name">
                    <span className="t-label">{t('projects.renameLabel')}</span>
                    <div className="proj-name-row">
                      <input
                        className="proj-input"
                        value={nameDraft ?? selectedName}
                        maxLength={PROJECT_NAME_MAX}
                        onChange={(e) => setNameDraft(e.target.value)}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          updateMutation.isPending ||
                          nameDraft === null ||
                          nameDraft.trim() === selectedName ||
                          nameDraft.trim() === ''
                        }
                        onClick={() =>
                          updateMutation.mutate({ id: selectedId, name: (nameDraft ?? '').trim() })
                        }
                      >
                        {t('projects.saveName')}
                      </Button>
                    </div>
                  </label>
                  {selected.updated_at != null || selected.updatedAt != null ? (
                    <span className="proj-detail__meta proj-dim t-data">
                      {t('projects.updatedAt', {
                        time: relTimeLabel(Number(selected.updated_at ?? selected.updatedAt)),
                      })}
                    </span>
                  ) : null}
                </div>

                <label className="proj-field">
                  <span className="t-label">{t('projects.knowledgeLabel')}</span>
                  <textarea
                    className="proj-input proj-knowledge-input"
                    rows={8}
                    value={knowledgeDraft ?? selectedKnowledge}
                    maxLength={PROJECT_KNOWLEDGE_MAX}
                    placeholder={t('projects.knowledgePlaceholder')}
                    onChange={(e) => setKnowledgeDraft(e.target.value)}
                  />
                  <small className="proj-field__hint">
                    {t('projects.knowledgeHint')}{' '}
                    <span className="t-data">
                      {t('projects.knowledgeCounter', {
                        count: (knowledgeDraft ?? selectedKnowledge).length,
                        max: PROJECT_KNOWLEDGE_MAX,
                      })}
                    </span>
                  </small>
                </label>
                <div className="proj-knowledge-actions">
                  {knowledgeDraft !== null && knowledgeDraft !== selectedKnowledge ? (
                    <span className="proj-dim t-data">{t('projects.unsavedChanges')}</span>
                  ) : null}
                  <Button
                    size="sm"
                    disabled={
                      updateMutation.isPending ||
                      knowledgeDraft === null ||
                      knowledgeDraft === selectedKnowledge
                    }
                    onClick={() =>
                      updateMutation.mutate({ id: selectedId, knowledge: knowledgeDraft ?? '' })
                    }
                  >
                    {t('projects.knowledgeSave')}
                  </Button>
                </div>

                <div className="proj-sessions">
                  <div className="proj-sessions__head">
                    <h3 className="t-label">{t('projects.detailSessionsTitle')}</h3>
                    <Button
                      size="sm"
                      disabled={createSessionMutation.isPending}
                      onClick={() =>
                        createSessionMutation.mutate({
                          agentId: projectAgentId(selected) || 'main',
                          projectId: selectedId,
                        })
                      }
                    >
                      <PlusIcon />
                      <span>{t('projects.newChatInProject')}</span>
                    </Button>
                  </div>
                  {selectedSessions.length === 0 ? (
                    <p className="proj-dim">{t('projects.detailNoSessions')}</p>
                  ) : (
                    // Projects are cross-agent — render the Project → Agents →
                    // Sessions tree, one bucket per agent.
                    groupProjectSessionsByAgent(selectedSessions).map((group) => (
                      <div className="proj-agent-group" key={group.agentId}>
                        <h4 className="proj-agent-group__label t-label">
                          {t('projects.agentGroupLabel', { id: group.agentId })}
                        </h4>
                        <ul className="proj-sessions__items">
                          {group.items.map((s) => {
                            const key = s.key ?? ''
                            return (
                              <li key={key} className="proj-session-row">
                                <button
                                  type="button"
                                  className="proj-session-row__key t-data"
                                  title={t('projects.openChat')}
                                  onClick={() =>
                                    navigate('/chat?session=' + encodeURIComponent(key))
                                  }
                                >
                                  <MessageSquareIcon aria-hidden="true" />
                                  <span>{sessionName(s) || key}</span>
                                </button>
                                <span className="proj-dim t-data">
                                  {s.updated_at != null ? relTimeLabel(s.updated_at) : ''}
                                </span>
                              </li>
                            )
                          })}
                        </ul>
                      </div>
                    ))
                  )}
                </div>

                <div className="proj-danger">
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() =>
                      setDialog({ kind: 'delete', id: selectedId, name: selectedName })
                    }
                  >
                    <Trash2Icon />
                    <span>{t('projects.deleteProject')}</span>
                  </Button>
                </div>
              </>
            )}
          </section>
        </div>
      )}

      {dialog.kind === 'create' ? (
        <CreateProjectDialog
          agents={agentsQuery.data ?? []}
          submitting={createMutation.isPending}
          onCancel={() => setDialog({ kind: 'none' })}
          onSubmit={(vars) => createMutation.mutate(vars)}
        />
      ) : null}

      {dialog.kind === 'delete' ? (
        <DeleteProjectDialog
          name={dialog.name}
          busy={deleteMutation.isPending}
          onCancel={() => setDialog({ kind: 'none' })}
          onConfirm={() => deleteMutation.mutate(dialog.id)}
        />
      ) : null}
    </div>
  )
}
