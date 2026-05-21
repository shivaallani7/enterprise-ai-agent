/**
 * API client with auth token injection.
 */

let tokenProvider: (() => Promise<string>) | null = null

export function setTokenProvider(fn: () => Promise<string>) {
  tokenProvider = fn
}

export interface DevSession {
  token: string
  username: string
  display_name: string
  default_persona: string
}

export function getDevSession(): DevSession | null {
  const raw = localStorage.getItem('dev_session')
  if (!raw) return null
  try { return JSON.parse(raw) } catch { return null }
}

export function saveDevSession(session: DevSession) {
  localStorage.setItem('dev_session', JSON.stringify(session))
}

export function clearDevSession() {
  localStorage.removeItem('dev_session')
}

// Keep backwards compat for App.tsx
export function getDevUsername(): string {
  return getDevSession()?.username || ''
}

export function clearDevUsername() {
  clearDevSession()
}

async function getToken(): Promise<string> {
  if (import.meta.env.VITE_AUTH_DISABLED === 'true') {
    const session = getDevSession()
    if (!session) throw new Error('No user logged in')
    return session.token
  }
  if (!tokenProvider) throw new Error('Token provider not set')
  return tokenProvider()
}

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getToken()
  return fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...(init.headers ?? {}),
    },
  })
}

export interface UserProfile {
  sub: string
  email: string
  name: string
  persona: string
  personaLabel: string
  createdAt: number
  updatedAt: number
}

export interface SessionSummary {
  sessionId: string
  createdAt: number
  updatedAt: number
  preview: string
  messageCount: number
}

export interface IngestReport {
  doc_id: string
  file_name: string
  doc_type: string
  mode: string
  status: string
  chunks_created: number
  chunks_skipped: number
  chunks_failed: number
  embedding_model: string
  chunk_strategy: string
  chunk_size: number
  overlap_pct: number
  version: number
  processing_ms: number
  warnings: string[]
  indexed_at: string
  error: string
}

export interface IngestRegistryItem {
  id: string
  doc_id: string
  file_name: string
  doc_type: string
  chunk_count: number
  version: number
  status: string
  indexed_at: string
  updated_at: number
}

export const api = {
  async getStories() {
    const res = await apiFetch('/api/jira/stories')
    if (!res.ok) throw new Error(`Failed to fetch stories: ${res.status}`)
    return res.json()
  },

  async getStory(storyKey: string) {
    const res = await apiFetch(`/api/jira/stories/${storyKey}`)
    if (!res.ok) throw new Error(`Failed to fetch story: ${res.status}`)
    return res.json()
  },

  async getHistory(sessionId: string) {
    const res = await apiFetch(`/api/chat/history/${sessionId}`)
    if (!res.ok) throw new Error(`Failed to fetch history: ${res.status}`)
    return res.json()
  },

  async submitFeedback(payload: {
    sessionId: string
    messageId: string
    rating: number
    correction?: string
    storyId?: string | null
  }) {
    const res = await apiFetch('/api/feedback', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (!res.ok) throw new Error(`Feedback failed: ${res.status}`)
    return res.json()
  },

  async login(username: string, password: string): Promise<DevSession> {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (res.status === 401) throw new Error('Invalid username or password')
    if (!res.ok) throw new Error(`Login failed: ${res.status}`)
    return res.json()
  },

  async getProfile(): Promise<UserProfile> {
    const res = await apiFetch('/api/users/me')
    if (!res.ok) throw new Error(`Failed to fetch profile: ${res.status}`)
    return res.json()
  },

  async updateProfile(updates: { name?: string; persona?: string }): Promise<UserProfile> {
    const res = await apiFetch('/api/users/me', {
      method: 'PUT',
      body: JSON.stringify(updates),
    })
    if (!res.ok) throw new Error(`Failed to update profile: ${res.status}`)
    return res.json()
  },

  async getMySessions(): Promise<{ sessions: SessionSummary[]; count: number }> {
    const res = await apiFetch('/api/users/me/sessions')
    if (!res.ok) throw new Error(`Failed to fetch sessions: ${res.status}`)
    return res.json()
  },

  async getFeedbackTrends(days = 30) {
    const res = await apiFetch(`/api/feedback/trends?days=${days}`)
    if (!res.ok) throw new Error(`Trends failed: ${res.status}`)
    return res.json()
  },

  async ingestDocuments(files: File[]): Promise<{ reports: IngestReport[]; total: number }> {
    const token = await getToken()
    const form = new FormData()
    for (const f of files) form.append('files', f)
    const res = await fetch('/api/ingest', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    })
    if (!res.ok) throw new Error(`Ingest failed: ${res.status}`)
    return res.json()
  },

  async getIngestRegistry(): Promise<{ documents: IngestRegistryItem[]; count: number }> {
    const res = await apiFetch('/api/ingest/registry')
    if (!res.ok) throw new Error(`Registry failed: ${res.status}`)
    return res.json()
  },

  async deleteIngestEntry(filename: string): Promise<void> {
    const res = await apiFetch(`/api/ingest/registry/${encodeURIComponent(filename)}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
  },

  async chatStream(
    payload: {
      sessionId: string
      storyId: string | null
      messages: Array<{ role: string; content: string }>
    },
    signal?: AbortSignal,
  ): Promise<ReadableStream<string>> {
    const token = await getToken()
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
      signal,
    })
    if (!res.ok) throw new Error(`Chat failed: ${res.status}`)
    return res.body!.pipeThrough(new TextDecoderStream()) as ReadableStream<string>
  },
}
