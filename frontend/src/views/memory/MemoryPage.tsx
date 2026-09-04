import './memory.css'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpenIcon,
  BrainIcon,
  DatabaseIcon,
  FileCodeIcon,
  FileTextIcon,
  FolderPlusIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  Trash2Icon,
  UserIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { useRpc } from '@/app/providers'
import { ModalShell } from '@/components/ModalShell'
import { Button } from '@/components/ui/button'
import { t } from '@/i18n'
import '@/i18n/en/memory'
import {
  formatBytes,
  formatScore,
  formatUsagePercent,
  isNearBudget,
  type CuratedMemoryData,
  type MemoryFileRow,
  type MemorySearchResultWire,
  type MemorySourceFilter,
  type MemoryStatusWire,
  type MemoryTab,
} from './logic'

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'object' && error !== null && 'message' in error) {
    return String((error as { message: unknown }).message)
  }
  return String(error)
}

export function MemoryPage() {
  const rpc = useRpc()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<MemoryTab>('curated')
  const [agentId] = useState('main')

  // Search state
  const [searchQuery, setSearchQuery] = useState('')
  const [searchSource, setSearchSource] = useState<MemorySourceFilter>('all')
  const [minScore, setMinScore] = useState(0.35)

  // Dialog states
  const [curatedDialogOpen, setCuratedDialogOpen] = useState(false)
  const [curatedDialogTarget, setCuratedDialogTarget] = useState<'memory' | 'user'>('memory')
  const [curatedEditOldText, setCuratedEditOldText] = useState<string | null>(null)
  const [curatedContent, setCuratedContent] = useState('')

  // Ingest state
  const [ingestPath, setIngestPath] = useState('')
  const [isIngesting, setIsIngesting] = useState(false)

  // Raw file viewer state
  const [viewingFilePath, setViewingFilePath] = useState<string | null>(null)
  const [viewingContent, setViewingContent] = useState<string | null>(null)

  // ── Queries ──────────────────────────────────────────────────────────────────

  const statusQuery = useQuery({
    queryKey: ['memory', 'status', agentId],
    queryFn: async () => {
      return (await rpc.call('memory.index', { agentId, force: false })) as MemoryStatusWire
    },
  })

  const curatedMemoryQuery = useQuery({
    queryKey: ['memory', 'curated', agentId, 'memory'],
    queryFn: async () => {
      return (await rpc.call('memory.curated.get', {
        agentId,
        target: 'memory',
      })) as CuratedMemoryData
    },
  })

  const curatedUserQuery = useQuery({
    queryKey: ['memory', 'curated', agentId, 'user'],
    queryFn: async () => {
      return (await rpc.call('memory.curated.get', {
        agentId,
        target: 'user',
      })) as CuratedMemoryData
    },
  })

  const kbDocsQuery = useQuery({
    queryKey: ['memory', 'kb', agentId],
    queryFn: async () => {
      const res = (await rpc.call('memory.knowledge_base.list', { agentId })) as {
        documents: MemoryFileRow[]
      }
      return res.documents || []
    },
  })

  const rawFilesQuery = useQuery({
    queryKey: ['memory', 'raw', agentId],
    queryFn: async () => {
      const res = (await rpc.call('memory.list', { agentId, source: 'all' })) as {
        files: MemoryFileRow[]
      }
      return res.files || []
    },
  })

  const searchQueryRun = useQuery({
    queryKey: ['memory', 'search', agentId, searchQuery, searchSource, minScore],
    queryFn: async () => {
      if (!searchQuery.trim()) return []
      const res = (await rpc.call('memory.search', {
        agentId,
        query: searchQuery.trim(),
        source: searchSource,
        minScore,
        limit: 15,
      })) as { results: MemorySearchResultWire[] }
      return res.results || []
    },
    enabled: searchQuery.trim().length > 0,
  })

  // ── Actions & Mutations ──────────────────────────────────────────────────────

  const handleRefresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['memory'] })
    toast.success(t('memory.refresh'))
  }

  const handleReindex = async (force: boolean) => {
    try {
      await rpc.call('memory.index', { agentId, force })
      await queryClient.invalidateQueries({ queryKey: ['memory'] })
      toast.success(force ? 'Memory rebuilt and indexed' : 'Memory synced')
    } catch (err: unknown) {
      toast.error(errorMessage(err) || 'Failed to reindex memory')
    }
  }

  const openAddCurated = (target: 'memory' | 'user') => {
    setCuratedDialogTarget(target)
    setCuratedEditOldText(null)
    setCuratedContent('')
    setCuratedDialogOpen(true)
  }

  const openEditCurated = (target: 'memory' | 'user', entry: string) => {
    setCuratedDialogTarget(target)
    setCuratedEditOldText(entry)
    setCuratedContent(entry)
    setCuratedDialogOpen(true)
  }

  const handleSaveCurated = async () => {
    const text = curatedContent.trim()
    if (!text) return
    try {
      if (curatedEditOldText !== null) {
        await rpc.call('memory.curated.replace', {
          agentId,
          target: curatedDialogTarget,
          oldText: curatedEditOldText,
          newContent: text,
        })
      } else {
        await rpc.call('memory.curated.add', {
          agentId,
          target: curatedDialogTarget,
          content: text,
        })
      }
      setCuratedDialogOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['memory', 'curated'] })
      toast.success('Curated entry saved.')
    } catch (err: unknown) {
      toast.error(errorMessage(err) || 'Failed to save curated entry')
    }
  }

  const handleRemoveCurated = async (target: 'memory' | 'user', entry: string) => {
    try {
      await rpc.call('memory.curated.remove', {
        agentId,
        target,
        oldText: entry,
      })
      await queryClient.invalidateQueries({ queryKey: ['memory', 'curated'] })
      toast.success('Entry removed.')
    } catch (err: unknown) {
      toast.error(errorMessage(err) || 'Failed to remove entry')
    }
  }

  const handleIngestPath = async () => {
    const p = ingestPath.trim()
    if (!p) return
    setIsIngesting(true)
    try {
      const res = (await rpc.call('memory.knowledge_base.ingest', {
        agentId,
        path: p,
      })) as { results: Array<{ chunksIndexed?: number }> }
      setIngestPath('')
      await queryClient.invalidateQueries({ queryKey: ['memory'] })
      const chunks = res.results?.reduce((sum, r) => sum + (r.chunksIndexed || 0), 0) || 0
      toast.success(`Ingested ${res.results?.length || 0} documents (${chunks} chunks).`)
    } catch (err: unknown) {
      toast.error(errorMessage(err) || 'Failed to ingest path')
    } finally {
      setIsIngesting(false)
    }
  }

  const handleFileUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setIsIngesting(true)
    try {
      for (const file of Array.from(files)) {
        const text = await file.text()
        await rpc.call('memory.knowledge_base.ingest', {
          agentId,
          filename: file.name,
          content: text,
        })
      }
      await queryClient.invalidateQueries({ queryKey: ['memory'] })
      toast.success(`Ingested ${files.length} file(s).`)
    } catch (err: unknown) {
      toast.error(errorMessage(err) || 'Failed to upload document')
    } finally {
      setIsIngesting(false)
    }
  }

  const handleRemoveKbDoc = async (path: string) => {
    try {
      await rpc.call('memory.knowledge_base.remove', { agentId, path })
      await queryClient.invalidateQueries({ queryKey: ['memory'] })
      toast.success(`Removed ${path}`)
    } catch (err: unknown) {
      toast.error(errorMessage(err) || 'Failed to remove document')
    }
  }

  const handleViewRaw = async (path: string) => {
    setViewingFilePath(path)
    setViewingContent('Loading…')
    try {
      const res = (await rpc.call('memory.show', { agentId, path })) as { content: string }
      setViewingContent(res.content)
    } catch (err: unknown) {
      setViewingContent(`Failed to read file: ${errorMessage(err)}`)
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  const memCurated = curatedMemoryQuery.data
  const userCurated = curatedUserQuery.data
  const kbDocs = kbDocsQuery.data || []
  const rawFiles = rawFilesQuery.data || []
  const searchResults = searchQueryRun.data || []
  const status = statusQuery.data

  return (
    <div className="mem-view">
      {/* Header */}
      <div className="mem-header">
        <div className="mem-title-group">
          <h1>{t('memory.title')}</h1>
          <p>{t('memory.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => handleReindex(false)}>
            <RefreshCwIcon className="w-4 h-4 mr-1.5" />
            {t('memory.reindex')}
          </Button>
          <Button variant="outline" size="sm" onClick={handleRefresh}>
            {t('memory.refresh')}
          </Button>
        </div>
      </div>

      {/* Stats Summary */}
      <div className="mem-stats-grid">
        <div className="mem-stat-card">
          <span className="stat-label">{t('memory.statCurated')}</span>
          <span className="stat-value">
            {(memCurated?.entries.length || 0) + (userCurated?.entries.length || 0)}
          </span>
          <span className="stat-hint">{t('memory.statCuratedHint')}</span>
        </div>
        <div className="mem-stat-card">
          <span className="stat-label">{t('memory.statDocuments')}</span>
          <span className="stat-value">{kbDocs.length}</span>
          <span className="stat-hint">{t('memory.statDocumentsHint')}</span>
        </div>
        <div className="mem-stat-card">
          <span className="stat-label">{t('memory.statChunks')}</span>
          <span className="stat-value">{status?.chunkCount ?? '-'}</span>
          <span className="stat-hint">{t('memory.statChunksHint')}</span>
        </div>
        <div className="mem-stat-card">
          <span className="stat-label">{t('memory.statStatus')}</span>
          <span className="stat-value text-sm font-medium">
            {status?.vecAvailable ? (
              <span className="text-emerald-500 font-semibold">{t('memory.statStatusVec')}</span>
            ) : (
              <span className="text-amber-500 font-semibold">{t('memory.statStatusFts')}</span>
            )}
          </span>
          <span className="stat-hint">sqlite-vec / hybrid</span>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="mem-tabs">
        <button
          className={`mem-tab-btn ${activeTab === 'curated' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('curated')}
        >
          <BrainIcon className="w-4 h-4 inline mr-1.5" />
          {t('memory.tabCurated')}
        </button>
        <button
          className={`mem-tab-btn ${activeTab === 'knowledge_base' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('knowledge_base')}
        >
          <BookOpenIcon className="w-4 h-4 inline mr-1.5" />
          {t('memory.tabKnowledgeBase')}
        </button>
        <button
          className={`mem-tab-btn ${activeTab === 'raw_sources' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('raw_sources')}
        >
          <DatabaseIcon className="w-4 h-4 inline mr-1.5" />
          {t('memory.tabRawSources')}
        </button>
        <button
          className={`mem-tab-btn ${activeTab === 'search' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('search')}
        >
          <SearchIcon className="w-4 h-4 inline mr-1.5" />
          {t('memory.tabSearch')}
        </button>
      </div>

      {/* Tab 1: Curated Memory */}
      {activeTab === 'curated' && (
        <div className="mem-curated-grid">
          {/* Agent Memory (MEMORY.md) */}
          <div className="mem-curated-panel">
            <div className="mem-curated-head">
              <div>
                <h3 className="flex items-center gap-1.5">
                  <BrainIcon className="w-4 h-4 text-blue-500" />
                  {t('memory.curatedAgentTitle')}
                </h3>
                <p>{t('memory.curatedAgentDesc')}</p>
                {memCurated && (
                  <div className="mt-2 text-xs text-muted-foreground">
                    {t('memory.curatedUsage', {
                      used: memCurated.charCount,
                      limit: memCurated.charLimit,
                      percent: formatUsagePercent(memCurated.charCount, memCurated.charLimit),
                    })}
                    <div className="mem-budget-bar">
                      <div
                        className={`mem-budget-fill ${
                          isNearBudget(memCurated.charCount, memCurated.charLimit)
                            ? 'is-near-limit'
                            : ''
                        }`}
                        style={{
                          width: `${formatUsagePercent(memCurated.charCount, memCurated.charLimit)}%`,
                        }}
                      />
                    </div>
                  </div>
                )}
              </div>
              <Button size="sm" variant="outline" onClick={() => openAddCurated('memory')}>
                <PlusIcon className="w-3.5 h-3.5 mr-1" />
                {t('memory.curatedAddEntry')}
              </Button>
            </div>

            <div className="mem-curated-list">
              {(!memCurated || memCurated.entries.length === 0) && (
                <div className="p-4 text-center text-sm text-muted-foreground">
                  {t('memory.curatedEmpty')}
                </div>
              )}
              {memCurated?.entries.map((entry, idx) => (
                <div key={idx} className="mem-entry-item">
                  <div className="mem-entry-text">{entry}</div>
                  <div className="mem-entry-actions">
                    <button
                      className="p-1 hover:text-blue-400"
                      onClick={() => openEditCurated('memory', entry)}
                      title={t('memory.curatedEditEntry')}
                    >
                      <PencilIcon className="w-3.5 h-3.5" />
                    </button>
                    <button
                      className="p-1 hover:text-rose-400"
                      onClick={() => handleRemoveCurated('memory', entry)}
                      title={t('memory.curatedRemoveEntry')}
                    >
                      <Trash2Icon className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* User Profile (USER.md) */}
          <div className="mem-curated-panel">
            <div className="mem-curated-head">
              <div>
                <h3 className="flex items-center gap-1.5">
                  <UserIcon className="w-4 h-4 text-emerald-500" />
                  {t('memory.curatedUserTitle')}
                </h3>
                <p>{t('memory.curatedUserDesc')}</p>
                {userCurated && (
                  <div className="mt-2 text-xs text-muted-foreground">
                    {t('memory.curatedUsage', {
                      used: userCurated.charCount,
                      limit: userCurated.charLimit,
                      percent: formatUsagePercent(userCurated.charCount, userCurated.charLimit),
                    })}
                    <div className="mem-budget-bar">
                      <div
                        className={`mem-budget-fill ${
                          isNearBudget(userCurated.charCount, userCurated.charLimit)
                            ? 'is-near-limit'
                            : ''
                        }`}
                        style={{
                          width: `${formatUsagePercent(userCurated.charCount, userCurated.charLimit)}%`,
                        }}
                      />
                    </div>
                  </div>
                )}
              </div>
              <Button size="sm" variant="outline" onClick={() => openAddCurated('user')}>
                <PlusIcon className="w-3.5 h-3.5 mr-1" />
                {t('memory.curatedAddEntry')}
              </Button>
            </div>

            <div className="mem-curated-list">
              {(!userCurated || userCurated.entries.length === 0) && (
                <div className="p-4 text-center text-sm text-muted-foreground">
                  {t('memory.curatedEmpty')}
                </div>
              )}
              {userCurated?.entries.map((entry, idx) => (
                <div key={idx} className="mem-entry-item">
                  <div className="mem-entry-text">{entry}</div>
                  <div className="mem-entry-actions">
                    <button
                      className="p-1 hover:text-emerald-400"
                      onClick={() => openEditCurated('user', entry)}
                      title={t('memory.curatedEditEntry')}
                    >
                      <PencilIcon className="w-3.5 h-3.5" />
                    </button>
                    <button
                      className="p-1 hover:text-rose-400"
                      onClick={() => handleRemoveCurated('user', entry)}
                      title={t('memory.curatedRemoveEntry')}
                    >
                      <Trash2Icon className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Tab 2: Knowledge Base */}
      {activeTab === 'knowledge_base' && (
        <div className="flex flex-col gap-4">
          <div className="p-4 border rounded-lg bg-surface-panel flex flex-col gap-3">
            <h3 className="font-semibold text-sm">{t('memory.kbUploadTitle')}</h3>
            <p className="text-xs text-muted-foreground">{t('memory.kbUploadDesc')}</p>
            <div className="mem-kb-upload-bar">
              <input
                type="text"
                placeholder={t('memory.kbPathPlaceholder')}
                className="flex-1 px-3 py-1.5 text-sm bg-background border rounded-md"
                value={ingestPath}
                onChange={(e) => setIngestPath(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleIngestPath()}
              />
              <Button
                size="sm"
                onClick={handleIngestPath}
                disabled={isIngesting || !ingestPath.trim()}
              >
                <FolderPlusIcon className="w-4 h-4 mr-1.5" />
                {t('memory.kbIngestPathButton')}
              </Button>
              <label className="cursor-pointer inline-flex items-center justify-center rounded-md text-sm font-medium border bg-background hover:bg-accent px-3 py-1.5">
                <FileCodeIcon className="w-4 h-4 mr-1.5" />
                Upload Files
                <input
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => handleFileUpload(e.target.files)}
                />
              </label>
            </div>
          </div>

          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface-panel text-xs text-muted-foreground border-b">
                <tr>
                  <th className="p-3">{t('memory.rawPathCol')}</th>
                  <th className="p-3 text-right">{t('memory.rawLinesCol')}</th>
                  <th className="p-3 text-right">{t('memory.rawSizeCol')}</th>
                  <th className="p-3">{t('memory.rawModifiedCol')}</th>
                  <th className="p-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {kbDocs.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="p-6 text-center text-muted-foreground text-sm">
                      {t('memory.kbEmpty')}
                    </td>
                  </tr>
                ) : (
                  kbDocs.map((doc) => (
                    <tr key={doc.path} className="border-b hover:bg-surface-panel/50">
                      <td className="p-3 font-mono text-xs">{doc.path}</td>
                      <td className="p-3 text-right text-xs">{doc.lineCount ?? '-'}</td>
                      <td className="p-3 text-right text-xs">{formatBytes(doc.sizeBytes)}</td>
                      <td className="p-3 text-xs text-muted-foreground">
                        {doc.modifiedAt ? new Date(doc.modifiedAt).toLocaleString() : '-'}
                      </td>
                      <td className="p-3 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="hover:text-rose-500"
                          onClick={() => handleRemoveKbDoc(doc.path)}
                        >
                          <Trash2Icon className="w-3.5 h-3.5" />
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tab 3: Raw Sources */}
      {activeTab === 'raw_sources' && (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-panel text-xs text-muted-foreground border-b">
              <tr>
                <th className="p-3">{t('memory.rawPathCol')}</th>
                <th className="p-3">{t('memory.rawSourceCol')}</th>
                <th className="p-3 text-right">{t('memory.rawLinesCol')}</th>
                <th className="p-3 text-right">{t('memory.rawSizeCol')}</th>
                <th className="p-3">{t('memory.rawModifiedCol')}</th>
                <th className="p-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {rawFiles.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-6 text-center text-muted-foreground text-sm">
                    {t('memory.rawEmpty')}
                  </td>
                </tr>
              ) : (
                rawFiles.map((file) => (
                  <tr key={file.path} className="border-b hover:bg-surface-panel/50">
                    <td className="p-3 font-mono text-xs">{file.path}</td>
                    <td className="p-3 text-xs">
                      <span className="px-2 py-0.5 rounded-full text-[10px] uppercase font-semibold bg-blue-500/10 text-blue-400">
                        {file.source || 'memory'}
                      </span>
                    </td>
                    <td className="p-3 text-right text-xs">{file.lineCount ?? '-'}</td>
                    <td className="p-3 text-right text-xs">{formatBytes(file.sizeBytes)}</td>
                    <td className="p-3 text-xs text-muted-foreground">
                      {file.modifiedAt ? new Date(file.modifiedAt).toLocaleString() : '-'}
                    </td>
                    <td className="p-3 text-right">
                      <Button variant="outline" size="sm" onClick={() => handleViewRaw(file.path)}>
                        <FileTextIcon className="w-3.5 h-3.5 mr-1" />
                        {t('memory.rawViewContent')}
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Tab 4: Search */}
      {activeTab === 'search' && (
        <div className="flex flex-col gap-4">
          <div className="p-4 border rounded-lg bg-surface-panel flex flex-col gap-3">
            <div className="flex gap-2">
              <input
                type="text"
                placeholder={t('memory.searchPlaceholder')}
                className="flex-1 px-3 py-2 bg-background border rounded-md text-sm"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
            <div className="flex gap-4 items-center flex-wrap">
              <div className="flex gap-2 items-center">
                <span className="text-xs text-muted-foreground">Filter Source:</span>
                {(['all', 'memory', 'knowledge_base', 'sessions'] as MemorySourceFilter[]).map(
                  (src) => (
                    <button
                      key={src}
                      className={`px-2.5 py-1 text-xs rounded-full border ${
                        searchSource === src
                          ? 'bg-blue-500/20 text-blue-400 border-blue-500/30'
                          : 'bg-surface hover:bg-surface-hover text-muted-foreground border-transparent'
                      }`}
                      onClick={() => setSearchSource(src)}
                    >
                      {src === 'all'
                        ? t('memory.searchSourceAll')
                        : src === 'memory'
                          ? t('memory.searchSourceMemory')
                          : src === 'knowledge_base'
                            ? t('memory.searchSourceKb')
                            : t('memory.searchSourceSessions')}
                    </button>
                  ),
                )}
              </div>
              <div className="flex gap-2 items-center ml-auto">
                <span className="text-xs text-muted-foreground">
                  Min Score: {formatScore(minScore)}
                </span>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={minScore}
                  onChange={(e) => setMinScore(parseFloat(e.target.value))}
                  className="w-24 h-1.5 accent-blue-500 cursor-pointer"
                />
              </div>
            </div>
          </div>

          <div className="mem-search-results">
            {searchQuery.trim() && searchResults.length === 0 && !searchQueryRun.isLoading && (
              <div className="p-6 text-center text-sm text-muted-foreground">
                {t('memory.searchNoResults')}
              </div>
            )}
            {searchResults.map((res, idx) => (
              <div key={idx} className="mem-search-card">
                <div className="mem-search-card-head">
                  <span className="font-mono">{res.path}</span>
                  <div className="flex gap-2 items-center">
                    <span className="px-2 py-0.5 rounded-full text-[10px] uppercase font-semibold bg-emerald-500/10 text-emerald-400">
                      {t('memory.searchResultScore', { score: formatScore(res.score) })}
                    </span>
                    {res.startLine !== undefined && res.endLine !== undefined && (
                      <span className="text-xs">
                        {t('memory.searchResultLines', { start: res.startLine, end: res.endLine })}
                      </span>
                    )}
                  </div>
                </div>
                <div className="mem-search-snippet">{res.snippet}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Curated Entry Dialog */}
      {curatedDialogOpen && (
        <ModalShell
          role="dialog"
          labelledBy="curated-dialog-title"
          overlayClassName="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
          className="bg-background border rounded-lg max-w-lg w-full overflow-hidden shadow-xl"
          onClose={() => setCuratedDialogOpen(false)}
        >
          <div className="p-4 flex flex-col gap-4">
            <h2 id="curated-dialog-title" className="text-base font-semibold">
              {curatedEditOldText !== null
                ? t('memory.dialogEditTitle')
                : t('memory.dialogAddTitle')}
            </h2>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">
                {t('memory.dialogTargetLabel')}
              </label>
              <span className="font-semibold text-sm">
                {curatedDialogTarget === 'memory' ? 'MEMORY.md (Agent)' : 'USER.md (User Profile)'}
              </span>
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">
                {t('memory.dialogContentLabel')}
              </label>
              <textarea
                className="w-full h-32 p-2.5 text-sm bg-background border rounded-md focus:outline-none"
                placeholder={t('memory.dialogContentPlaceholder')}
                value={curatedContent}
                onChange={(e) => setCuratedContent(e.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2 mt-2">
              <Button variant="outline" onClick={() => setCuratedDialogOpen(false)}>
                {t('memory.dialogCancel')}
              </Button>
              <Button onClick={handleSaveCurated} disabled={!curatedContent.trim()}>
                {t('memory.dialogSave')}
              </Button>
            </div>
          </div>
        </ModalShell>
      )}

      {/* Raw File Content Viewer Modal */}
      {viewingFilePath && (
        <ModalShell
          role="dialog"
          labelledBy="raw-dialog-title"
          overlayClassName="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
          className="bg-background border rounded-lg max-w-2xl w-full overflow-hidden shadow-xl"
          onClose={() => setViewingFilePath(null)}
        >
          <div className="p-4 flex flex-col gap-3">
            <h2 id="raw-dialog-title" className="text-base font-semibold">
              {t('memory.rawContentTitle', { path: viewingFilePath })}
            </h2>
            <pre className="p-3 bg-black/40 rounded-md font-mono text-xs max-h-[60vh] overflow-auto whitespace-pre-wrap">
              {viewingContent}
            </pre>
            <div className="flex justify-end">
              <Button variant="outline" onClick={() => setViewingFilePath(null)}>
                {t('memory.rawCloseInspector')}
              </Button>
            </div>
          </div>
        </ModalShell>
      )}
    </div>
  )
}
