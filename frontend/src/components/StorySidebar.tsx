import React from 'react'
import { JiraStory } from '../hooks/useJiraStories'

interface Props {
  stories: JiraStory[]
  loading: boolean
  error: string | null
  activeTab: string
  onSelectStory: (key: string) => void
  onSelectGeneral: () => void
  onRefresh: () => void
}

export function StorySidebar({
  stories,
  loading,
  error,
  activeTab,
  onSelectStory,
  onSelectGeneral,
  onRefresh,
}: Props) {
  return (
    <aside style={styles.sidebar}>
      <div style={styles.header}>
        <span style={styles.headerText}>Stories</span>
        <button
          style={styles.refreshBtn}
          onClick={onRefresh}
          disabled={loading}
          title="Refresh stories"
          aria-label="Refresh"
        >
          {loading ? '…' : '↻'}
        </button>
      </div>

      <nav style={styles.nav}>
        <button
          style={{
            ...styles.tab,
            ...(activeTab === 'general' ? styles.activeTab : {}),
          }}
          onClick={onSelectGeneral}
        >
          <span style={styles.tabIcon}>🌐</span>
          <span>General</span>
        </button>

        <div style={styles.divider} />

        {error && (
          <div style={styles.errorMsg}>
            {error}
            <button style={styles.retryLink} onClick={onRefresh}>Retry</button>
          </div>
        )}

        {!error && !loading && stories.length === 0 && (
          <div style={styles.empty}>No open stories</div>
        )}

        {stories.map((story) => (
          <button
            key={story.key}
            style={{
              ...styles.tab,
              ...(activeTab === story.key ? styles.activeTab : {}),
            }}
            onClick={() => onSelectStory(story.key)}
            title={story.title}
          >
            <span style={styles.storyKey}>{story.key}</span>
            <span style={styles.storyTitle}>{story.title}</span>
            <StatusBadge status={story.status} />
          </button>
        ))}
      </nav>
    </aside>
  )
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === 'In Progress' ? 'var(--primary)' :
    status === 'Done' ? 'var(--success)' :
    status === 'Blocked' ? 'var(--danger)' :
    'var(--text-muted)'

  return (
    <span style={{ ...styles.badge, background: color + '22', color }}>
      {status}
    </span>
  )
}

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    width: '240px',
    minWidth: '200px',
    background: 'var(--surface)',
    borderRight: '1px solid var(--border)',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    padding: '12px 16px',
    borderBottom: '1px solid var(--border)',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  headerText: {
    fontWeight: 700,
    fontSize: '13px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    color: 'var(--text-muted)',
  },
  refreshBtn: {
    background: 'transparent',
    color: 'var(--text-muted)',
    fontSize: '16px',
    padding: '2px 6px',
    borderRadius: '4px',
    cursor: 'pointer',
    lineHeight: 1,
  },
  nav: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
  },
  tab: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: '2px',
    padding: '8px 10px',
    borderRadius: 'var(--radius)',
    textAlign: 'left',
    cursor: 'pointer',
    transition: 'background 0.1s',
    background: 'transparent',
    width: '100%',
  },
  activeTab: {
    background: 'var(--surface-2)',
    outline: '1px solid var(--border)',
  },
  tabIcon: {
    fontSize: '16px',
  },
  storyKey: {
    fontSize: '11px',
    fontFamily: 'var(--mono)',
    color: 'var(--primary)',
    fontWeight: 600,
  },
  storyTitle: {
    fontSize: '13px',
    color: 'var(--text)',
    lineHeight: 1.3,
    display: '-webkit-box',
    WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical',
    overflow: 'hidden',
  },
  badge: {
    fontSize: '10px',
    padding: '1px 6px',
    borderRadius: '10px',
    fontWeight: 600,
    marginTop: '2px',
  },
  divider: {
    height: '1px',
    background: 'var(--border)',
    margin: '4px 0',
  },
  errorMsg: {
    padding: '10px 12px',
    color: 'var(--danger)',
    fontSize: '12px',
    background: 'rgba(239,68,68,0.08)',
    borderRadius: 'var(--radius)',
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  retryLink: {
    background: 'transparent',
    color: 'var(--primary)',
    fontSize: '12px',
    fontWeight: 600,
    cursor: 'pointer',
    padding: 0,
    textAlign: 'left',
  },
  empty: {
    padding: '12px',
    color: 'var(--text-muted)',
    fontSize: '13px',
  },
}
