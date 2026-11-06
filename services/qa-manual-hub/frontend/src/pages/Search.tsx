import { useEffect, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { BodyIndexPanel, BodyMatch } from '../components/BodySearch'
import { readFilters, useQuerySort, writeFilters } from '../queryState'
import {
  Alert,
  Card,
  CurrentBadge,
  Empty,
  Field,
  Loading,
  SortTh,
  dateKey,
  fmtDate,
  fmtDateTime,
  type SortColumn,
} from '../components/ui'
import type { Category, Product, SearchHit } from '../types'

type SortKey =
  | 'product'
  | 'document'
  | 'category'
  | 'revision'
  | 'docNumber'
  | 'language'
  | 'revisionDate'
  | 'uploader'
  | 'uploadDate'
  | 'file'

const COLUMNS: readonly SortColumn<SearchHit, SortKey>[] = [
  { key: 'product', value: (h) => h.product_name },
  { key: 'document', value: (h) => h.document_name },
  { key: 'category', value: (h) => h.category_name },
  { key: 'revision', value: (h) => h.version ?? h.revision },
  { key: 'docNumber', value: (h) => h.document_number },
  { key: 'language', value: (h) => h.language },
  { key: 'revisionDate', value: (h) => dateKey(h.revision_date) },
  { key: 'uploader', value: (h) => h.uploaded_by_display_name },
  { key: 'uploadDate', value: (h) => dateKey(h.upload_date) },
  { key: 'file', value: (h) => h.original_file_name },
]

const BLANK = {
  q: '',
  product_id: '',
  category_id: '',
  document_name: '',
  document_number: '',
  revision: '',
  version: '',
  language: '',
  file_name: '',
  uploaded_by: '',
  revision_date_from: '',
  revision_date_to: '',
  upload_date_from: '',
  upload_date_to: '',
  document_status: 'active',
  version_status: 'all',
  current_only: false,
}

export default function Search() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const [form, setForm] = useState(() => readFilters(params, BLANK))
  const filterKey = JSON.stringify(readFilters(params, BLANK))
  const [refresh, setRefresh] = useState(0)
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const advanced = params.get('view') === 'advanced' || (params.get('view') !== 'basic' &&
    Object.keys(BLANK).some((key) => !['q', 'product_id', 'category_id'].includes(key) &&
      params.has(key)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { sort, toggle, sorted } = useQuerySort(hits, COLUMNS)

  useEffect(() => {
    Promise.all([api.products({ include_inactive: true }), api.categories()])
      .then(([p, c]) => {
        setProducts(p)
        setCategories(c)
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    let active = true
    const applied = JSON.parse(filterKey) as typeof BLANK
    setForm(applied)
    setBusy(true)
    setHits(null)
    setError(null)
    // Keep the existing array API contract and pin the displayed upper bound.
    api.search({ ...applied, limit: 200 })
      .then((result) => { if (active) setHits(result) })
      .catch((e) => { if (active) setError(e instanceof Error ? e.message : '검색에 실패했습니다.') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [filterKey, refresh])

  function submit(e: FormEvent) {
    e.preventDefault()
    setParams((previous) => writeFilters(previous, form, BLANK))
    setRefresh((value) => value + 1)
  }

  function set<K extends keyof typeof BLANK>(key: K, value: (typeof BLANK)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Search</h1>
          <div className="desc">
            제품·문서 정보와 색인된 원문 본문을 함께 검색합니다.
          </div>
        </div>
      </div>

      {error && <Alert kind="error">{error}</Alert>}
      <BodyIndexPanel admin={!!user?.is_admin} locked={!!user?.must_change_password}
        runBatch={api.reindexSearch} onUpdated={() => setRefresh((value) => value + 1)} />

      <Card>
        <form onSubmit={submit}>
          <div className="filter-bar">
            <Field label="통합 검색" className="grow">
              <input
                type="search"
                value={form.q}
                onChange={(e) => set('q', e.target.value)}
                placeholder="문서 정보·색인된 본문에서 검색"
                autoFocus
              />
            </Field>
            <Field label="Product">
              <select
                value={form.product_id}
                onChange={(e) => set('product_id', e.target.value)}
              >
                <option value="">전체</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Category">
              <select
                value={form.category_id}
                onChange={(e) => set('category_id', e.target.value)}
              >
                <option value="">전체</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? '검색 중...' : '검색'}
            </button>
            <button
              type="button"
              onClick={() => {
                setForm({ ...BLANK })
                setParams(new URLSearchParams())
                setRefresh((value) => value + 1)
              }}
            >
              초기화
            </button>
            <button type="button" className="btn-link" onClick={() => setParams((previous) => {
              const next = new URLSearchParams(previous)
              next.set('view', advanced ? 'basic' : 'advanced')
              return next
            })}>
              {advanced ? '상세 조건 접기' : '상세 조건 펼치기'}
            </button>
          </div>

          {advanced && (
            <>
              <div className="divider" />
              <div className="form-grid">
                <Field label="Document Name">
                  <input
                    type="text"
                    value={form.document_name}
                    onChange={(e) => set('document_name', e.target.value)}
                  />
                </Field>
                <Field label="Document Number">
                  <input
                    type="text"
                    value={form.document_number}
                    onChange={(e) => set('document_number', e.target.value)}
                  />
                </Field>
                <Field label="Revision">
                  <input
                    type="text"
                    value={form.revision}
                    onChange={(e) => set('revision', e.target.value)}
                  />
                </Field>
                <Field label="Version">
                  <input
                    type="text"
                    value={form.version}
                    onChange={(e) => set('version', e.target.value)}
                  />
                </Field>
                <Field label="Language">
                  <input
                    type="text"
                    value={form.language}
                    onChange={(e) => set('language', e.target.value)}
                  />
                </Field>
                <Field label="Original File Name">
                  <input
                    type="text"
                    value={form.file_name}
                    onChange={(e) => set('file_name', e.target.value)}
                  />
                </Field>
                <Field label="Uploaded By">
                  <input
                    type="text"
                    value={form.uploaded_by}
                    onChange={(e) => set('uploaded_by', e.target.value)}
                    placeholder="이름 또는 로그인 ID"
                  />
                </Field>
                <Field label="Revision Date (from)">
                  <input
                    type="date"
                    value={form.revision_date_from}
                    onChange={(e) => set('revision_date_from', e.target.value)}
                  />
                </Field>
                <Field label="Revision Date (to)">
                  <input
                    type="date"
                    value={form.revision_date_to}
                    onChange={(e) => set('revision_date_to', e.target.value)}
                  />
                </Field>
                <Field label="Upload Date (from)">
                  <input
                    type="date"
                    value={form.upload_date_from}
                    onChange={(e) => set('upload_date_from', e.target.value)}
                  />
                </Field>
                <Field label="Upload Date (to)">
                  <input
                    type="date"
                    value={form.upload_date_to}
                    onChange={(e) => set('upload_date_to', e.target.value)}
                  />
                </Field>
                <Field label="Document Status">
                  <select
                    value={form.document_status}
                    onChange={(e) => set('document_status', e.target.value)}
                  >
                    <option value="active">Active</option>
                    <option value="archived">Archived</option>
                    <option value="all">전체</option>
                  </select>
                </Field>
                <Field label="Version Status">
                  <select
                    value={form.version_status}
                    onChange={(e) => set('version_status', e.target.value)}
                  >
                    <option value="all">전체</option>
                    <option value="active">Active</option>
                    <option value="archived">Archived</option>
                  </select>
                </Field>
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.current_only}
                  onChange={(e) => set('current_only', e.target.checked)}
                />
                Current 버전만 검색
              </label>
            </>
          )}
        </form>
      </Card>

      <Card title="검색 결과" sub={sorted ? `표시 ${sorted.length}건` : undefined} flush>
        <p className="faint small" role="status">
          최대 200건까지 표시합니다. 정렬은 표시된 결과에 적용됩니다.
          {sorted && sorted.length >= 200 && ' 전체 결과 수는 확인되지 않았습니다. 상세 조건으로 검색 범위를 좁혀 주세요.'}
          {' 본문 색인이 준비되지 않은 파일은 문서 정보로만 검색합니다. 이미지 문자의 OCR 검색은 제공하지 않습니다.'}
        </p>
        {!sorted ? (
          <Loading />
        ) : sorted.length === 0 ? (
          <Empty title="조건에 맞는 결과가 없습니다">
            검색어를 줄이거나 상세 조건을 초기화해 보세요.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <SortTh label="Product" sortKey="product" sort={sort} onSort={toggle} />
                  <SortTh
                    label="Document"
                    sortKey="document"
                    sort={sort}
                    onSort={toggle}
                    className="wrap"
                  />
                  <SortTh label="Category" sortKey="category" sort={sort} onSort={toggle} />
                  <SortTh label="Rev / Ver" sortKey="revision" sort={sort} onSort={toggle} />
                  <SortTh label="Doc. No." sortKey="docNumber" sort={sort} onSort={toggle} />
                  <SortTh label="Lang" sortKey="language" sort={sort} onSort={toggle} />
                  <SortTh
                    label="Revision Date"
                    sortKey="revisionDate"
                    sort={sort}
                    onSort={toggle}
                  />
                  <SortTh label="Uploaded By" sortKey="uploader" sort={sort} onSort={toggle} />
                  <SortTh
                    label="Upload Date"
                    sortKey="uploadDate"
                    sort={sort}
                    onSort={toggle}
                  />
                  <SortTh
                    label="File"
                    sortKey="file"
                    sort={sort}
                    onSort={toggle}
                    className="wrap"
                  />
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((h, i) => (
                  <tr
                    key={`${h.document_id}-${h.version_id ?? i}`}
                    className={
                      h.document_status === 'archived' || h.version_status === 'archived'
                        ? 'row-archived'
                        : undefined
                    }
                  >
                    <td>{h.product_name}</td>
                    <td className="wrap">
                      <Link className="doc-link" to={`/documents/${h.document_id}`}>
                        {h.document_name}
                      </Link>
                      <BodyMatch hit={h} />
                    </td>
                    <td className="faint">{h.category_name}</td>
                    <td>
                      {h.version || h.revision ? (
                        <span className="rev">{h.version ?? h.revision}</span>
                      ) : (
                        <span className="faint small">-</span>
                      )}
                    </td>
                    <td className="mono">{h.document_number ?? '-'}</td>
                    <td className="faint">{h.language ?? '-'}</td>
                    <td className="faint">{fmtDate(h.revision_date)}</td>
                    <td>{h.uploaded_by_display_name ?? '-'}</td>
                    <td className="faint">{fmtDateTime(h.upload_date)}</td>
                    <td className="wrap faint small">{h.original_file_name ?? '-'}</td>
                    <td>
                      <span className="inline">
                        {h.version_id && <CurrentBadge isCurrent={h.is_current} />}
                        {h.version_id && (
                          <a
                            className="btn btn-sm"
                            href={api.downloadUrl(h.document_id, h.version_id)}
                          >
                            다운로드
                          </a>
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
