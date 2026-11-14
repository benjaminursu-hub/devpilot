import {
  ArrowLeft, ArrowRight, ArrowUpRight, ChevronDown, CircleAlert, CircleCheck, Clock3,
  FolderGit2, GitBranch, ListFilter, LoaderCircle, RefreshCw, Workflow,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Repository, WorkflowRun } from './api'
import { formatDate, isActive, isFailed, readable, runPath } from './data'

export function PageHeading({ title, description, actions }: {
  title: string; description: string; actions?: ReactNode
}) {
  return <header className="page-heading">
    <div><h1>{title}</h1><p>{description}</p></div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>
}

export function Loading({ label }: { label: string }) {
  return <div className="state" role="status"><LoaderCircle className="spin" size={20} aria-hidden="true" />{label}</div>
}

export function ErrorNotice({ title, message, retry }: { title: string; message: string; retry?: () => void }) {
  return <div className="error-notice" role="alert">
    <CircleAlert size={18} aria-hidden="true" />
    <div><strong>{title}</strong><p>{message}</p></div>
    {retry && <button className="button button-small" type="button" onClick={retry}><RefreshCw size={14} /> Retry</button>}
  </div>
}

export function EmptyState({ title, description, action, icon = <Workflow size={23} /> }: {
  title: string; description: string; action?: ReactNode; icon?: ReactNode
}) {
  return <div className="empty-state">
    <span className="empty-icon" aria-hidden="true">{icon}</span>
    <h3>{title}</h3><p>{description}</p>{action}
  </div>
}

export function Panel({ title, description, action, children }: {
  title: string; description?: string; action?: ReactNode; children: ReactNode
}) {
  return <section className="panel">
    <header className="panel-header"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</header>
    {children}
  </section>
}

export function StatusBadge({ run }: { run: WorkflowRun }) {
  const kind = isFailed(run) ? 'danger' : run.conclusion === 'success' ? 'success' : isActive(run) ? 'progress' : 'neutral'
  const Icon = kind === 'danger' ? CircleAlert : kind === 'success' ? CircleCheck : Clock3
  return <span className={`badge badge-${kind}`}><Icon size={13} aria-hidden="true" />{readable(run.conclusion || run.status)}</span>
}

export function GitHubLink({ url, labeled = false }: { url: string; labeled?: boolean }) {
  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null
    return <a className={labeled ? 'button' : 'icon-link github-link'} href={parsed.href} target="_blank" rel="noreferrer"
      aria-label="Open on GitHub (opens in a new tab)" title="Open on GitHub">{labeled && 'Open on GitHub'}<ArrowUpRight size={16} aria-hidden="true" /></a>
  } catch {
    return null
  }
}

export function RunList({ runs, repositories, emptyTitle = 'No workflow runs yet', emptyDescription = 'Run a workflow in GitHub Actions, then refresh this list.', emptyAction }: {
  runs: WorkflowRun[]; repositories?: Repository[]; emptyTitle?: string; emptyDescription?: string; emptyAction?: ReactNode
}) {
  if (!runs.length) return <EmptyState title={emptyTitle} description={emptyDescription} action={emptyAction} />
  const repoNames = new Map(repositories?.map((repo) => [repo.id, repo.full_name]))
  return <div className="table-wrap" tabIndex={0} role="region" aria-label="Recent workflow runs"><table className="run-table">
    <caption className="sr-only">Recent GitHub Actions workflow runs</caption>
    <thead><tr><th scope="col">Workflow</th><th scope="col">Status</th><th scope="col">Branch</th><th scope="col">Run time</th><th scope="col"><span className="sr-only">GitHub Actions</span></th></tr></thead>
    <tbody>{runs.map((run) => <tr key={`${run.repository_id}:${run.id}`}>
      <td><Link className="row-link" to={runPath(run)}>{run.name}</Link>
        {repoNames.get(run.repository_id) && <span className="cell-subtitle">{repoNames.get(run.repository_id)}</span>}
      </td>
      <td><StatusBadge run={run} />{run.conclusion && <span className="cell-subtitle capitalize">{readable(run.status)}</span>}</td>
      <td><span className="branch"><GitBranch size={14} aria-hidden="true" />{run.branch ?? 'Unknown branch'}</span></td>
      <td><time dateTime={run.created_at}>{formatDate(run.created_at)}</time></td>
      <td><GitHubLink url={run.html_url} /></td>
    </tr>)}</tbody>
  </table></div>
}

export function RepositoryList({ repositories }: { repositories: Repository[] }) {
  return <ul className="item-list">{repositories.map((repo) => <li key={repo.id}>
    <Link to={`/repositories/${repo.id}`} className="item-link">
      <span className="item-icon"><FolderGit2 size={17} aria-hidden="true" /></span>
      <span className="item-main"><strong>{repo.name}</strong><small>{repo.full_name}</small></span>
      <ArrowRight size={16} className="item-arrow" aria-hidden="true" />
    </Link>
  </li>)}</ul>
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return <Link to={to} className="back-link"><ArrowLeft size={16} />{children}</Link>
}

export function RunFilter({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <label className="filter"><ListFilter size={16} aria-hidden="true" />
    <span className="sr-only">Filter workflow runs</span>
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="all">All statuses</option><option value="failed">Failed</option>
      <option value="success">Successful</option><option value="active">In progress</option>
    </select>
    <ChevronDown className="filter-caret" size={14} aria-hidden="true" />
  </label>
}
