import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { readFilters, useQuerySort, writeFilters } from '../queryState'
import {
  Alert,
  Card,
  CurrentBadge,
  Empty,
  Field,
  Loading,
  SortTh,
  StatusBadge,
  dateKey,
  fmtDate,
  fmtDateTime,
  type SortColumn,
} from '../components/ui'
import { DocumentForm } from './ProductDetail'
import type { Category, DocumentRow, Product } from '../types'

type SortKey =
  | 'product'
  | 'name'
  | 'category'
  | 'revision'
  | 'revisionDate'
  | 'uploader'
  | 'uploadDate'
  | 'versions'

// Defined once outside the component: a fresh array each render would make the
// memo inside useSort recompute on every keystroke in the filter box.
const COLUMNS: readonly SortColumn<DocumentRow, SortKey>[] = [
  { key: 'product', value: (d) => d.product_name },
  { key: 'name', value: (d) => d.name },
  { key: 'category', value: (d) => d.category_sort_order },
  { key: 'revision', value: (d) => d.current_version_label },
  { key: 'revisionDate', value: (d) => dateKey(d.revision_date) },
  { key: 'uploader', value: (d) => d.uploaded_by_display_name },
  { key: 'uploadDate', value: (d) => dateKey(d.upload_date) },
  { key: 'versions', value: (d) => d.version_count },
]

const BLANK = { product_id: '', category_id: '', status: 'active', q: '' }

export default function Documents() {
  const [rows, setRows] = useState<DocumentRow[] | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [params, setParams] = useSearchParams()
  const { product_id: productId, category_id: categoryId, status, q } = readFilters(params, BLANK)
  function setFilter(key: keyof typeof BLANK, value: string) {
    setParams((previous) => writeFilters(previous, { ...readFilters(previous, BLANK), [key]: value }, BLANK))
  }
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    Promise.all([api.products({ include_inactive: true }), api.categories()])
      .then(([p, c]) => {
        setProducts(p)
        setCategories(c)
      })
      .catch((e) => setError(e instanceof Error ? e.message : '불러오지 못했습니다.'))
  }, [])

  const load = useCallback(() => {
    setError(null)
    api
      .documents({
        product_id: productId || undefined,
        category_id: categoryId || undefined,
        status,
        q: q.trim() || undefined,
      })
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : '불러오지 못했습니다.'))
  }, [productId, categoryId, status, q])

  useEffect(load, [load])

  // Sorted in the browser: the endpoint returns the full filtered set, so
  // reordering is instant and costs no round-trip.
  const { sort, toggle, sorted } = useQuerySort(rows, COLUMNS)

  const productCount = useMemo(
    () => new Set((sorted ?? []).map((d) => d.product_id)).size,
    [sorted],
  )

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <div className="desc">
            전 제품의 문서를 한 화면에서 확인합니다. 표 머리글을 클릭하면 정렬됩니다.
          </div>
        </div>
        <div className="head-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => setCreating(true)}
            disabled={products.filter((p) => p.is_active).length === 0}
          >
            + 문서 등록
          </button>
        </div>
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div className="filter-bar">
          <Field label="Product">
            <select value={productId} onChange={(e) => setFilter('product_id', e.target.value)}>
              <option value="">전체</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.is_active ? '' : ' (비활성)'}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Category">
            <select value={categoryId} onChange={(e) => setFilter('category_id', e.target.value)}>
              <option value="">전체</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Status">
            <select value={status} onChange={(e) => setFilter('status', e.target.value)}>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="all">전체</option>
            </select>
          </Field>
          <Field label="문서 이름" className="grow">
            <input
              type="search"
              value={q}
              onChange={(e) => setFilter('q', e.target.value)}
              placeholder="부분 검색"
            />
          </Field>
          {(productId || categoryId || q || status !== 'active') && (
            <button
              type="button"
              onClick={() => {
                setParams(new URLSearchParams())
              }}
            >
              초기화
            </button>
          )}
        </div>
      </Card>

      <Card
        title="문서 목록"
        sub={
          sorted
            ? `${sorted.length}건${productCount > 1 ? ` · 제품 ${productCount}개` : ''}`
            : undefined
        }
        flush
      >
        {!sorted ? (
          <Loading />
        ) : sorted.length === 0 ? (
          <Empty title="조건에 맞는 문서가 없습니다">
            필터를 초기화하거나 검색어를 줄여 보세요.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <SortTh label="Product" sortKey="product" sort={sort} onSort={toggle} />
                  <SortTh
                    label="Document"
                    sortKey="name"
                    sort={sort}
                    onSort={toggle}
                    className="wrap"
                  />
                  <SortTh label="Category" sortKey="category" sort={sort} onSort={toggle} />
                  <SortTh
                    label="Current Revision"
                    sortKey="revision"
                    sort={sort}
                    onSort={toggle}
                  />
                  <SortTh
                    label="Revision Date"
                    sortKey="revisionDate"
                    sort={sort}
                    onSort={toggle}
                  />
                  <SortTh
                    label="Uploaded By"
                    sortKey="uploader"
                    sort={sort}
                    onSort={toggle}
                  />
                  <SortTh
                    label="Upload Date"
                    sortKey="uploadDate"
                    sort={sort}
                    onSort={toggle}
                  />
                  <SortTh
                    label="Ver."
                    sortKey="versions"
                    sort={sort}
                    onSort={toggle}
                    className="num"
                  />
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((d) => (
                  <tr
                    key={d.id}
                    className={d.status === 'archived' ? 'row-archived' : undefined}
                  >
                    <td>
                      <Link to={`/products/${d.product_id}`}>{d.product_name}</Link>
                    </td>
                    <td className="wrap">
                      <div className="stack">
                        <Link className="doc-link" to={`/documents/${d.id}`}>
                          {d.name}
                        </Link>
                        {d.status === 'archived' && <StatusBadge status={d.status} />}
                      </div>
                    </td>
                    <td className="faint">{d.category_name}</td>
                    <td>
                      {d.current_version_label ? (
                        <span className="inline">
                          <span className="rev">{d.current_version_label}</span>
                          <CurrentBadge isCurrent />
                        </span>
                      ) : (
                        <span className="faint small">버전 없음</span>
                      )}
                    </td>
                    <td className="faint">{fmtDate(d.revision_date)}</td>
                    <td>{d.uploaded_by_display_name ?? '-'}</td>
                    <td className="faint">{fmtDateTime(d.upload_date)}</td>
                    <td className="num">{d.version_count}</td>
                    <td>
                      {d.current_version_id && (
                        <a
                          className="btn btn-sm"
                          href={api.downloadUrl(d.id, d.current_version_id)}
                        >
                          다운로드
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {creating && (
        <DocumentForm
          productId={productId || products.find((p) => p.is_active)?.id || ''}
          categories={categories}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false)
            load()
          }}
        />
      )}
    </>
  )
}
