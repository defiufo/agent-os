import { defineNamespace } from '../registry'

export const projects = defineNamespace('projects', {
  documentTitle: 'Projects - AgentOS Control',
  eyebrow: 'Chat · Projects',
  title: 'Projects',
  subtitle:
    'Group chat sessions and share project knowledge — every session in a project gets its knowledge injected.',
  refresh: 'Refresh',
  refreshBusy: 'Refreshing…',
  newProject: 'New project',

  // Loading / error states.
  loadingLabel: 'Loading projects…',
  errorTitle: 'Projects failed to load',
  errorRetry: 'Retry',

  // Empty states.
  emptyTitle: 'No projects yet',
  emptyBody:
    'Create a project to group related sessions. Knowledge you write here is shared with every session in the project.',
  emptyAction: 'Create the first project',
  noSelection: 'Select a project to see its sessions and knowledge.',

  // List.
  listTitle: 'All projects',
  sessionCount_one: '{count} session',
  sessionCount_other: '{count} sessions',
  updatedAt: 'Updated {time}',

  // Create dialog.
  createTitle: 'Create project',
  nameLabel: 'Name',
  namePlaceholder: 'e.g. Token launch research',
  agentLabel: 'Default agent',
  agentHint: 'New chats in this project start with this agent. Sessions of any agent can join.',
  knowledgeLabel: 'Project knowledge',
  knowledgePlaceholder:
    'Instructions and facts shared with every session in this project (optional)…',
  knowledgeHint: 'Injected into the system prompt of every session in this project.',
  createSubmit: 'Create project',
  createSubmitBusy: 'Creating…',

  // Detail panel.
  detailSessionsTitle: 'Sessions in this project',
  agentGroupLabel: 'Agent · {id}',
  detailNoSessions: 'No sessions yet — start one below or move sessions in from the Sessions page.',
  newChatInProject: 'New chat in project',
  openChat: 'Open chat',
  renameLabel: 'Project name',
  saveName: 'Save name',
  knowledgeSave: 'Save knowledge',
  knowledgeSaved: 'Knowledge saved',
  knowledgeCounter: '{count} / {max} characters',
  unsavedChanges: 'Unsaved changes',
  deleteProject: 'Delete project',

  // Delete confirm.
  deleteTitle: 'Delete project?',
  deleteBody:
    'Sessions in this project are kept — they just become project-less and stop receiving the shared knowledge.',
  deleteConfirm: 'Delete project',

  // Toasts.
  toastLoadFailed: 'Failed to load projects: {message}',
  toastCreated: 'Project created',
  toastCreateFailed: 'Failed to create project: {message}',
  toastUpdated: 'Project updated',
  toastUpdateFailed: 'Failed to update project: {message}',
  toastUpdateConflict:
    'This project changed elsewhere — latest version loaded, your draft is kept. Review and save again.',
  toastDeleted: 'Project deleted',
  toastDeleteFailed: 'Failed to delete project: {message}',
  toastSessionCreated: 'Session created in project',
  toastSessionCreateFailed: 'Failed to start chat: {message}',
} as const)
