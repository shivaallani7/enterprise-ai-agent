import React, { useState } from 'react'
import { useMsal, useIsAuthenticated } from '@azure/msal-react'
import { useJiraStories } from './hooks/useJiraStories'
import { StorySidebar } from './components/StorySidebar'
import { StoryTab } from './components/StoryTab'
import { ChatWindow } from './components/ChatWindow'
import { Dashboard } from './components/Dashboard'
import { ProfileModal } from './components/ProfileModal'
import { IngestModal } from './components/IngestModal'
import { DevLogin } from './components/DevLogin'
import { loginRequest } from './lib/authConfig'
import { setTokenProvider as setApiToken, getDevUsername, clearDevUsername, getDevSession, DevSession } from './lib/api'

// Lazy-connect MSAL token to API client
function useTokenProvider() {
  const { instance, accounts } = useMsal()
  React.useEffect(() => {
    if (accounts.length === 0) return
    setApiToken(async () => {
      const result = await instance.acquireTokenSilent({
        ...loginRequest,
        account: accounts[0],
      })
      return result.accessToken
    })
  }, [instance, accounts])
}

type ActiveTab = 'general' | 'dashboard' | string

// Set VITE_AUTH_DISABLED=true in .env.local to skip MSAL login entirely.
// The API client automatically sends "dev-token" when no token provider is set
// and VITE_AUTH_DISABLED is true, matching the backend dev-token bypass.
const AUTH_DISABLED = import.meta.env.VITE_AUTH_DISABLED === 'true'

export default function App() {
  const isAuthenticated = useIsAuthenticated()
  const { instance } = useMsal()
  const [devSession, setDevSession] = useState<DevSession | null>(getDevSession)

  if (AUTH_DISABLED) {
    if (!devSession) {
      return <DevLogin onLogin={session => setDevSession(session)} />
    }
    return (
      <AuthenticatedApp
        devUsername={devSession.display_name}
        onDevLogout={() => { clearDevUsername(); setDevSession(null) }}
      />
    )
  }

  if (!isAuthenticated) {
    return <LoginScreen onLogin={() => instance.loginRedirect(loginRequest)} />
  }

  return <AuthenticatedApp />
}

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  return (
    <div style={loginStyles.root}>
      <div style={loginStyles.card}>
        <h1 style={loginStyles.title}>Enterprise AI Agent</h1>
        <p style={loginStyles.subtitle}>Jira-aware coding assistant for your team</p>
        <button style={loginStyles.btn} onClick={onLogin}>
          Sign in with Microsoft
        </button>
      </div>
    </div>
  )
}

interface AuthenticatedAppProps {
  devUsername?: string
  onDevLogout?: () => void
}

