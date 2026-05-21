import React, { useState } from 'react'
import { api } from '../lib/api'

interface Props {
  sessionId: string
  messageId: string
  storyId: string | null
}

type Rating = 'up' | 'down' | null

export function FeedbackWidget({ sessionId, messageId, storyId }: Props) {
  const [rating, setRating] = useState<Rating>(null)
  const [correction, setCorrection] = useState('')
  const [showCorrection, setShowCorrection] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const handleRate = async (r: Rating) => {
    if (submitted) return
    setRating(r)
    if (r === 'down') {
      setShowCorrection(true)
    } else {
      await submit(r, '')
    }
  }

  const submit = async (r: Rating, corr: string) => {
    setSubmitting(true)
    try {
      await api.submitFeedback({
        sessionId,
        messageId,
        rating: r === 'up' ? 1 : -1,
        correction: corr || undefined,
        storyId,
      })
      setSubmitted(true)
      setShowCorrection(false)
    } catch {
      // Silent — feedback is best-effort
    } finally {
      setSubmitting(false)
    }
  }

  if (submitted) {
    return <div style={styles.thanks}>Thanks for your feedback!</div>
  }

  return (
    <div style={styles.root}>
      <div style={styles.buttons}>
        <button
          style={{ ...styles.btn, ...(rating === 'up' ? styles.active : {}) }}
          onClick={() => handleRate('up')}
          title="Helpful"
          aria-label="Thumbs up"
        >
          👍
        </button>
        <button
          style={{ ...styles.btn, ...(rating === 'down' ? styles.activeDown : {}) }}
          onClick={() => handleRate('down')}
          title="Not helpful"
          aria-label="Thumbs down"
        >
          👎
        </button>
      </div>

      {showCorrection && (
        <div style={styles.correctionBox}>
          <textarea
            style={styles.correctionInput}
            placeholder="What should the correct answer be? (optional)"
            value={correction}
            onChange={(e) => setCorrection(e.target.value)}
            rows={3}
          />
          <div style={styles.correctionActions}>
            <button
              style={styles.submitBtn}
              onClick={() => submit('down', correction)}
              disabled={submitting}
            >
              {submitting ? 'Saving…' : 'Submit'}
            </button>
            <button
              style={styles.cancelBtn}
              onClick={() => { setShowCorrection(false); setRating(null) }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    marginTop: '8px',
  },
  buttons: {
    display: 'flex',
    gap: '4px',
  },
  btn: {
    fontSize: '14px',
    padding: '2px 6px',
    borderRadius: '4px',
    opacity: 0.5,
    transition: 'opacity 0.15s',
    cursor: 'pointer',
    background: 'transparent',
  },
  active: {
    opacity: 1,
    background: 'rgba(34,197,94,0.15)',
  },
  activeDown: {
    opacity: 1,
    background: 'rgba(239,68,68,0.15)',
  },
  correctionBox: {
    marginTop: '8px',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  correctionInput: {
    fontSize: '12px',
    resize: 'vertical',
    minHeight: '60px',
  },
  correctionActions: {
    display: 'flex',
    gap: '6px',
  },
  submitBtn: {
    background: 'var(--primary)',
    color: '#fff',
    padding: '4px 12px',
    borderRadius: '4px',
    fontSize: '12px',
    fontWeight: 600,
  },
  cancelBtn: {
    color: 'var(--text-muted)',
    padding: '4px 8px',
    fontSize: '12px',
  },
  thanks: {
    fontSize: '12px',
    color: 'var(--success)',
    marginTop: '6px',
    opacity: 0.8,
  },
}
