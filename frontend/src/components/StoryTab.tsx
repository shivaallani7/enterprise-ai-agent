import React, { useState } from 'react'
import { JiraStory } from '../hooks/useJiraStories'
import { ChatWindow } from './ChatWindow'

interface Props {
  story: JiraStory
  sessionId: string
}

export function StoryTab({ story, sessionId }: Props) {
  const [detailsOpen, setDetailsOpen] = useState(false)

  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <div style={styles.headerTop}>
          <div style={styles.meta}>
            <span style={styles.key}>{story.key}</span>
            <h1 style={styles.title}>{story.title}</h1>
          </div>
          <div style={styles.headerRight}>
            <div style={styles.badges}>
              <Badge label="Status" value={story.status} />
              <Badge label="Assignee" value={story.assignee} />
            </div>
            <button
              style={styles.detailsToggle}
              onClick={() => setDetailsOpen((o) => !o)}
              aria-expanded={detailsOpen}
            >
              {detailsOpen ? 'Hide details' : 'Show details'}
            </button>
          </div>
        </div>

        {detailsOpen && (
          <div style={styles.details}>
            {story.description && (
              <DetailSection label="Description" content={story.description} />
            )}
            {story.acceptance_criteria && (
              <DetailSection label="Acceptance Criteria" content={story.acceptance_criteria} />
            )}
            {story.pr_list && (
              <DetailSection label="Linked PRs" content={story.pr_list} />
            )}
          </div>
        )}
      </div>

      <div style={styles.body}>
        <ChatWindow sessionId={sessionId} storyId={story.key} />
      </div>
    </div>
  )
}

function DetailSection({ label, content }: { label: string; content: string }) {
  return (
    <div style={detailStyles.section}>
      <div style={detailStyles.label}>{label}</div>
      <div style={detailStyles.content}>{content}</div>
    </div>
  )
}

function Badge({ label, value }: { label: string; value: string }) {
  return (
    <span style={styles.badge}>
      <span style={styles.badgeLabel}>{label}: </span>
      <span style={styles.badgeValue}>{value}</span>
    </span>
  )
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
  },
  header: {
    borderBottom: '1px solid var(--border)',
    background: 'var(--surface)',
    flexShrink: 0,
  },
  headerTop: {
    padding: '12px 20px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: '12px',
  },
  meta: {
    display: 'flex',
    alignItems: 'baseline',
    gap: '10px',
    flex: 1,
    minWidth: 0,
  },
  key: {
    fontFamily: 'var(--mono)',
    color: 'var(--primary)',
    fontWeight: 700,
    fontSize: '13px',
    flexShrink: 0,
  },
  title: {
    fontSize: '16px',
    fontWeight: 600,
    color: 'var(--text)',
    lineHeight: 1.3,
    overflow: 'hidden',
    whiteSpace: 'nowrap',
    textOverflow: 'ellipsis',
  },
  headerRight: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    gap: '6px',
    flexShrink: 0,
  },
  badges: {
    display: 'flex',
    gap: '12px',
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
  },
  badge: {
    fontSize: '12px',
    color: 'var(--text-muted)',
  },
  badgeLabel: {
    fontWeight: 600,
  },
  badgeValue: {
    color: 'var(--text)',
  },
  detailsToggle: {
    fontSize: '12px',
    color: 'var(--primary)',
    background: 'transparent',
    cursor: 'pointer',
    padding: '2px 4px',
    fontWeight: 500,
  },
  details: {
    padding: '0 20px 12px',
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
    maxHeight: '220px',
    overflowY: 'auto',
  },
  body: {
    flex: 1,
    overflow: 'hidden',
  },
}

const detailStyles: Record<string, React.CSSProperties> = {
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  label: {
    fontSize: '11px',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    color: 'var(--text-muted)',
  },
  content: {
    fontSize: '13px',
    color: 'var(--text)',
    whiteSpace: 'pre-wrap',
    lineHeight: 1.5,
  },
}
