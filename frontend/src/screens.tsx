import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowDown, ArrowRight, CircleAlert, FolderGit2, GitBranch, LoaderCircle, Plus, RefreshCw, Terminal } from 'lucide-react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, errorMessage } from './api'
import type { Repository, WorkflowRun } from './api'
import { AnalysisPanel } from './analysis'
import { WorkflowLog } from './WorkflowLog'
import {
  BackLink, EmptyState, ErrorNotice, GitHubLink, Loading, PageHeading, Panel, RepositoryList, RunFilter, RunList, StatusBadge,
} from './components'
import { formatDate, isActive, isFailed, sortRuns, useResource, useRuns } from './data'
import type { Resource, SavedAnalysis } from './data'

type Repositories = { resource: Resource<Repository[]>; reload: () => void }
const addPath = '/repositories?add=1'

function AddLink() {
  return <Link className="button button-primary" to={addPath}><Plus size={17} aria-hidden="true" /> Add repository</Link>
}

function RefreshButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  return <button className="button" onClick={onClick} disabled={disabled}><RefreshCw size={15} /> Refresh</button>
}

export function Dashboard({ repositories }: { repositories: Repositories }) {
  const { resource } = repositories
  const repos = resource.state === 'ready' ? resource.data : undefined
  const { resource: runData, reload } = useRuns(repos)
  const data = runData.state === 'ready' ? runData.data : undefined
  const failed = data?.runs.filter(isFailed) ?? []

  return <>
    <PageHeading title="Overview" description="Find and investigate recent GitHub Actions failures." actions={<AddLink />} />
    {resource.state === 'error' && <ErrorNotice title="Could not load repositories" message={resource.error} retry={repositories.reload} />}
    {repos && (repos.length ? <>
      <Panel title="Recent failures" description="From the latest runs returned for connected repositories."
        action={<Link className="button button-small" to="/runs?status=failed">Browse failures <ArrowRight size={14} /></Link>}>
        {runData.state === 'loading' && <Loading label="Loading recent runs…" />}
        {!!data?.failures.length && <RunErrors failures={data.failures} retry={reload} />}
        {data && data.failures.length < repos.length && <RunList runs={failed.slice(0, 6)} repositories={repos}
          emptyTitle="No failures in the runs loaded"
          emptyDescription="Browse recent workflow runs to check their status."
          emptyAction={<Link className="text-link" to="/runs">Browse workflow runs <ArrowRight size={14} /></Link>} />}
      </Panel>
      <Panel title="Repositories" action={<Link className="text-link" to="/repositories">Manage repositories <ArrowRight size={14} /></Link>}>
        <RepositoryList repositories={repos.slice(0, 5)} />
      </Panel>
    </> : <Panel title="Recent failures">
      <EmptyState title="Connect a repository" description="Add a GitHub repository to see its recent workflow runs." action={<AddLink />} />
    </Panel>)}
    {resource.state === 'loading' && <Loading label="Loading repositories…" />}
  </>
}
function RunErrors({ failures, retry }: { failures: { repository: Repository; error: string }[]; retry: () => void }) {
  return <div className="panel-body run-errors"><ErrorNotice title="Some workflow runs couldn’t be loaded"
    message={failures.map(({ repository, error }) => `${repository.full_name}: ${error}`).join('\n')} retry={retry} /></div>
}

export function RepositoryPage({ repositories }: { repositories: Repositories }) {
  const { resource, reload } = repositories
  return <>
    <PageHeading title="Repositories" description="Connect GitHub repositories and browse their workflow history."
      actions={<><RefreshButton onClick={reload} disabled={resource.state === 'loading'} /><AddLink /></>} />
    <div className="repository-grid">
      <Panel title="Connected repositories" description="Select a repository to see its workflow runs.">
        {resource.state === 'loading' && <Loading label="Loading repositories…" />}
        {resource.state === 'error' && <div className="panel-body"><ErrorNotice title="Could not load repositories" message={resource.error} retry={reload} /></div>}
        {resource.state === 'ready' && (resource.data.length ? <RepositoryList repositories={resource.data} /> :
          <EmptyState title="No repositories connected" description="Add your first GitHub repository to get started." icon={<FolderGit2 size={23} />} />)}
      </Panel>
      <RepositoryForm onAdded={reload} />
    </div>
  </>
}

