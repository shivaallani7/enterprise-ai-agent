import { useState, useCallback, useRef, useEffect } from 'react'
import { api } from '../lib/api'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  confidence?: number
  timestamp: number
}

export interface ChatState {
  messages: Message[]
  streaming: boolean
  error: string | null
}

function parseSSELine(line: string): Record<string, unknown> | null {
  if (!line.startsWith('data:')) return null
  try {
    return JSON.parse(line.slice(5).trim())
  } catch {
    return null
  }
}

export function useChat(sessionId: string, storyId: string | null) {
  const [state, setState] = useState<ChatState>({
    messages: [],
    streaming: false,
    error: null,
  })

  // Keep a ref to the latest messages so sendMessage never closes over stale state.
  // Without this, the useCallback dep on state.messages causes a stale closure:
  // if the user sends a second message before the first finishes, allMessages
  // would not include the in-flight assistant reply.
  const messagesRef = useRef<Message[]>([])
  useEffect(() => {
    messagesRef.current = state.messages
  }, [state.messages])

  // Ref to the active AbortController so we can cancel mid-stream
  const abortRef = useRef<AbortController | null>(null)

  // Guard against concurrent sends — only one stream at a time
  const streamingRef = useRef(false)

  const sendMessage = useCallback(
    async (content: string) => {
      if (streamingRef.current) return

      const userMsg: Message = {
        id: crypto.randomUUID(),
        role: 'user',
        content,
        timestamp: Date.now(),
      }

      setState((s) => ({
        ...s,
        messages: [...s.messages, userMsg],
        streaming: true,
        error: null,
      }))
      streamingRef.current = true

      const assistantId = crypto.randomUUID()
      let assistantContent = ''
      let finalSources: string[] = []
      let finalConfidence = 0.9

      // Cancel any previous in-flight request
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      try {
        // Read from ref — always current even if state hasn't flushed yet
        const allMessages = [
          ...messagesRef.current,
          { role: 'user' as const, content },
        ].map((m) => ({ role: m.role, content: m.content }))

        const stream = await api.chatStream(
          { sessionId, storyId, messages: allMessages },
          controller.signal,
        )

        const reader = stream.getReader()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          if (controller.signal.aborted) break

          buffer += value
          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            const chunk = parseSSELine(line)
            if (!chunk) continue

            const delta = (chunk.delta as string) ?? ''
            const isDone = Boolean(chunk.done)
            const isClear = Boolean(chunk.clear)
            const sources = (chunk.sources as string[]) ?? []
            const confidence = (chunk.confidence as number) ?? 0.9

            if (isClear) {
              // Critic rejected previous answer — reset the assistant bubble
              assistantContent = ''
            } else if (isDone) {
              // Final done sentinel carries aggregated sources & confidence
              finalSources = sources
              finalConfidence = confidence
            } else {
              assistantContent += delta
            }

            setState((s) => {
              const exists = s.messages.some((m) => m.id === assistantId)
              const updated: Message = {
                id: assistantId,
                role: 'assistant',
                content: assistantContent,
                sources: isDone ? sources : undefined,
                confidence: isDone ? confidence : undefined,
                timestamp: Date.now(),
              }
              return {
                ...s,
                messages: exists
                  ? s.messages.map((m) => (m.id === assistantId ? updated : m))
                  : [...s.messages, updated],
                streaming: !isDone,
              }
            })

            if (isDone) break
          }
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          // User cancelled — mark streaming done without an error message
          setState((s) => ({ ...s, streaming: false }))
        } else {
          setState((s) => ({
            ...s,
            streaming: false,
            error: err instanceof Error ? err.message : 'Chat failed',
          }))
        }
      } finally {
        streamingRef.current = false
        if (abortRef.current === controller) {
          abortRef.current = null
        }
      }
    },
    // Only re-create when session/story changes, NOT on every message update.
    // messagesRef always has the latest value without causing re-creation.
    [sessionId, storyId],
  )

  const cancelStream = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const clearHistory = useCallback(() => {
    abortRef.current?.abort()
    streamingRef.current = false
    setState({ messages: [], streaming: false, error: null })
  }, [])

  return { ...state, sendMessage, cancelStream, clearHistory }
}
