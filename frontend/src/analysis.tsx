import { useEffect, useRef, useState } from 'react'
import { ArrowRight, CircleAlert, CircleCheck, Clock3, ListChecks, LoaderCircle } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, api, errorMessage } from './api'
import type { Analysis, AnalysisStart } from './api'
import { BackLink, EmptyState, ErrorNotice, PageHeading, Panel } from './components'
import { formatDate, runPath } from './data'
import type { SavedAnalysis } from './data'

const pollDelay = 2_000
const maxRetries = 3

export function AnalysisPanel({ id, initial, onStatus, startAgain, starting, runReview }: {
  id: string; initial?: AnalysisStart['status']; onStatus?: (status: Analysis['status']) => void; startAgain?: () => void; starting?: boolean; runReview?: boolean
}) {
  const [analysis, setAnalysis] = useState<Analysis>()
  const [revision, setRevision] = useState(0)
  const [failure, setFailure] = useState<{ message: string; stopped: boolean }>()
  const statusListener = useRef(onStatus)
  useEffect(() => { statusListener.current = onStatus }, [onStatus])

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0

    async function poll() {
      try {
        const current = await api.analysis(id, controller.signal)
        if (controller.signal.aborted) return
        failures = 0
        setFailure(undefined)
        setAnalysis(current)
        statusListener.current?.(current.status)
        if (current.status === 'queued' || current.status === 'running') timer = setTimeout(poll, pollDelay)
      } catch (error) {
        if (controller.signal.aborted) return
        failures++
        const transient = !(error instanceof ApiError && error.status && error.status < 500
          && error.status !== 408 && error.status !== 429)
        const stopped = !transient || failures > maxRetries
        setFailure({ message: errorMessage(error), stopped })
        if (!stopped) timer = setTimeout(poll, pollDelay * 2 ** (failures - 1))
      }
    }

    void poll()
    return () => {
      controller.abort()
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [id, revision])

  const status = analysis?.status ?? initial
  const result = analysis?.result

  return <Panel title="Latest diagnosis"
    action={status && <AnalysisBadge status={status} />}>
    <div className="panel-body analysis-content">
      {failure && (failure.stopped
        ? <ErrorNotice title="Could not check analysis status" message={failure.message} retry={() => {
          setFailure(undefined)
          setRevision((value) => value + 1)
        }} />
        : <p className="inline-note" role="status"><Clock3 size={16} />Connection interrupted. Retrying automatically.</p>)}
      {!failure?.stopped && (!analysis || status === 'queued' || status === 'running') &&
        <p className="inline-note" role="status"><LoaderCircle className="spin" size={17} />
          {status === 'queued' ? 'Waiting for the analysis worker…' : status === 'running' ? 'Analyzing the workflow logs…' : 'Checking analysis status…'}
        </p>}
      {analysis?.status === 'failed' && <>
        <ErrorNotice title="Analysis failed" message={analysis.error || 'The backend could not complete this analysis.'} />
        {startAgain && <button className="button" onClick={startAgain} disabled={starting}>
          {starting && <LoaderCircle size={16} className="spin" aria-hidden="true" />}
          {starting ? 'Starting analysis…' : 'Analyze again'}
        </button>}
      </>}
      {analysis?.status === 'completed' && (result ? <>
        <section className="report-summary">
          <h3>{runReview ? 'Likely cause' : 'Interpretation'}</h3>
          <p>{result.summary}</p>
        </section>
        <div className="report-grid">
          <ReportItems title={runReview && result.suggested_fixes.length === 1 ? 'Next step' : 'Suggested next steps'}
            items={result.suggested_fixes} ordered />
          {runReview ? <details className="report-details">
            <summary>More analysis details</summary>
            <ReportItems title="Possible causes" items={result.possible_causes} />
            <ReportItems title="Evidence cited in the analysis" items={result.evidence} code />
          </details> : <>
            <ReportItems title="Possible causes" items={result.possible_causes} />
            <ReportItems title="Evidence cited in the analysis" items={result.evidence} code />
          </>}
        </div>
        <p className="report-confidence">Reported confidence: {result.confidence}</p>
      </> : <ErrorNotice title="Report unavailable" message="The API marked the analysis complete without returning a report." />)}
    </div>
  </Panel>
}

function ReportItems({ title, items, ordered, code }: {
  title: string; items: string[]; ordered?: boolean; code?: boolean
}) {
  const List = ordered ? 'ol' : 'ul'
  return <section className={`report-section ${code ? 'report-evidence' : ''}`}>
    <h3>{title}</h3>
    {items.length ? <List>{items.map((item, index) => <li key={index}>{code ? <code>{item}</code> : item}</li>)}</List>
      : <p className="muted">None returned.</p>}
  </section>
}

export function AnalysisBadge({ status }: { status: Analysis['status'] }) {
  const kind = status === 'failed' ? 'danger' : status === 'completed' ? 'success' : 'progress'
  const Icon = status === 'failed' ? CircleAlert : status === 'completed' ? CircleCheck : Clock3
  return <span className={`badge badge-${kind}`}><Icon size={13} aria-hidden="true" />{status}</span>
}

export function AnalysisList({ analyses }: { analyses: SavedAnalysis[] }) {
  if (!analyses.length) return <EmptyState title="No analyses started" description="Open a failed run and select Analyze failure to generate your first diagnostic report."
    icon={<ListChecks size={20} />} action={<Link className="button button-small" to="/runs?status=failed">Browse failed runs <ArrowRight size={15} /></Link>} />
  return <ul className="item-list">{analyses.map((analysis) => <li key={analysis.analysis_id}>
    <Link to={`/analyses/${encodeURIComponent(analysis.analysis_id)}`} className="item-link">
      <span className="item-icon"><ListChecks size={17} aria-hidden="true" /></span>
      <span className="item-main"><strong>{analysis.workflow}</strong><small>{analysis.repository} · {formatDate(analysis.createdAt)}</small></span>
      <AnalysisBadge status={analysis.status} /><ArrowRight size={16} className="item-arrow" aria-hidden="true" />
    </Link>
  </li>)}</ul>
}

export function ReportPage({ analyses, save }: { analyses: SavedAnalysis[]; save: (analysis: SavedAnalysis) => void }) {
  const { analysisId } = useParams()
  const entry = analyses.find((item) => item.analysis_id === analysisId)
  return analysisId && <ReportDetail key={analysisId} id={analysisId} entry={entry} save={save} />
}

function ReportDetail({ id, entry, save }: { id: string; entry?: SavedAnalysis; save: (analysis: SavedAnalysis) => void }) {
  // Keep the initial reference stable as polling updates the index.
  const [initial] = useState(entry)
  const [onStatus] = useState(() => (status: Analysis['status']) => {
    if (initial) save({ ...initial, status })
  })
  return <>
    <BackLink to="/analyses">Analyses</BackLink>
    <PageHeading title={initial?.workflow ?? 'Analysis report'} description={initial?.repository ?? id}
      actions={initial && <Link className="button" to={runPath({ id: initial.run_id, repository_id: initial.repositoryId })}>View workflow run <ArrowRight size={16} /></Link>} />
    <AnalysisPanel id={id} initial={initial?.status} onStatus={onStatus} />
  </>
}
