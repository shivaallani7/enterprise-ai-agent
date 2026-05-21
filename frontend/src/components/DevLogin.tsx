import React, { useState } from 'react'
import { api, saveDevSession, DevSession } from '../lib/api'

interface Props {
  onLogin: (session: DevSession) => void
}

export function DevLogin({ onLogin }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) return
    setLoading(true)
    setError('')
    try {
      const session = await api.login(username.trim(), password)
      saveDevSession(session)
      onLogin(session)
    } catch (err: any) {
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.root}>
      <div style={styles.card}>
        <h1 style={styles.title}>Enterprise AI Agent</h1>
        <p style={styles.subtitle}>Sign in to continue</p>

        <form style={styles.form} onSubmit={handleSubmit}>
          <div style={styles.field}>
            <label style={styles.label}>Username</label>
            <input
              style={styles.input}
              value={username}
              onChange={e => { setUsername(e.target.value); setError('') }}
              placeholder="Enter your username"
              autoFocus
              autoComplete="username"
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Password</label>
            <input
              style={styles.input}
              type="password"
              value={password}
              onChange={e => { setPassword(e.target.value); setError('') }}
              placeholder="Enter your password"
              autoComplete="current-password"
            />
          </div>

          {error && <div style={styles.error}>{error}</div>}

          <button
            style={{ ...styles.btn, ...(loading ? styles.btnDisabled : {}) }}
            type="submit"
            disabled={loading || !username.trim() || !password}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    minHeight: '100vh',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--bg)',
  },
  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: '12px',
    padding: '40px',
    width: '380px',
    maxWidth: '95vw',
    display: 'flex', flexDirection: 'column', gap: '24px',
  },
  title: { margin: 0, fontSize: '22px', fontWeight: 700 },
  subtitle: { margin: '-16px 0 0', fontSize: '13px', color: 'var(--text-muted)' },
  form: { display: 'flex', flexDirection: 'column', gap: '16px' },
  field: { display: 'flex', flexDirection: 'column', gap: '6px' },
  label: { fontSize: '13px', fontWeight: 600, color: 'var(--text)' },
  input: {
    padding: '10px 12px', borderRadius: 'var(--radius)',
    border: '1px solid var(--border)', background: 'var(--surface-2)',
    color: 'var(--text)', fontSize: '14px', outline: 'none',
  },
  error: {
    fontSize: '13px', color: 'var(--danger)',
    background: 'rgba(239,68,68,0.08)', padding: '8px 12px',
    borderRadius: 'var(--radius)', border: '1px solid rgba(239,68,68,0.25)',
  },
  btn: {
    padding: '11px 20px', borderRadius: 'var(--radius)',
    background: 'var(--primary)', color: '#fff',
    border: 'none', cursor: 'pointer', fontSize: '14px', fontWeight: 600,
    marginTop: '4px',
  },
  btnDisabled: { opacity: 0.6, cursor: 'not-allowed' },
}
