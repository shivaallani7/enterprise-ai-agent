import React, { useEffect, useState } from 'react'
import { api, UserProfile, SessionSummary } from '../lib/api'

const PERSONAS = [
  { value: 'general',           label: 'General' },
  { value: 'frontend_engineer', label: 'Frontend Engineer' },
  { value: 'backend_engineer',  label: 'Backend Engineer' },
  { value: 'tech_lead',         label: 'Tech Lead' },
  { value: 'qa_engineer',       label: 'QA Engineer' },
  { value: 'product_manager',   label: 'Product Manager' },
  { value: 'devops_engineer',   label: 'DevOps Engineer' },
]

const PERSONA_DESCRIPTIONS: Record<string, string> = {
  general:           'Balanced engineering responses',
  frontend_engineer: 'Focus on UI, React, TypeScript, CSS',
  backend_engineer:  'Focus on APIs, services, databases',
  tech_lead:         'Architecture, trade-offs, code quality',
  qa_engineer:       'Test coverage, edge cases, AC gaps',
  product_manager:   'Plain language, business impact',
  devops_engineer:   'CI/CD, infra, containers, observability',
}

interface Props {
  onClose: () => void
  onPersonaChange: (persona: string, label: string) => void
}

export function ProfileModal({ onClose, onPersonaChange }: Props) {
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [name, setName] = useState('')
  const [persona, setPersona] = useState('general')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<'profile' | 'history'>('profile')

  const loadSessions = () => {
    setSessionsLoading(true)
    api.getMySessions()
      .then(r => setSessions(r.sessions))
      .catch(() => {})
      .finally(() => setSessionsLoading(false))
  }

  useEffect(() => {
    api.getProfile().then(p => {
      setProfile(p)
      setName(p.name)
      setPersona(p.persona)
    }).catch(() => setError('Could not load profile'))

    loadSessions()
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      const updated = await api.updateProfile({ name, persona })
      setProfile(updated)
      setSaved(true)
      onPersonaChange(updated.persona, updated.personaLabel)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('Failed to save changes')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div style={styles.modal}>
        <div style={styles.header}>
          <h2 style={styles.title}>My Profile</h2>
          <button style={styles.closeBtn} onClick={onClose}>✕</button>
        </div>

        <div style={styles.tabs}>
          <button
            style={{ ...styles.tab, ...(tab === 'profile' ? styles.tabActive : {}) }}
            onClick={() => setTab('profile')}
          >Profile</button>
          <button
            style={{ ...styles.tab, ...(tab === 'history' ? styles.tabActive : {}) }}
            onClick={() => { setTab('history'); loadSessions() }}
          >Session History ({sessions.length})</button>
        </div>

        {tab === 'profile' && (
          <div style={styles.body}>
            {profile && (
              <div style={styles.meta}>
                <span style={styles.email}>{profile.email}</span>
              </div>
            )}

            <label style={styles.label}>Display name</label>
            <input
              style={styles.input}
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Your name"
            />

            <label style={styles.label}>Persona</label>
            <p style={styles.hint}>
              Shapes how the agent responds — tone, focus, and which details to emphasise.
            </p>
            <div style={styles.personaGrid}>
              {PERSONAS.map(p => (
                <button
                  key={p.value}
                  style={{
                    ...styles.personaCard,
                    ...(persona === p.value ? styles.personaCardActive : {}),
                  }}
                  onClick={() => setPersona(p.value)}
                >
                  <span style={styles.personaName}>{p.label}</span>
                  <span style={styles.personaDesc}>{PERSONA_DESCRIPTIONS[p.value]}</span>
                </button>
              ))}
            </div>

            {error && <div style={styles.error}>{error}</div>}

            <div style={styles.footer}>
              <button style={styles.cancelBtn} onClick={onClose}>Cancel</button>
              <button
                style={{ ...styles.saveBtn, ...(saving ? styles.saveBtnDisabled : {}) }}
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? 'Saving…' : saved ? 'Saved!' : 'Save changes'}
              </button>
            </div>
          </div>
        )}

        {tab === 'history' && (
          <div style={styles.body}>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button style={styles.refreshBtn} onClick={loadSessions} disabled={sessionsLoading}>
                {sessionsLoading ? 'Refreshing…' : '↻ Refresh'}
              </button>
            </div>
            {sessions.length === 0 ? (
              <div style={styles.empty}>{sessionsLoading ? 'Loading…' : 'No sessions yet.'}</div>
            ) : (
              <div style={styles.sessionList}>
                {sessions.map(s => (
                  <div key={s.sessionId} style={styles.sessionCard}>
                    <div style={styles.sessionPreview}>{s.preview || '(empty session)'}</div>
                    <div style={styles.sessionMeta}>
                      <span style={styles.sessionId}>#{s.sessionId}</span>
                      <span>{s.messageCount} messages</span>
                      <span>{new Date(s.updatedAt * 1000).toLocaleString()}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed', inset: 0,
    background: 'rgba(0,0,0,0.5)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 1000,
  },
  modal: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: '12px',
    width: '560px',
    maxWidth: '95vw',
    maxHeight: '85vh',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '20px 24px 0',
  },
  title: { margin: 0, fontSize: '18px', fontWeight: 700 },
  closeBtn: {
    background: 'none', border: 'none', cursor: 'pointer',
    fontSize: '16px', color: 'var(--text-muted)', padding: '4px',
  },
  tabs: {
    display: 'flex', gap: '4px',
    padding: '12px 24px 0',
    borderBottom: '1px solid var(--border)',
  },
  tab: {
    background: 'none', border: 'none', cursor: 'pointer',
    padding: '8px 12px', fontSize: '13px', color: 'var(--text-muted)',
    borderBottom: '2px solid transparent', marginBottom: '-1px',
  },
  tabActive: {
    color: 'var(--primary)', borderBottomColor: 'var(--primary)', fontWeight: 600,
  },
  body: {
    flex: 1, overflowY: 'auto', padding: '20px 24px',
    display: 'flex', flexDirection: 'column', gap: '12px',
  },
  meta: { marginBottom: '4px' },
  email: { fontSize: '13px', color: 'var(--text-muted)' },
  label: { fontSize: '13px', fontWeight: 600, color: 'var(--text)' },
  hint: { fontSize: '12px', color: 'var(--text-muted)', margin: '-8px 0 0' },
  input: {
    padding: '8px 12px', borderRadius: 'var(--radius)',
    border: '1px solid var(--border)', background: 'var(--surface-2)',
    color: 'var(--text)', fontSize: '14px', width: '100%', boxSizing: 'border-box',
  },
  personaGrid: {
    display: 'grid', gridTemplateColumns: '1fr 1fr',
    gap: '8px',
  },
  personaCard: {
    display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
    padding: '10px 12px', borderRadius: 'var(--radius)',
    border: '1px solid var(--border)', background: 'var(--surface-2)',
    cursor: 'pointer', textAlign: 'left', gap: '2px',
  },
  personaCardActive: {
    border: '2px solid var(--primary)', background: 'color-mix(in srgb, var(--primary) 8%, var(--surface-2))',
  },
  personaName: { fontSize: '13px', fontWeight: 600, color: 'var(--text)' },
  personaDesc: { fontSize: '11px', color: 'var(--text-muted)' },
  error: {
    fontSize: '13px', color: 'var(--danger)',
    background: 'rgba(239,68,68,0.1)', padding: '8px 12px',
    borderRadius: 'var(--radius)', border: '1px solid rgba(239,68,68,0.3)',
  },
  footer: {
    display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '8px',
  },
  cancelBtn: {
    padding: '8px 16px', borderRadius: 'var(--radius)',
    border: '1px solid var(--border)', background: 'none',
    color: 'var(--text)', cursor: 'pointer', fontSize: '13px',
  },
  saveBtn: {
    padding: '8px 20px', borderRadius: 'var(--radius)',
    background: 'var(--primary)', color: '#fff',
    border: 'none', cursor: 'pointer', fontSize: '13px', fontWeight: 600,
  },
  saveBtnDisabled: { opacity: 0.6, cursor: 'not-allowed' },
  empty: { color: 'var(--text-muted)', fontSize: '13px', textAlign: 'center', padding: '20px' },
  refreshBtn: {
    fontSize: '12px', padding: '4px 10px', borderRadius: 'var(--radius)',
    border: '1px solid var(--border)', background: 'none', cursor: 'pointer',
    color: 'var(--text-muted)',
  },
  sessionList: { display: 'flex', flexDirection: 'column', gap: '8px' },
  sessionCard: {
    padding: '10px 12px', borderRadius: 'var(--radius)',
    border: '1px solid var(--border)', background: 'var(--surface-2)',
  },
  sessionPreview: { fontSize: '13px', color: 'var(--text)', marginBottom: '6px' },
  sessionMeta: {
    display: 'flex', gap: '12px',
    fontSize: '11px', color: 'var(--text-muted)',
  },
  sessionId: { fontFamily: 'var(--mono)', opacity: 0.7 },
}
