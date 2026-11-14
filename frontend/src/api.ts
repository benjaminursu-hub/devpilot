export interface Repository {
  id: number
  owner: string
  name: string
  full_name: string
}

export type NewRepository = Pick<Repository, 'owner' | 'name'>

export interface WorkflowRun {
  id: number
  repository_id: number
  name: string
  status: string
  conclusion: string | null
  branch: string | null
  html_url: string
  created_at: string
}

export interface RunLogs {
  run_id: number
  logs: string
  truncated: boolean
}

export type AnalysisStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface AnalysisStart {
  analysis_id: string
  run_id: number
  status: AnalysisStatus
}

export interface AnalysisResult {
  summary: string
  possible_causes: string[]
  evidence: string[]
  suggested_fixes: string[]
  confidence: string
}

export interface Analysis {
  id: string
  run_id: number
  status: AnalysisStatus
  result: AnalysisResult | null
  error: string | null
}

export const apiOrigin = (import.meta.env.VITE_API_BASE_URL?.trim() || 'http://localhost:8000').replace(/\/+$/, '')

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, { signal, body, method = 'GET' }: {
  signal?: AbortSignal
  body?: NewRepository
  method?: 'GET' | 'POST'
} = {}): Promise<T> {
  // React can clean up an effect immediately in development. Let it cancel
  // before sending a request that the server would otherwise still process.
  await Promise.resolve()
  signal?.throwIfAborted()

  const endpoint = `/api/v1${path}`
  const timeoutMessage = `The API request timed out (${method} ${endpoint}). Try again.`
  const timeout = AbortSignal.timeout(25_000)
  let response: Response
  try {
    response = await fetch(`${apiOrigin}${endpoint}`, {
      method,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      headers: body ? { Accept: 'application/json', 'Content-Type': 'application/json' } : { Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ApiError(timeout.aborted
      ? timeoutMessage
      : `Network error: the browser could not read an API response (${method} ${endpoint}). The server may be unreachable, or the browser may have blocked its response.`)
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch (error) {
    if (signal?.aborted) throw error
    // Once the status is visible, preserve HTTP errors even if the body can't be read.
    if (response.ok) {
      if (timeout.aborted) throw new ApiError(timeoutMessage)
      if (error instanceof SyntaxError) {
        throw new ApiError(`The API returned invalid JSON (HTTP ${response.status}).`)
      }
      throw new ApiError(`Network error: the API response was interrupted (${method} ${endpoint}).`)
    }
  }

  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : undefined
    const message = typeof detail === 'string' && detail.trim() ? detail.trim() : `${method} ${endpoint} failed.`
    throw new ApiError(`API error (HTTP ${response.status}): ${message}`, response.status)
  }
  return payload as T
}

export const api = {
  health: (signal: AbortSignal) => request<{ status: 'ok' }>('/health', { signal }),
  repositories: (signal: AbortSignal) => request<Repository[]>('/repositories', { signal }),
  addRepository: (body: NewRepository, signal: AbortSignal) =>
    request<Repository>('/repositories', { method: 'POST', body, signal }),
  runs: (repositoryId: number, signal: AbortSignal) =>
    request<WorkflowRun[]>(`/repositories/${repositoryId}/runs`, { signal }),
  logs: (runId: number, signal: AbortSignal) =>
    request<RunLogs>(`/runs/${runId}/logs`, { signal }),
  startAnalysis: (runId: number, signal: AbortSignal) =>
    request<AnalysisStart>(`/runs/${runId}/analyze`, { method: 'POST', signal }),
  analysis: (id: string, signal: AbortSignal) =>
    request<Analysis>(`/analyses/${encodeURIComponent(id)}`, { signal }),
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong. Try again.'
}
