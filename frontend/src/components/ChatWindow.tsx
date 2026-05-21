import React, { useEffect, useRef, useState } from 'react'
import { useChat, Message } from '../hooks/useSSE'
import { FeedbackWidget } from './FeedbackWidget'

interface Props {
  sessionId: string
  storyId: string | null
}

export function ChatWindow({ sessionId, storyId }: Props) {
  const { messages, streaming, error, sendMessage, cancelStream } = useChat(sessionId, storyId)
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || streaming) return
    setInput('')
    sendMessage(trimmed)
  }

  // Show spinner only while streaming hasn't produced an assistant message yet
  const lastMsg = messages[messages.length - 1]
  const showSpinner = streaming && lastMsg?.role !== 'assistant'

  return (
    <div style={styles.root}>
      <div style={styles.messages}>
        {messages.length === 0 && (
          <div style={styles.empty}>
            {storyId
              ? `Ask anything about ${storyId} — story context is pre-loaded.`
              : 'Ask a general question about the project.'}
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            sessionId={sessionId}
            storyId={storyId}
            isStreaming={streaming && msg === lastMsg && msg.role === 'assistant'}
          />
        ))}
        {showSpinner && (
          <div style={{ ...styles.bubbleWrap, justifyContent: 'flex-start' }}>
            <div style={{ ...styles.bubble, ...styles.assistant }}>
              <Spinner />
            </div>
          </div>
        )}
        {error && <div style={styles.error}>{error}</div>}
        <div ref={bottomRef} />
      </div>

      <form style={styles.inputRow} onSubmit={handleSubmit}>
        <textarea
          style={styles.textarea}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit(e as unknown as React.FormEvent)
            }
          }}
          placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
          rows={2}
          disabled={streaming}
        />
        {streaming ? (
          <button
            type="button"
            style={styles.stopBtn}
            onClick={cancelStream}
            title="Stop generating"
          >
            Stop
          </button>
        ) : (
          <button style={styles.sendBtn} type="submit" disabled={!input.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  )
}

interface BubbleProps {
  message: Message
  sessionId: string
  storyId: string | null
  isStreaming: boolean
}

function MessageBubble({ message, sessionId, storyId, isStreaming }: BubbleProps) {
  const isUser = message.role === 'user'
  return (
    <div style={{ ...styles.bubbleWrap, justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <div
        style={{
          ...styles.bubble,
          ...(isUser ? styles.user : styles.assistant),
          ...(isStreaming ? styles.streaming : {}),
        }}
      >
        {isUser ? (
          <div style={styles.plain}>{message.content}</div>
        ) : (
          <MarkdownContent content={message.content} />
        )}
        {!isUser && message.sources && message.sources.length > 0 && message.confidence !== undefined && !isStreaming && (
          <ConfidenceBadge value={message.confidence} />
        )}
        {message.sources && message.sources.length > 0 && (
          <div style={styles.sources}>
            <strong>Sources:</strong>{' '}
            {message.sources.map((s, i) => (
              <code key={i} style={styles.sourceTag}>{s}</code>
            ))}
          </div>
        )}
        {!isUser && !isStreaming && (
          <FeedbackWidget
            sessionId={sessionId}
            messageId={message.id}
            storyId={storyId}
          />
        )}
      </div>
    </div>
  )
}

// ── Lightweight markdown renderer ──────────────────────────────────────────────

function MarkdownContent({ content }: { content: string }) {
  if (!content) return null

  const lines = content.split('\n')
  const elements: React.ReactNode[] = []
  let i = 0
  let listBuffer: React.ReactNode[] = []

  const flushList = () => {
    if (listBuffer.length > 0) {
      elements.push(<ul key={`ul-${i}`} style={mdStyles.ul}>{listBuffer}</ul>)
      listBuffer = []
    }
  }

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code block
    if (line.startsWith('```')) {
      flushList()
      const lang = line.slice(3).trim()
      const codeLines: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      elements.push(
        <pre key={i} style={mdStyles.pre}>
          {lang && <span style={mdStyles.langTag}>{lang}</span>}
          <code>{codeLines.join('\n')}</code>
        </pre>
      )
    }
    // Headings
    else if (line.startsWith('### ')) {
      flushList()
      elements.push(<strong key={i} style={mdStyles.h3}>{renderInline(line.slice(4))}</strong>)
    }
    else if (line.startsWith('## ')) {
      flushList()
      elements.push(<strong key={i} style={mdStyles.h2}>{renderInline(line.slice(3))}</strong>)
    }
    else if (line.startsWith('# ')) {
      flushList()
      elements.push(<strong key={i} style={mdStyles.h1}>{renderInline(line.slice(2))}</strong>)
    }
    // Unordered list items
    else if (/^[-*+] /.test(line)) {
      listBuffer.push(<li key={i} style={mdStyles.li}>{renderInline(line.slice(2))}</li>)
    }
    // Ordered list items
    else if (/^\d+\. /.test(line)) {
      const text = line.replace(/^\d+\.\s+/, '')
      listBuffer.push(<li key={i} style={mdStyles.li}>{renderInline(text)}</li>)
    }
    // Empty line — paragraph break
    else if (line.trim() === '') {
      flushList()
      // gap handled by container flex gap
    }
    // Regular line
    else {
      flushList()
      elements.push(<p key={i} style={mdStyles.p}>{renderInline(line)}</p>)
    }

    i++
  }

  flushList()

  return <div style={mdStyles.root}>{elements}</div>
}

function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g)
  if (parts.length === 1) return text
  return (
    <>
      {parts.map((part, idx) => {
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
          return <strong key={idx}>{part.slice(2, -2)}</strong>
        }
        if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
          return <em key={idx}>{part.slice(1, -1)}</em>
        }
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
          return <code key={idx} style={mdStyles.inlineCode}>{part.slice(1, -1)}</code>
        }
        return part
      })}
    </>
  )
}