function AuthenticatedApp({ devUsername, onDevLogout }: AuthenticatedAppProps) {
  useTokenProvider()
  const { stories, loading, error: storiesError, refetch } = useJiraStories()
  const [activeTab, setActiveTab] = useState<ActiveTab>('general')
  const [showProfile, setShowProfile] = useState(false)
  const [showIngest, setShowIngest] = useState(false)
  const [personaLabel, setPersonaLabel] = useState('')

  const activeStory = stories.find((s) => s.key === activeTab) ?? null

  return (
    <div style={appStyles.root}>
      {/* Top bar */}
      <header style={appStyles.topbar}>
        <span style={appStyles.logo}>Enterprise AI Agent</span>
        <nav style={appStyles.topNav}>
          {devUsername && (
            <span style={appStyles.devUser}>👤 {devUsername}</span>
          )}
          <button
            style={{ ...appStyles.navBtn, ...(activeTab === 'dashboard' ? appStyles.navBtnActive : {}) }}
            onClick={() => setActiveTab('dashboard')}
          >
            Dashboard
          </button>
          <button
            style={appStyles.navBtn}
            onClick={() => setShowIngest(true)}
            title="Upload documents to the knowledge base"
          >
            Knowledge Base
          </button>
          <button
            style={appStyles.profileBtn}
            onClick={() => setShowProfile(true)}
            title="My profile & persona"
          >
            {personaLabel || 'Profile'}
          </button>
          {onDevLogout && (
            <button style={appStyles.logoutBtn} onClick={onDevLogout} title="Switch user">
              Switch user
            </button>
          )}
        </nav>
      </header>

      {showProfile && (
        <ProfileModal
          onClose={() => setShowProfile(false)}
          onPersonaChange={(_, label) => setPersonaLabel(label)}
        />
      )}

      {showIngest && <IngestModal onClose={() => setShowIngest(false)} />}

      <div style={appStyles.body}>
        <StorySidebar
          stories={stories}
          loading={loading}
          error={storiesError}
          activeTab={activeTab}
          onSelectStory={setActiveTab}
          onSelectGeneral={() => setActiveTab('general')}
          onRefresh={refetch}
        />

        <main style={appStyles.main}>
          {activeTab === 'dashboard' && <Dashboard />}
          {activeTab === 'general' && (
            <div style={appStyles.generalWrap}>
              <div style={appStyles.tabHeader}>
                <h2 style={appStyles.tabTitle}>General</h2>
                <span style={appStyles.tabSub}>No story context — uses project-level RAG</span>
              </div>
              <ChatWindow sessionId="general" storyId={null} />
            </div>
          )}
          {activeStory && activeTab !== 'general' && activeTab !== 'dashboard' && (
            <StoryTab
              story={activeStory}
              sessionId={`story_${activeStory.key}`}
            />
          )}
        </main>
      </div>
    </div>
  )
}

const loginStyles: Record<string, React.CSSProperties> = {
  root: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--bg)',
  },
  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: '12px',
    padding: '48px',
    textAlign: 'center',
    maxWidth: '400px',
    width: '100%',
  },
  title: {
    fontSize: '24px',
    fontWeight: 700,
    marginBottom: '8px',
  },
  subtitle: {
    color: 'var(--text-muted)',
    marginBottom: '32px',
    fontSize: '15px',
  },
  btn: {
    background: 'var(--primary)',
    color: '#fff',
    padding: '12px 32px',
    borderRadius: 'var(--radius)',
    fontWeight: 600,
    fontSize: '15px',
    transition: 'background 0.15s',
  },
}

const appStyles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    overflow: 'hidden',
  },
  topbar: {
    height: '48px',
    background: 'var(--surface)',
    borderBottom: '1px solid var(--border)',
    display: 'flex',
    alignItems: 'center',
    padding: '0 16px',
    justifyContent: 'space-between',
    flexShrink: 0,
  },
  logo: {
    fontWeight: 700,
    fontSize: '15px',
    color: 'var(--primary)',
  },
  topNav: {
    display: 'flex',
    gap: '8px',
  },
  navBtn: {
    padding: '4px 12px',
    borderRadius: 'var(--radius)',
    fontSize: '13px',
    color: 'var(--text-muted)',
    transition: 'background 0.1s',
  },
  navBtnActive: {
    background: 'var(--surface-2)',
    color: 'var(--text)',
  },
  devUser: {
    fontSize: '12px',
    color: 'var(--text-muted)',
    padding: '0 4px',
  },
  profileBtn: {
    padding: '4px 12px',
    borderRadius: 'var(--radius)',
    fontSize: '13px',
    color: 'var(--primary)',
    border: '1px solid var(--primary)',
    cursor: 'pointer',
    fontWeight: 500,
  },
  logoutBtn: {
    padding: '4px 12px',
    borderRadius: 'var(--radius)',
    fontSize: '13px',
    color: 'var(--text-muted)',
    border: '1px solid var(--border)',
    cursor: 'pointer',
  },
  body: {
    display: 'flex',
    flex: 1,
    overflow: 'hidden',
  },
  main: {
    flex: 1,
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
  },
  generalWrap: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
  },
  tabHeader: {
    padding: '12px 20px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--surface)',
  },
  tabTitle: {
    fontSize: '16px',
    fontWeight: 600,
  },
  tabSub: {
    fontSize: '12px',
    color: 'var(--text-muted)',
  },
}
