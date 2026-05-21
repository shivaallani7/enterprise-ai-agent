import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api'

export interface JiraStory {
  key: string
  title: string
  description: string
  acceptance_criteria: string
  status: string
  assignee: string
  pr_list: string
  comments: string
}

const POLL_INTERVAL_MS = 5 * 60 * 1000 // 5 minutes

export function useJiraStories() {
  const [stories, setStories] = useState<JiraStory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchStories = useCallback(async () => {
    try {
      const data = await api.getStories()
      setStories(data.stories)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load stories')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStories()
    const id = setInterval(fetchStories, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [fetchStories])

  return { stories, loading, error, refetch: fetchStories }
}
