import { CircleHelp, FolderGit2, LayoutDashboard, ListChecks, Terminal, Workflow } from 'lucide-react'
import { useEffect } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api'
import { AnalysisList, ReportPage } from './analysis'
import { EmptyState, PageHeading, Panel } from './components'
import { useResource, useSavedAnalyses } from './data'
import { Dashboard, RepositoryPage, RepositoryRunPage, RunDetailPage, RunsPage } from './screens'

const nav = [
  { to: '/', title: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/repositories', title: 'Repositories', icon: FolderGit2, end: false },
  { to: '/runs', title: 'Workflow runs', icon: Workflow, end: false },
  { to: '/analyses', title: 'Analyses', icon: ListChecks, end: false },
]

export function App() {
  const repositories = useResource(api.repositories)
  const health = useResource(api.health)
  const saved = useSavedAnalyses()
  const { pathname } = useLocation()

  useEffect(() => { window.scrollTo(0, 0) }, [pathname])
  const online = health.resource.state === 'ready' && health.resource.data.status === 'ok'
  const healthLabel = health.resource.state === 'loading' ? 'Checking API' : online ? 'API online' : 'API unavailable'

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar">
      <Link className="brand" to="/" aria-label="DevPilot overview">
        <span className="brand-mark"><Terminal size={17} aria-hidden="true" /></span>
        <strong>DevPilot</strong>
      </Link>
      <div className="nav-section">
        <nav aria-label="Workspace">{nav.map(({ to, title, icon: Icon, end }) =>
          <NavLink key={to} to={to} end={end} className="nav-link">
            <Icon size={18} aria-hidden="true" /><span>{title}</span>
          </NavLink>)}</nav>
      </div>
      <div className="sidebar-footer">
        <button className="health" type="button" onClick={health.reload} title="Check backend connection again"
          aria-label={`${healthLabel}. Check backend connection again.`}>
          <span className={`health-dot ${online ? 'online' : health.resource.state === 'loading' ? '' : 'offline'}`} />
          <span role="status">{healthLabel}</span>
        </button>
      </div>
    </aside>
    <div className="workspace">
      <main className="content" id="main-content" tabIndex={-1}>
        <Routes>
          <Route index element={<Dashboard repositories={repositories} />} />
          <Route path="repositories" element={<RepositoryPage repositories={repositories} />} />
          <Route path="repositories/:repositoryId" element={<RepositoryRunPage repositories={repositories} />} />
          <Route path="repositories/:repositoryId/runs/:runId" element={<RunDetailPage repositories={repositories} {...saved} />} />
          <Route path="runs" element={<RunsPage repositories={repositories} />} />
          <Route path="analyses" element={<>
            <PageHeading title="Analyses" description="Reopen diagnostic reports requested from this browser." />
            <Panel title="Diagnostic history"><AnalysisList analyses={saved.analyses} /></Panel>
          </>} />
          <Route path="analyses/:analysisId" element={<ReportPage {...saved} />} />
          <Route path="*" element={<Panel title="Page not found"><EmptyState title="This page doesn’t exist"
            icon={<CircleHelp size={23} />} description="Use the workspace navigation to get back to your pipelines."
            action={<Link className="button button-primary" to="/">Back to overview</Link>} /></Panel>} />
        </Routes>
      </main>
    </div>
  </div>
}