function RepositoryForm({ onAdded }: { onAdded: () => void }) {
  const [params] = useSearchParams()
  const focusOwner = params.get('add') === '1'
  const ownerInput = useRef<HTMLInputElement>(null)
  const pending = useRef<AbortController | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => { if (focusOwner) ownerInput.current?.focus() }, [focusOwner])
  useEffect(() => () => pending.current?.abort(), [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending.current) return
    const fields = new FormData(event.currentTarget)
    const owner = String(fields.get('owner') ?? '').trim()
    const name = String(fields.get('name') ?? '').trim()
    if (!/^[a-zA-Z0-9-]+$/.test(owner) || !/^[a-zA-Z0-9_.-]+$/.test(name) || name === '.' || name === '..') {
      setError('Enter the GitHub owner and repository name separately, without a URL.')
      return
    }
    const controller = new AbortController()
    pending.current = controller
    setSaving(true)
    setError('')
    try {
      const repo = await api.addRepository({ owner, name }, controller.signal)
      if (controller.signal.aborted) return
      onAdded()
      navigate(`/repositories/${repo.id}`)
    } catch (error) {
      if (!controller.signal.aborted) setError(errorMessage(error))
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null
        setSaving(false)
      }
    }
  }

  return <Panel title="Add a repository" description="Enter the owner and name from the GitHub URL.">
    <form className="panel-body repository-form" onSubmit={submit} aria-label="Add a repository">
      <label>GitHub owner<input ref={ownerInput} name="owner" autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="e.g. octocat" required disabled={saving} /></label>
      <label>Repository name<input name="name" autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="e.g. hello-world" required disabled={saving} /></label>
      {error && <ErrorNotice title="Could not add repository" message={error} />}
      <button className="button button-primary" type="submit" disabled={saving}>
        {saving ? <LoaderCircle className="spin" size={16} /> : <Plus size={17} />}{saving ? 'Adding repository…' : 'Add repository'}
      </button>
    </form>
  </Panel>
}

function useRunFilter() {
  const [params, setParams] = useSearchParams()
  const value = params.get('status') ?? 'all'
  const status = ['failed', 'success', 'active'].includes(value) ? value : 'all'
  return {
    clear: () => setParams({}),
    filterControl: <RunFilter value={status} onChange={(next) => setParams(next === 'all' ? {} : { status: next })} />,
    filtered: (runs: WorkflowRun[]) => runs.filter((run) =>
      status === 'all' || (status === 'failed' && isFailed(run)) ||
      (status === 'success' && run.conclusion === 'success') || (status === 'active' && isActive(run))),
  }
}

export function RunsPage({ repositories }: { repositories: Repositories }) {
  const { resource } = repositories
  const repos = resource.state === 'ready' ? resource.data : undefined
  const runs = useRuns(repos)
  const filter = useRunFilter()
  const data = runs.resource.state === 'ready' ? runs.resource.data : undefined
  const loading = resource.state === 'loading' || runs.resource.state === 'loading'
  return <>
    <PageHeading title="Workflow runs" description="Recent GitHub Actions runs from connected repositories." />
    <Panel title="Recent runs" action={<div className="list-actions">{filter.filterControl}
      <RefreshButton onClick={resource.state === 'error' ? repositories.reload : runs.reload} disabled={loading} /></div>}>
      {loading && <Loading label="Loading workflow runs…" />}
      {resource.state === 'error' && <div className="panel-body"><ErrorNotice title="Could not load repositories" message={resource.error} retry={repositories.reload} /></div>}
      {repos && !repos.length && <EmptyState title="No repositories connected" description="Add a repository to see its workflow runs." action={<AddLink />} />}
      {!!repos?.length && data && <>
        {!!data.failures.length && <RunErrors failures={data.failures} retry={runs.reload} />}
        {data.failures.length < repos.length && <FilteredRuns runs={data.runs} repositories={repos} filter={filter} />}
      </>}
    </Panel>
  </>
}

export function RepositoryRunPage({ repositories }: { repositories: Repositories }) {
  return <RepositoryRoute repositories={repositories}>{(repo) => <RepositoryRuns key={repo.id} repo={repo} />}</RepositoryRoute>
}

function RepositoryRuns({ repo }: { repo: Repository }) {
  const load = useCallback((signal: AbortSignal) => api.runs(repo.id, signal), [repo.id])
  const { resource, reload } = useResource(load)
  const filter = useRunFilter()
  return <>
    <BackLink to="/repositories">Repositories</BackLink>
    <PageHeading title={repo.full_name} description="Recent GitHub Actions runs for this repository." />
    <Panel title="Workflow runs" action={<div className="list-actions">{filter.filterControl}
      <RefreshButton onClick={reload} disabled={resource.state === 'loading'} /></div>}>
      {resource.state === 'loading' && <Loading label="Loading workflow runs…" />}
      {resource.state === 'error' && <div className="panel-body"><ErrorNotice title="Could not load workflow runs" message={resource.error} retry={reload} /></div>}
      {resource.state === 'ready' && <FilteredRuns runs={sortRuns(resource.data)} filter={filter} />}
    </Panel>
  </>
}

