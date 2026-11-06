import { useEffect, useRef, useState } from 'react'
import { runIndexBatches } from '../bodyIndex'
import type { BodyIndexResult, BodyStatus, SearchHit } from '../types'

const LABELS: Record<BodyStatus, string> = {
  pending: '본문 색인 대기', ready: '본문 검색 가능', truncated: '본문 일부만 색인됨',
  no_text: '추출 가능한 텍스트 없음 · OCR 미제공', encrypted: '암호화된 파일 · 본문 검색 불가',
  unsupported: '본문 추출 미지원 형식', too_large: '파일 크기 상한 초과', failed: '본문 추출 실패',
  timeout: '본문 추출 시간 초과', missing: '원본 파일 없음', hash_mismatch: '원본 파일 판 확인 실패',
}

export function BodyMatch({ hit }: { hit: Pick<SearchHit, 'snippet' | 'body_status' | 'body_detail' | 'version' | 'revision'> }) {
  return <div className="small" style={{ maxWidth: 480, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
    <div className="faint">{LABELS[hit.body_status || 'pending']} · 원문 판 {hit.version || hit.revision || '미지정'}</div>
    {hit.snippet && <p>{hit.snippet}</p>}
    {hit.body_detail && <div className="faint">{hit.body_detail}</div>}
  </div>
}

export function BodyIndexPanel({ admin, locked, runBatch, onUpdated }: {
  admin: boolean; locked: boolean
  runBatch: (options: { cursor?: string; limit: number; current_only: boolean; force: boolean }) => Promise<BodyIndexResult>
  onUpdated: () => void
}) {
  const [running, setRunning] = useState(false)
  const [currentOnly, setCurrentOnly] = useState(true)
  const [force, setForce] = useState(false)
  const [cursor, setCursor] = useState<string | null>(null)
  const [completed, setCompleted] = useState(0)
  const [remaining, setRemaining] = useState<number | null>(null)
  const [last, setLast] = useState<BodyIndexResult | null>(null)
  const [counts, setCounts] = useState<Partial<Record<BodyStatus, number>>>({})
  const [issues, setIssues] = useState<BodyIndexResult['results']>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const stop = useRef(false)
  const inFlight = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; stop.current = true } }, [])
  if (!admin) return null

  async function start() {
    if (inFlight.current || locked) return
    inFlight.current = true
    stop.current = false
    setRunning(true)
    setError('')
    setNotice('원본 파일을 작은 묶음으로 읽고 있습니다.')
    try {
      await runIndexBatches(
        (position) => runBatch({ cursor: position || undefined, limit: 2, current_only: currentOnly, force }),
        () => stop.current,
        (batch) => {
          if (!mounted.current) return
          setCursor(batch.next_cursor)
          setCompleted((value) => value + batch.processed)
          setRemaining(batch.remaining)
          setLast(batch)
          setCounts((previous) => {
            const next = { ...previous }
            for (const item of batch.results) next[item.status] = (next[item.status] || 0) + 1
            return next
          })
          setIssues((previous) => [...previous, ...batch.results.filter((item) => !['ready', 'truncated'].includes(item.status))].slice(-100))
          setNotice(batch.done ? '색인 갱신을 마쳤습니다. 파일별 상태를 확인하세요.' : '본문 색인 갱신 중…')
        }, cursor,
      )
      if (mounted.current) {
        if (stop.current) setNotice('현재 묶음까지 처리하고 중지했습니다. 같은 조건으로 재개할 수 있습니다.')
        onUpdated()
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '본문 색인 갱신에 실패했습니다.')
    } finally { inFlight.current = false; if (mounted.current) setRunning(false) }
  }
  function reset() { setCursor(null); setCompleted(0); setRemaining(null); setLast(null); setCounts({}); setIssues([]); setNotice(''); setError('') }
  return <details style={{ margin: '12px 0' }}>
    <summary>관리자 · 본문 색인 갱신</summary>
    <p className="faint small">화면을 열어도 색인을 자동 실행하지 않습니다. 먼저 Current 문서를 갱신한 뒤 필요할 때 과거 판을 포함하세요. OCR은 제공하지 않습니다.</p>
    {locked && <p role="alert">비밀번호를 변경한 뒤 색인을 갱신할 수 있습니다.</p>}
    <div className="filter-bar">
      <label className="check"><input type="checkbox" checked={currentOnly} disabled={running} onChange={(e) => { setCurrentOnly(e.target.checked); reset() }} />Current 버전만 (해제하면 과거 판 포함)</label>
      <label className="check"><input type="checkbox" checked={force} disabled={running} onChange={(e) => { setForce(e.target.checked); reset() }} />이미 처리한 파일도 다시 추출</label>
      <button type="button" disabled={running || locked || !!last?.done} onClick={() => void start()}>{cursor ? '본문 색인 재개' : '본문 색인 갱신'}</button>
      {running && <button type="button" onClick={() => { stop.current = true; setNotice('현재 묶음을 마친 뒤 중지합니다…') }}>중지</button>}
      {!running && completed > 0 && <button type="button" onClick={reset}>처음부터 다시</button>}
    </div>
    <p role="status">{notice}{completed > 0 && ` 처리 ${completed}개${remaining !== null ? ` · 남은 대상 ${remaining}개` : ''}`}</p>
    {running && <progress aria-label="본문 색인 처리 진행" value={completed} max={Math.max(1, completed + (remaining ?? 1))} />}
    {error && <p role="alert">{error} 마지막으로 완료한 묶음 다음부터 재개할 수 있습니다.</p>}
    {completed > 0 && <ul>{Object.entries(counts).map(([status, count]) => <li key={status}>{LABELS[status as BodyStatus]}: {count}개</li>)}</ul>}
    {issues.length > 0 && <details><summary>본문을 검색할 수 없는 파일의 처리 결과 (최근 100개까지)</summary><ul>{issues.map((item, index) => <li key={`${item.id}-${index}`}>{LABELS[item.status]}{item.detail ? ` · ${item.detail}` : ''}</li>)}</ul></details>}
  </details>
}
