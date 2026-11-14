import { ChevronDown, ChevronUp, Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

type Match = { start: number; end: number; index: number }

function findMatches(lines: string[], query: string) {
  if (!query) return { byLine: new Map<number, Match[]>(), count: 0 }
  const pattern = new RegExp(query.replace(/[.*+?^\x24{}()|[\]\\]/g, '\\$&'), 'giu')
  const byLine = new Map<number, Match[]>()
  let count = 0

  lines.forEach((line, index) => {
    const matches = Array.from(line.matchAll(pattern), (match) => ({
      start: match.index,
      end: match.index + match[0].length,
      index: count++,
    }))
    if (matches.length) byLine.set(index + 1, matches)
  })
  return { byLine, count }
}

function highlight(line: string, matches: Match[], active: number) {
  const parts: ReactNode[] = []
  let offset = 0
  for (const match of matches) {
    parts.push(line.slice(offset, match.start))
    parts.push(<mark key={match.start} data-active={match.index === active ? 'true' : undefined}>
      {line.slice(match.start, match.end)}
    </mark>)
    offset = match.end
  }
  parts.push(line.slice(offset))
  return parts
}

export function WorkflowLog({ lines, jump }: {
  lines: string[]
  jump: { line: number } | null
}) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const [targetLine, setTargetLine] = useState<number>()
  const input = useRef<HTMLInputElement>(null)
  const viewer = useRef<HTMLDivElement>(null)
  const { byLine, count } = useMemo(() => findMatches(lines, query), [lines, query])
  const active = count ? Math.min(selected, count - 1) : -1

  useEffect(() => {
    if (!jump) return
    const row = viewer.current?.querySelector<HTMLElement>('[data-line="' + jump.line + '"]')
    if (!row) return
    setTargetLine(jump.line)
    row.focus({ preventScroll: true })
    row.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [jump])

  useEffect(() => {
    if (active < 0) return
    viewer.current?.querySelector('[data-active="true"]')?.scrollIntoView({
      block: 'nearest', inline: 'nearest',
    })
  }, [active, query])

  function updateQuery(value: string) {
    setQuery(value)
    setSelected(0)
    setTargetLine(undefined)
  }

  function moveMatch(direction: number) {
    if (count === 0) return
    setTargetLine(undefined)
    setSelected((active + direction + count) % count)
  }

  return <>
    <div className="log-toolbar" role="search" aria-label="Workflow log search">
      <div className="log-search">
        <Search size={15} aria-hidden="true" />
        <input ref={input} type="search" aria-label="Search log" placeholder="Search log" value={query}
          onChange={(event) => updateQuery(event.target.value)} onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return
            if (event.key === 'Enter') {
              event.preventDefault()
              moveMatch(event.shiftKey ? -1 : 1)
            }
            if (event.key === 'Escape' && query) {
              event.preventDefault()
              updateQuery('')
            }
          }} />
        {query && <button className="icon-link" type="button" aria-label="Clear search" onClick={() => {
          updateQuery('')
          input.current?.focus()
        }}><X size={15} aria-hidden="true" /></button>}
      </div>
      <div className="log-match-controls">
        <span className="log-match-count" role="status">{query && (count ? (active + 1) + ' of ' + count : 'No matches')}</span>
        <button className="icon-link" type="button" aria-label="Previous match" disabled={!count} onClick={() => moveMatch(-1)}>
          <ChevronUp size={16} aria-hidden="true" />
        </button>
        <button className="icon-link" type="button" aria-label="Next match" disabled={!count} onClick={() => moveMatch(1)}>
          <ChevronDown size={16} aria-hidden="true" />
        </button>
      </div>
    </div>
    <div className="log-viewer" ref={viewer} tabIndex={0} role="region" aria-label="Workflow log output">
      <ol className="log-lines">
        {lines.map((line, index) => {
          const number = index + 1
          const matches = byLine.get(number)
          return <li key={number} data-line={number} data-target={number === targetLine ? 'true' : undefined} tabIndex={-1}>
            <span className="log-line-number" aria-hidden="true">{number}</span>
            <code>{matches ? highlight(line, matches, active) : line}</code>
          </li>
        })}
      </ol>
    </div>
  </>
}