function FilteredRuns({ runs, repositories, filter }: {
  runs: WorkflowRun[]; repositories?: Repository[]; filter: ReturnType<typeof useRunFilter>
}) {
  if (!runs.length) return <RunList runs={runs} />
  return <RunList runs={filter.filtered(runs)} repositories={repositories}
    emptyTitle="No runs match this filter" emptyDescription="Try a different status or clear the filter."
    emptyAction={<button className="button button-small" onClick={filter.clear}>Clear filter</button>} />
}

function RepositoryRoute({ repositories, children }: { repositories: Repositories; children: (repo: Repository) => React.ReactNode }) {
  const { repositoryId } = useParams()
  const { resource, reload } = repositories
  if (resource.state === 'loading') return <Loading label="Loading repository…" />
  if (resource.state === 'error') return <ErrorNotice title="Could not load repositories" message={resource.error} retry={reload} />
  const repo = resource.data.find(({ id }) => String(id) === repositoryId)
  return repo ? children(repo) : <>
    <BackLink to="/repositories">Repositories</BackLink>
    <EmptyState title="Repository not found" description="This repository is not registered with the current backend." icon={<FolderGit2 size={23} />} />
  </>
}

type AnalysisIndex = { analyses: SavedAnalysis[]; save: (analysis: SavedAnalysis) => void }

export function RunDetailPage({ repositories, ...index }: { repositories: Repositories } & AnalysisIndex) {
  const { runId } = useParams()
  return <RepositoryRoute repositories={repositories}>{(repo) =>
    <RunDetail key={`${repo.id}:${runId}`} repo={repo} runId={runId} {...index} />}</RepositoryRoute>
}

function RunDetail({ repo, runId, analyses, save }: { repo: Repository; runId?: string } & AnalysisIndex) {
  const load = useCallback((signal: AbortSignal) => api.runs(repo.id, signal), [repo.id])
  const { resource, reload } = useResource(load)
  const run = resource.state === 'ready' ? resource.data.find((entry) => String(entry.id) === runId) : undefined
  const analysis = analyses.find((entry) => entry.run_id === run?.id && entry.repositoryId === repo.id)
  return <>
    <BackLink to={`/repositories/${repo.id}`}>{repo.full_name}</BackLink>
    {resource.state === 'loading' && <Loading label="Loading workflow details…" />}
    {resource.state === 'error' && <ErrorNotice title="Could not load workflow details" message={resource.error} retry={reload} />}
    {resource.state === 'ready' && (run ? <RunInspector repo={repo} run={run} analysis={analysis} save={save} /> :
      <EmptyState title="Workflow run not found" description="This run isn’t in the repository’s recent workflow history." />)}
  </>
}