// ── Confidence badge ───────────────────────────────────────────────────────────

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 75 ? '#16a34a' : pct >= 50 ? '#d97706' : '#dc2626'
  const bg   = pct >= 75 ? '#dcfce7' : pct >= 50 ? '#fef3c7' : '#fee2e2'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', marginTop: '6px' }}>
      <span style={{
        fontSize: '11px',
        fontWeight: 600,
        color,
        background: bg,
        padding: '1px 7px',
        borderRadius: '999px',
        border: `1px solid ${color}33`,
      }}>
        {pct}% confidence
      </span>
    </span>
  )
}

// ── Spinner ────────────────────────────────────────────────────────────────────

function Spinner() {
  return <span style={{ opacity: 0.5, letterSpacing: '4px' }}>● ● ●</span>
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
  },
  messages: {
    flex: 1,
    overflowY: 'auto',
    padding: '16px',
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  empty: {
    textAlign: 'center',
    color: 'var(--text-muted)',
    marginTop: '40px',
    fontSize: '13px',
  },
  bubbleWrap: {
    display: 'flex',
  },
  bubble: {
    maxWidth: '80%',
    padding: '10px 14px',
    borderRadius: 'var(--radius)',
    lineHeight: 1.6,
    fontSize: '14px',
  },
  streaming: {
    borderLeft: '2px solid var(--primary)',
  },
  user: {
    background: 'var(--primary)',
    color: '#fff',
  },
  assistant: {
    background: 'var(--surface-2)',
    border: '1px solid var(--border)',
    color: 'var(--text)',
  },
  plain: {
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  sources: {
    marginTop: '8px',
    fontSize: '12px',
    color: 'var(--text-muted)',
    display: 'flex',
    flexWrap: 'wrap',
    gap: '4px',
    alignItems: 'center',
  },
  sourceTag: {
    background: 'var(--surface)',
    padding: '1px 6px',
    borderRadius: '4px',
    fontSize: '11px',
    fontFamily: 'var(--mono)',
  },
  inputRow: {
    display: 'flex',
    gap: '8px',
    padding: '12px 16px',
    borderTop: '1px solid var(--border)',
    alignItems: 'flex-end',
  },
  textarea: {
    flex: 1,
    resize: 'none',
    minHeight: '44px',
    maxHeight: '160px',
    lineHeight: 1.5,
  },
  sendBtn: {
    background: 'var(--primary)',
    color: '#fff',
    padding: '8px 20px',
    borderRadius: 'var(--radius)',
    fontWeight: 600,
    height: '44px',
    whiteSpace: 'nowrap',
    transition: 'background 0.15s',
  },
  stopBtn: {
    background: 'var(--danger)',
    color: '#fff',
    padding: '8px 20px',
    borderRadius: 'var(--radius)',
    fontWeight: 600,
    height: '44px',
    whiteSpace: 'nowrap',
    cursor: 'pointer',
  },
  error: {
    color: 'var(--danger)',
    fontSize: '13px',
    padding: '8px 12px',
    background: 'rgba(239,68,68,0.1)',
    borderRadius: 'var(--radius)',
    border: '1px solid rgba(239,68,68,0.3)',
  },
}

const mdStyles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    wordBreak: 'break-word',
  },
  p: {
    margin: 0,
    whiteSpace: 'pre-wrap',
  },
  h1: {
    display: 'block',
    fontSize: '15px',
    marginBottom: '2px',
  },
  h2: {
    display: 'block',
    fontSize: '14px',
    marginBottom: '2px',
  },
  h3: {
    display: 'block',
    fontSize: '13px',
  },
  ul: {
    margin: 0,
    paddingLeft: '20px',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
  },
  li: {
    margin: 0,
  },
  pre: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: '6px',
    padding: '10px 12px',
    overflowX: 'auto',
    fontFamily: 'var(--mono)',
    fontSize: '12px',
    lineHeight: 1.5,
    margin: 0,
    position: 'relative',
  },
  langTag: {
    position: 'absolute',
    top: '4px',
    right: '8px',
    fontSize: '10px',
    color: 'var(--text-muted)',
    fontFamily: 'var(--mono)',
  },
  inlineCode: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    padding: '1px 5px',
    borderRadius: '4px',
    fontFamily: 'var(--mono)',
    fontSize: '12px',
  },
}
