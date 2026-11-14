import { useCallback, useEffect, useState } from 'react'
import { api, apiOrigin, errorMessage } from './api'
import type { AnalysisStart, Repository, WorkflowRun } from './api'

export type Resource<T> =
  | { state: 'loading' }
  | { state: 'ready'; data: T }
  | { state: 'error'; error: string }

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [snapshot, setSnapshot] = useState<{ load: typeof load; value: Resource<T> }>()
  const [revision, setRevision] = useState(0)
  const reload = useCallback(() => {
    setSnapshot(undefined)
    setRevision((value) => value + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal).then(
      (data) => { if (!controller.signal.aborted) setSnapshot({ load, value: { state: 'ready', data } }) },
      (error: unknown) => {
        if (!controller.signal.aborted) setSnapshot({ load, value: { state: 'error', error: errorMessage(error) } })
      },
    )
    return () => controller.abort()
  }, [load, revision])

  const resource: Resource<T> = snapshot?.load === load ? snapshot.value : { state: 'loading' }
  return { resource, reload }
}

export function useRuns(repositories: Repository[] | undefined) {
  const load = useCallback(async (signal: AbortSignal) => {
    const results = await Promise.all((repositories ?? []).map(async (repository) => {
      try {
        return { runs: await api.runs(repository.id, signal) }
      } catch (error) {
        return { failure: { repository, error: errorMessage(error) } }
      }
    }))
    return {
      runs: sortRuns(results.flatMap((result) => result.runs ?? [])),
      failures: results.flatMap((result) => result.failure ? [result.failure] : []),
    }
  }, [repositories])
  return useResource(load)
}

export function sortRuns(runs: WorkflowRun[]) {
  return [...runs].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
}

export const isFailed = (run: WorkflowRun) => run.conclusion === 'failure' || run.conclusion === 'timed_out' || run.conclusion === 'startup_failure'
export const isActive = (run: WorkflowRun) => ['in_progress', 'queued', 'requested', 'pending', 'waiting'].includes(run.status)
export const readable = (value: string) => value.replaceAll('_', ' ')
export const runPath = (run: Pick<WorkflowRun, 'id' | 'repository_id'>) => `/repositories/${run.repository_id}/runs/${run.id}`

export function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  }).format(date)
}

export interface SavedAnalysis extends AnalysisStart {
  repositoryId: number
  workflow: string
  repository: string
  createdAt: string
}

const storageKey = `devpilot:analysis-index:v1:${apiOrigin}`

function readAnalyses(): SavedAnalysis[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey) ?? '[]')
    if (!Array.isArray(value)) return []
    return value.filter((entry) => entry
      && typeof entry.analysis_id === 'string' && Number.isSafeInteger(entry.run_id)
      && Number.isSafeInteger(entry.repositoryId) && typeof entry.workflow === 'string'
      && typeof entry.repository === 'string' && typeof entry.createdAt === 'string'
      && ['queued', 'running', 'completed', 'failed'].includes(entry.status))
  } catch {
    return []
  }
}

export function useSavedAnalyses() {
  const [analyses, setAnalyses] = useState(readAnalyses)
  const save = useCallback((analysis: SavedAnalysis) => {
    setAnalyses((current) => {
      const existing = current.find((entry) => entry.analysis_id === analysis.analysis_id)
      if (existing?.status === analysis.status) return current
      return existing
        ? current.map((entry) => entry.analysis_id === analysis.analysis_id ? analysis : entry)
        : [analysis, ...current]
    })
  }, [])
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(analyses))
    } catch {
      // The current tab still works when storage is disabled.
    }
  }, [analyses])
  return { analyses, save }
}