function RunInspector({ repo, run, analysis, save }: { repo: Repository; run: WorkflowRun; analysis?: SavedAnalysis; save: AnalysisIndex['save'] }) {
  const load = useCallback((signal: AbortSignal) => api.logs(run.id, signal), [run.id])
  const logs = useResource(load)
  const pending = useRef<AbortController | null>(null)
  const report = useRef<HTMLDivElement>(null)
  const fullLog = useRef<HTMLElement>(null)
  const jumpToReport = useRef(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState('')
  const [logJump, setLogJump] = useState<{ line: number } | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  useEffect(() => {
    if (analysis?.analysis_id && jumpToReport.current) {
      jumpToReport.current = false
      report.current?.focus()
    }
  }, [analysis?.analysis_id])

  const updateStatus = useCallback((status: SavedAnalysis['status']) => {
    if (analysis) save({ ...analysis, status })
  }, [analysis, save])

  async function start() {
    if (pending.current || !isFailed(run)) return
    const controller = new AbortController()
    pending.current = controller
    setStarting(true)
    setStartError('')
    try {
      const started = await api.startAnalysis(run.id, controller.signal)
      if (controller.signal.aborted) return
      jumpToReport.current = true
      save({ ...started, repositoryId: repo.id, workflow: run.name, repository: repo.full_name, createdAt: new Date().toISOString() })
    } catch (error) {
      if (!controller.signal.aborted) setStartError(errorMessage(error))
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null
        setStarting(false)
      }
    }
  }

  const failed = isFailed(run)
  const log = logs.resource.state === 'ready' ? logs.resource.data : undefined
  const lines = useMemo(() => log?.logs ? log.logs.replace(/\r?\n$/, '').split(/\r?\n/) : [], [log?.logs])
  const excerpt = useMemo(() => failed ? failureExcerpt(lines) : [], [failed, lines])

  return <article className="run-review">
    <header className="run-heading">
      <div>
        <div className="run-title"><h1>{run.name}</h1><StatusBadge run={run} /></div>
        <div className="run-meta">
          {run.conclusion && <span className="capitalize">{run.status.replaceAll('_', ' ')}</span>}
          <span className="branch"><GitBranch size={15} aria-hidden="true" />{run.branch ?? 'Unknown branch'}</span>
          <time dateTime={run.created_at}>{formatDate(run.created_at)}</time>
          <span>Run #{run.id}</span>
        </div>
      </div>
      <div className="page-actions">
        <GitHubLink url={run.html_url} labeled />
        {failed && !analysis && <button className="button button-primary" onClick={start} disabled={starting}>
          {starting && <LoaderCircle size={16} className="spin" aria-hidden="true" />}
          {starting ? 'Starting analysis…' : 'Analyze failure'}
        </button>}
        <a className="text-link" href="#workflow-log" onClick={(event) => {
          event.preventDefault()
          fullLog.current?.focus()
        }}>View full log <ArrowDown size={15} aria-hidden="true" /></a>
      </div>
    </header>
    {startError && <ErrorNotice title="Could not start analysis" message={startError} />}
    {(failed || analysis) && <div className="triage-grid">
      <Panel title="Failure excerpt" description="Observed in the workflow log">
        <div className="panel-body excerpt-body">
          {excerpt.length ? <ol className="excerpt" aria-label="Line numbers in the available log">{excerpt.map(({ line, number }) =>
            <li key={number}><button type="button" onClick={() => setLogJump({ line: number })}
              aria-label={`Jump to log line ${number}: ${line}`}>
              <span className="line-number" aria-hidden="true">{number}</span><code>{line}</code>
            </button></li>)}</ol>
            : <p className="muted">{logs.resource.state === 'loading' ? 'Loading the workflow log…' :
              log?.logs ? 'No short error excerpt could be identified in the available log.' : 'No log excerpt is available.'}</p>}
          {excerpt.length > 0 && <p className="muted excerpt-hint">Select a line to open it in the full log.</p>}
        </div>
      </Panel>
      {analysis ? <div ref={report} className="report-anchor" tabIndex={-1} role="region" aria-label="Latest diagnosis">
        <AnalysisPanel key={analysis.analysis_id} id={analysis.analysis_id} initial={analysis.status}
          onStatus={updateStatus} startAgain={failed ? start : undefined} starting={starting} runReview />
      </div> : <Panel title="Diagnosis">
        <div className="panel-body diagnosis-empty">
          <strong>No analysis yet</strong>
          <p className="muted">Analyze this failure to see possible causes and suggested fixes.</p>
        </div>
      </Panel>}
    </div>}
    <section id="workflow-log" ref={fullLog} tabIndex={-1} className="log-anchor" aria-label="Workflow log">
      <Panel title="Workflow log" description={lines.length ? `${lines.length} ${lines.length === 1 ? 'line' : 'lines'} available` : undefined}
        action={<RefreshButton onClick={() => { setLogJump(null); logs.reload() }} disabled={logs.resource.state === 'loading'} />}>
        {logs.resource.state === 'loading' && <Loading label="Loading workflow log…" />}
        {logs.resource.state === 'error' && <div className="panel-body"><ErrorNotice title="Could not load the workflow log" message={logs.resource.error} retry={logs.reload} /></div>}
        {log && <>
          {log.truncated && <p className="log-notice" role="status"><CircleAlert size={16} aria-hidden="true" />
            DevPilot received a partial log. Open the run on GitHub for the complete output.</p>}
          {log.logs ? <WorkflowLog lines={lines} jump={logJump} /> :
            <EmptyState title="No log output" description="Check the run on GitHub for available output." icon={<Terminal size={20} />} />}
        </>}
      </Panel>
    </section>
  </article>
}

function failureExcerpt(lines: string[]) {
  const matches: { line: string; number: number; annotation: boolean }[] = []
  for (const [index, line] of lines.entries()) {
    const output = line.trimStart()
    const annotation = /^##\[error\]\s*\S/i.test(output)
    if (line.length <= 320 && (annotation || /^(?:\[ERROR\]\s*|(?:ERROR|FATAL|PANIC):\s*)\S/i.test(output))) {
      matches.push({ line, number: index + 1, annotation })
    }
  }
  const annotation = matches.filter((match) => match.annotation).at(-1)
  const excerpt = matches.filter((match) => match !== annotation).slice(annotation ? -2 : -3)
  if (annotation) excerpt.push(annotation)
  return excerpt.sort((a, b) => a.number - b.number)
}
