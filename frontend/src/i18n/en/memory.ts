import { defineNamespace } from '../registry'

export const memory = defineNamespace('memory', {
  documentTitle: 'Memory - AgentOS Control',
  eyebrow: 'Control · Memory',
  title: 'Memory',
  subtitle:
    'Inspect, search, and curate agent long-term memory, user preferences, and ingested knowledge bases.',
  refresh: 'Refresh',
  refreshBusy: 'Refreshing…',
  reindex: 'Reindex',
  reindexBusy: 'Reindexing…',
  forceReindex: 'Force Rebuild',

  // Tabs.
  tabCurated: 'Curated Memory',
  tabKnowledgeBase: 'Knowledge Base',
  tabRawSources: 'Raw Sources',
  tabSearch: 'Search',

  // Stats / Overview.
  statCurated: 'Curated entries',
  statCuratedHint: 'MEMORY.md and USER.md',
  statDocuments: 'Ingested documents',
  statDocumentsHint: 'Knowledge base files',
  statChunks: 'Indexed chunks',
  statChunksHint: 'Vector + FTS searchable',
  statStatus: 'Engine status',
  statStatusVec: 'Vector search enabled',
  statStatusFts: 'FTS-only fallback',

  // Curated section.
  curatedAgentTitle: 'Agent Memory (MEMORY.md)',
  curatedAgentDesc:
    'Durable conventions, project facts, and working decisions retained across sessions.',
  curatedUserTitle: 'User Profile (USER.md)',
  curatedUserDesc:
    'User preferences, communication styles, and identity guidance injected on every turn.',
  curatedAddEntry: 'Add Entry',
  curatedEditEntry: 'Edit Entry',
  curatedRemoveEntry: 'Remove Entry',
  curatedEmpty: 'No entries saved yet.',
  curatedUsage: '{used} / {limit} chars ({percent}%)',
  curatedBudgetNear: 'Near char budget limit',
  curatedLoadFailed: 'Failed to parse curated markdown file',

  // Add / Edit Entry Dialog.
  dialogAddTitle: 'Add Curated Entry',
  dialogEditTitle: 'Edit Curated Entry',
  dialogTargetLabel: 'Target File',
  dialogContentLabel: 'Entry Content',
  dialogContentPlaceholder: 'Write the convention or preference here…',
  dialogSave: 'Save Entry',
  dialogCancel: 'Cancel',
  dialogDeleteConfirm: 'Are you sure you want to remove this entry?',

  // Knowledge base section.
  kbUploadTitle: 'Ingest Documents',
  kbUploadDesc: 'Add documents (.pdf, .docx, .pptx, .md, .txt, code) into the knowledge base.',
  kbUploadButton: 'Ingest File or Folder',
  kbPathPlaceholder: 'Relative or absolute folder/file path…',
  kbIngestPathButton: 'Ingest Path',
  kbDropzonePrompt: 'Drop files here or click to select',
  kbIngestSuccess: 'Ingested {count} documents ({chunks} chunks indexed)',
  kbRemoveDoc: 'Remove document',
  kbRemoveDocConfirm: 'Remove {path} from the knowledge base?',
  kbEmpty: 'No documents in knowledge base yet. Ingest files to provide background context.',

  // Search section.
  searchPlaceholder: 'Search across memory, knowledge base, and sessions…',
  searchSourceAll: 'All Sources',
  searchSourceMemory: 'Curated (MEMORY.md)',
  searchSourceKb: 'Knowledge Base',
  searchSourceSessions: 'Past Sessions',
  searchMinScore: 'Min score: {score}',
  searchNoResults: 'No memory snippets matched your query.',
  searchResultScore: 'Score: {score}',
  searchResultLines: 'Lines {start}-{end}',

  // Raw files table.
  rawPathCol: 'Path',
  rawSourceCol: 'Source',
  rawLinesCol: 'Lines',
  rawSizeCol: 'Size',
  rawModifiedCol: 'Last Modified',
  rawEmpty: 'No source files indexed.',
  rawViewContent: 'View File Content',
  rawContentTitle: 'Viewing {path}',
  rawCloseInspector: 'Close Inspector',
} as const)
