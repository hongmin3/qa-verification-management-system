import { useCallback, useEffect, useState } from 'react'
import type { DragEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, parseFileName, sha256Hex } from '../api'
import {
  Alert,
  Card,
  ConfirmDialog,
  CurrentBadge,
  Empty,
  Field,
  Loading,
  Modal,
  StatusBadge,
  fmtBytes,
  fmtDate,
  fmtDateTime,
  useAsyncAction,
} from '../components/ui'
import type {
  AppSettings,
  Category,
  DocumentDetail as Detail,
  DuplicateFileInfo,
  Version,
} from '../types'

export default function DocumentDetail() {
  const { documentId = '' } = useParams()
  const [doc, setDoc] = useState<Detail | null>(null)
  const [categories, setCategories] = useState<Category[]>([])
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState(false)

  const [uploading, setUploading] = useState(false)
  const [editingDoc, setEditingDoc] = useState(false)
  const [editingVersion, setEditingVersion] = useState<Version | null>(null)
  const [previewVersionId, setPreviewVersionId] = useState<string | null>(null)
  const [viewerLoading, setViewerLoading] = useState(false)
  const [confirm, setConfirm] = useState<{
    title: string
    message: string
    label: string
    danger?: boolean
    run: () => Promise<void>
  } | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [detail, cats, cfg] = await Promise.all([
        api.document(documentId),
        api.categories(),
        api.settings(),
      ])
      setDoc(detail)
      setCategories(cats)
      setSettings(cfg)
    } catch (e) {
      setError(e instanceof Error ? e.message : '불러오지 못했습니다.')
    }
  }, [documentId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (previewVersionId) setViewerLoading(true)
  }, [previewVersionId])

  useEffect(() => {
    if (!doc) return
    setPreviewVersionId((prev) => {
      if (prev && doc.versions.some((v) => v.id === prev)) return prev
      const current = doc.versions.find(
        (v) => v.id === doc.current_version_id && v.can_preview,
      )
      if (current) return current.id
      const firstPreviewable = doc.versions.find((v) => v.can_preview)
      return firstPreviewable ? firstPreviewable.id : null
    })
  }, [doc])

  async function act(fn: () => Promise<void>, message: string) {
    setBusyAction(true)
    setError(null)
    setNotice(null)
    try {
      await fn()
      await load()
      setNotice(message)
    } catch (e) {
      setError(e instanceof Error ? e.message : '요청이 실패했습니다.')
    } finally {
      setBusyAction(false)
      setConfirm(null)
    }
  }

  if (error && !doc) return <Alert kind="error">{error}</Alert>
  if (!doc) return <Loading />

  const archived = doc.status === 'archived'
  const previewVersion = doc.versions.find((v) => v.id === previewVersionId) ?? null

  return (
    <>
      <div className="breadcrumb">
        <Link to="/products">Products</Link>
        <span>/</span>
        <Link to={`/products/${doc.product_id}`}>{doc.product_name}</Link>
        <span>/</span>
        <span>{doc.name}</span>
      </div>

      <div className="page-head">
        <div>
          <h1>
            {doc.name} {archived && <StatusBadge status={doc.status} />}
          </h1>
          <div className="desc">
            {doc.category_name}
            {doc.description && ` · ${doc.description}`}
          </div>
        </div>
        <div className="head-actions">
          <button type="button" onClick={() => setEditingDoc(true)} disabled={busyAction}>
            문서 정보 수정
          </button>
          {archived ? (
            <button
              type="button"
              onClick={() =>
                setConfirm({
                  title: '문서 복원',
                  message: `'${doc.name}' 문서를 활성 상태로 되돌립니다.`,
                  label: '복원',
                  run: () =>
                    act(async () => {
                      await api.restoreDocument(doc.id)
                    }, '문서를 복원했습니다.'),
                })
              }
              disabled={busyAction}
            >
              문서 복원
            </button>
          ) : (
            <button
              type="button"
              className="btn-danger"
              onClick={async () => {
                const check = await api.archiveCheck(doc.id).catch(() => null)
                setConfirm({
                  title: '문서 보관 (Archive)',
                  message:
                    check?.detail ??
                    `'${doc.name}' 문서를 보관 상태로 전환합니다. 파일은 삭제되지 않습니다.`,
                  label: '보관',
                  danger: true,
                  run: () =>
                    act(async () => {
                      await api.archiveDocument(doc.id)
                    }, '문서를 보관했습니다. 언제든 복원할 수 있습니다.'),
                })
              }}
              disabled={busyAction}
            >
              문서 보관
            </button>
          )}
          <button
            type="button"
            className="btn-primary"
            onClick={() => setUploading(true)}
            disabled={busyAction || archived}
            title={archived ? '보관된 문서에는 업로드할 수 없습니다.' : undefined}
          >
            + 새 버전 업로드
          </button>
        </div>
      </div>

      {error && <Alert kind="error" onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert kind="ok" onClose={() => setNotice(null)}>{notice}</Alert>}
      {warning && <Alert kind="warn" onClose={() => setWarning(null)}>{warning}</Alert>}
      {archived && (
        <Alert kind="warn">
          이 문서는 보관(Archived) 상태입니다. 이력과 파일은 모두 보존되어 있으며, 새 버전을
          올리려면 먼저 복원하세요.
        </Alert>
      )}

      <div className="stat-grid">
        <div className="stat">
          <div className="stat-label">Current Revision</div>
          <div className="stat-value">
            {doc.current_version_label ? (
              <span className="rev" style={{ fontSize: 20 }}>
                {doc.current_version_label}
              </span>
            ) : (
              <span className="faint" style={{ fontSize: 15 }}>
                등록된 버전 없음
              </span>
            )}
          </div>
          {doc.current_version_id && (
            <div className="stat-hint">
              <CurrentBadge isCurrent /> Revision Date {fmtDate(doc.revision_date)}
            </div>
          )}
        </div>
        <div className="stat">
          <div className="stat-label">Uploaded By</div>
          <div className="stat-value" style={{ fontSize: 17 }}>
            {doc.uploaded_by_display_name ?? '-'}
          </div>
          <div className="stat-hint">{fmtDateTime(doc.upload_date)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Document Number</div>
          <div className="stat-value mono" style={{ fontSize: 15 }}>
            {doc.current_document_number ?? '-'}
          </div>
          <div className="stat-hint">Language {doc.current_language ?? '-'}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Total Versions</div>
          <div className="stat-value">{doc.version_count}</div>
          <div className="stat-hint">모든 이력 보존</div>
        </div>
      </div>

      <Card
        title="매뉴얼 뷰어"
        sub={
          previewVersion
            ? `${previewVersion.version ?? previewVersion.revision} · ${previewVersion.stored_file.original_file_name}`
            : undefined
        }
      >
        {previewVersion ? (
          <>
            <div className="viewer-toolbar">
              <a className="btn btn-sm" href={api.downloadUrl(doc.id, previewVersion.id)}>
                다운로드
              </a>
              {previewVersion.id !== doc.current_version_id && (
                <span className="faint small">
                  Current 버전이 아닌 다른 버전을 보고 있습니다. 아래 이력에서 전환할 수
                  있습니다.
                </span>
              )}
            </div>
            <div className="viewer-frame-wrap">
              {viewerLoading && (
                <div className="viewer-loading">
                  <span className="spinner" aria-hidden="true" />
                  <span>문서를 불러오는 중입니다...</span>
                  <span className="faint small">
                    처음 여는 문서는 변환에 몇 초 걸릴 수 있습니다.
                  </span>
                </div>
              )}
              <iframe
                key={previewVersion.id}
                className="preview-frame"
                src={api.previewUrl(doc.id, previewVersion.id)}
                title="manual viewer"
                onLoad={() => setViewerLoading(false)}
              />
            </div>
          </>
        ) : (
          <Empty title="미리보기를 지원하지 않는 문서입니다">
            이 파일 형식은 뷰어로 열 수 없습니다. "다운로드" 로 확인하세요.
          </Empty>
        )}
      </Card>

      <Card title="Revision History" sub={`${doc.versions.length}건 — 최신순`}>
        {doc.versions.length === 0 ? (
          <Empty title="아직 업로드된 버전이 없습니다">
            "새 버전 업로드" 로 첫 파일을 등록하세요.
          </Empty>
        ) : (
          <div className="timeline">
            {doc.versions.map((v) => (
              <div
                key={v.id}
                className={[
                  'tl-item',
                  v.is_current ? 'is-current' : '',
                  v.status === 'archived' ? 'is-archived' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                <span className="tl-dot" />
                <div className="tl-card">
                  <div className="tl-head">
                    <span className="rev">{v.version ?? v.revision}</span>
                    {v.version && v.revision && (
                      <span className="badge badge-history badge-mono">
                        Rev {v.revision}
                      </span>
                    )}
                    <CurrentBadge isCurrent={v.is_current} />
                    {v.status === 'archived' && <StatusBadge status={v.status} />}
                    <span className="spacer" />
                    <span className="faint small">{fmtDateTime(v.upload_date)}</span>
                  </div>

                  <div className="tl-body">
                    <div className="meta-grid">
                      <div className="meta-item">
                        <div className="k">Uploaded By</div>
                        <div className="v">
                          {v.uploaded_by_display_name}{' '}
                          <span className="faint small">({v.uploaded_by_login_id})</span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <div className="k">Revision Date</div>
                        <div className="v">{fmtDate(v.revision_date)}</div>
                      </div>
                      <div className="meta-item">
                        <div className="k">Document No.</div>
                        <div className="v mono">{v.document_number ?? '-'}</div>
                      </div>
                      <div className="meta-item">
                        <div className="k">Language</div>
                        <div className="v">{v.language ?? '-'}</div>
                      </div>
                      <div className="meta-item">
                        <div className="k">File</div>
                        <div className="v">
                          {v.stored_file.original_file_name}
                          <span className="faint small">
                            {' '}
                            · {fmtBytes(v.stored_file.byte_size)}
                          </span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <div className="k">SHA-256</div>
                        <div className="v hash" title={v.stored_file.sha256}>
                          {v.stored_file.sha256.slice(0, 24)}…
                        </div>
                      </div>
                    </div>

                    {v.revision_description && (
                      <div className="revdesc">
                        <span className="k">Revision Description</span>
                        {v.revision_description}
                      </div>
                    )}
                    {v.comment && (
                      <div className="revdesc">
                        <span className="k">Comment</span>
                        {v.comment}
                      </div>
                    )}
                  </div>

                  <div className="tl-foot">
                    <a className="btn btn-sm" href={api.downloadUrl(doc.id, v.id)}>
                      다운로드
                    </a>
                    {v.can_preview && (
                      <button
                        type="button"
                        className={v.id === previewVersionId ? 'btn-sm btn-primary' : 'btn-sm'}
                        disabled={v.id === previewVersionId}
                        onClick={() => setPreviewVersionId(v.id)}
                      >
                        {v.id === previewVersionId ? '뷰어에 표시 중' : '뷰어로 보기'}
                      </button>
                    )}
                    {!v.is_current && v.status === 'active' && (
                      <button
                        type="button"
                        className="btn-sm btn-primary"
                        disabled={busyAction}
                        onClick={() =>
                          setConfirm({
                            title: 'Set as Current',
                            message: `'${v.version ?? v.revision}' 을 Current 버전으로 지정합니다. 기존 Current(${
                              doc.current_version_label ?? '없음'
                            })는 이력으로 남고 삭제되지 않습니다.`,
                            label: 'Current 로 지정',
                            run: () =>
                              act(async () => {
                                await api.setCurrent(doc.id, v.id)
                              }, `Current 버전을 ${v.version ?? v.revision} 으로 변경했습니다.`),
                          })
                        }
                      >
                        Set as Current
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn-sm"
                      onClick={() => setEditingVersion(v)}
                      disabled={busyAction}
                    >
                      메타데이터 수정
                    </button>
                    {v.status === 'active' ? (
                      <button
                        type="button"
                        className="btn-sm btn-danger"
                        disabled={busyAction || v.is_current}
                        title={
                          v.is_current
                            ? 'Current 버전은 보관할 수 없습니다. 다른 버전을 Current 로 먼저 지정하세요.'
                            : undefined
                        }
                        onClick={() =>
                          setConfirm({
                            title: '버전 보관',
                            message: `'${v.version ?? v.revision}' 버전을 보관합니다. 파일은 삭제되지 않고 저장소에 그대로 남습니다.`,
                            label: '보관',
                            danger: true,
                            run: () =>
                              act(async () => {
                                await api.archiveVersion(doc.id, v.id)
                              }, '버전을 보관했습니다.'),
                          })
                        }
                      >
                        보관
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn-sm"
                        disabled={busyAction}
                        onClick={() =>
                          act(async () => {
                            await api.restoreVersion(doc.id, v.id)
                          }, '버전을 복원했습니다.')
                        }
                      >
                        복원
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {uploading && settings && (
        <UploadForm
          documentId={doc.id}
          currentLabel={doc.current_version_label}
          settings={settings}
          onClose={() => setUploading(false)}
          onDone={async (result) => {
            setUploading(false)
            await load()
            setNotice(
              result.became_current
                ? `버전 ${result.version.version ?? result.version.revision} 을 업로드했고 Current 로 지정했습니다.`
                : `버전 ${result.version.version ?? result.version.revision} 을 업로드했습니다. Current 는 변경되지 않았습니다.`,
            )
            setWarning(result.warning)
          }}
        />
      )}

      {editingDoc && (
        <DocumentEditForm
          doc={doc}
          categories={categories}
          onClose={() => setEditingDoc(false)}
          onSaved={async () => {
            setEditingDoc(false)
            await load()
            setNotice('문서 정보를 수정했습니다.')
          }}
        />
      )}

      {editingVersion && (
        <VersionEditForm
          documentId={doc.id}
          version={editingVersion}
          onClose={() => setEditingVersion(null)}
          onSaved={async () => {
            setEditingVersion(null)
            await load()
            setNotice('버전 메타데이터를 수정했습니다.')
          }}
        />
      )}

      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          confirmLabel={confirm.label}
          danger={confirm.danger}
          busy={busyAction}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void confirm.run()}
        />
      )}
    </>
  )
}

// --------------------------------------------------------------------------- //
// upload
// --------------------------------------------------------------------------- //
function UploadForm({
  documentId,
  currentLabel,
  settings,
  onClose,
  onDone,
}: {
  documentId: string
  currentLabel: string | null
  settings: AppSettings
  onClose: () => void
  onDone: (result: import('../types').VersionUploadResult) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [revision, setRevision] = useState('')
  const [version, setVersion] = useState('')
  const [documentNumber, setDocumentNumber] = useState('')
  const [language, setLanguage] = useState('')
  // Revision Date is the date printed in the document, not the upload date, so
  // it stays blank unless the file name carries one (SPEC 13.6 #3).
  const [revisionDate, setRevisionDate] = useState('')
  const [revisionDescription, setRevisionDescription] = useState('')
  const [comment, setComment] = useState('')
  const [setAsCurrent, setSetAsCurrent] = useState(true)
  const [duplicates, setDuplicates] = useState<DuplicateFileInfo[] | null>(null)
  const [hashing, setHashing] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const { busy, error, setError, onSubmit } = useAsyncAction()

  const maxBytes = settings.max_upload_mb * 1024 * 1024

  async function pick(selected: File | null) {
    setFile(selected)
    setDuplicates(null)
    setError(null)
    if (!selected) return

    const ext = selected.name.split('.').pop()?.toLowerCase() ?? ''
    if (!settings.allowed_extensions.includes(ext)) {
      setError(
        `'${ext}' 확장자는 허용되지 않습니다. 허용: ${settings.allowed_extensions.join(', ')}`,
      )
      return
    }
    if (selected.size > maxBytes) {
      setError(
        `파일 크기 ${fmtBytes(selected.size)} 가 제한 ${settings.max_upload_mb} MB 를 초과합니다.`,
      )
      return
    }

    // Best-effort guess from the file name. Formats are never enforced (see the
    // hint text below), so anything not found is simply left blank for the user
    // to fill in, including the Revision Date.
    const guess = parseFileName(selected.name)
    setVersion(guess.version ?? '')
    setRevision(guess.revision ?? '')
    setDocumentNumber(guess.documentNumber ?? '')
    setLanguage(guess.language ?? '')
    setRevisionDate(guess.revisionDate ?? '')

    // Hash locally so the duplicate warning appears before the bytes are sent.
    setHashing(true)
    try {
      const digest = await sha256Hex(selected)
      if (digest) setDuplicates(await api.duplicateCheck(digest))
    } catch {
      // Non-fatal: the server checks again after upload.
    } finally {
      setHashing(false)
    }
  }

  function onDrop(e: DragEvent<HTMLLabelElement>) {
    e.preventDefault()
    setDragActive(false)
    void pick(e.dataTransfer.files?.[0] ?? null)
  }

  const submit = onSubmit(async () => {
    if (!file) throw new Error('업로드할 파일을 선택하세요.')
    if (!revision.trim() && !version.trim()) {
      throw new Error('Revision 또는 Version 중 하나는 반드시 입력해야 합니다.')
    }
    const form = new FormData()
    form.append('file', file)
    if (revision.trim()) form.append('revision', revision.trim())
    if (version.trim()) form.append('version', version.trim())
    if (documentNumber.trim()) form.append('document_number', documentNumber.trim())
    if (language.trim()) form.append('language', language.trim())
    if (revisionDate) form.append('revision_date', revisionDate)
    if (revisionDescription.trim())
      form.append('revision_description', revisionDescription.trim())
    if (comment.trim()) form.append('comment', comment.trim())
    form.append('set_as_current', setAsCurrent ? 'true' : 'false')

    onDone(await api.uploadVersion(documentId, form))
  })

  return (
    <Modal
      title="새 버전 업로드"
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button
            type="submit"
            form="upload-form"
            className="btn-primary"
            disabled={busy || hashing || !file}
          >
            {busy ? '업로드 중...' : '업로드'}
          </button>
        </>
      }
    >
      <form id="upload-form" onSubmit={submit}>
        {error && <Alert kind="error">{error}</Alert>}

        <Field
          label="파일 *"
          hint={`최대 ${settings.max_upload_mb} MB · 허용 확장자: ${settings.allowed_extensions.join(', ')}`}
        >
          <label
            className={dragActive ? 'dropzone is-active' : 'dropzone'}
            onDragOver={(e) => {
              e.preventDefault()
              setDragActive(true)
            }}
            onDragLeave={(e) => {
              e.preventDefault()
              setDragActive(false)
            }}
            onDrop={onDrop}
          >
            <input
              type="file"
              onChange={(e) => void pick(e.target.files?.[0] ?? null)}
              required
            />
            <span className="dropzone-text">
              {file
                ? `${file.name} · ${fmtBytes(file.size)}`
                : '파일을 여기로 끌어다 놓거나 클릭해서 선택하세요'}
            </span>
          </label>
        </Field>
        {file && hashing && (
          <p className="small muted" style={{ marginTop: -6 }}>
            SHA-256 계산 중...
          </p>
        )}

        {duplicates && duplicates.length > 0 && (
          <Alert kind="warn">
            동일한 내용의 파일이 이미 등록되어 있습니다.
            <ul>
              {duplicates.map((d, i) => (
                <li key={i}>
                  {[d.product_name, d.document_name, d.version_label]
                    .filter(Boolean)
                    .join(' / ')}{' '}
                  — {d.original_file_name} ({d.uploaded_by_display_name ?? '-'},{' '}
                  {fmtDateTime(d.upload_date)})
                </li>
              ))}
            </ul>
            업무상 필요하다면 그대로 별도 버전으로 등록할 수 있습니다.
          </Alert>
        )}

        <div className="divider" />

        <div className="form-grid">
          <Field label="Version" hint="예: V1.0.12W1, 1.1, 2026.07">
            <input
              type="text"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              maxLength={64}
              placeholder="V1.0.12W1"
            />
          </Field>
          <Field label="Revision" hint="예: Rev.1.3, R2, A">
            <input
              type="text"
              value={revision}
              onChange={(e) => setRevision(e.target.value)}
              maxLength={64}
              placeholder="Rev.1.3"
            />
          </Field>
          <Field label="Document Number">
            <input
              type="text"
              value={documentNumber}
              onChange={(e) => setDocumentNumber(e.target.value)}
              maxLength={128}
            />
          </Field>
          <Field label="Language" hint="예: KO, EN">
            <input
              type="text"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              maxLength={32}
            />
          </Field>
          <Field label="Revision Date">
            <input
              type="date"
              value={revisionDate}
              onChange={(e) => setRevisionDate(e.target.value)}
            />
          </Field>
        </div>

        <p className="small faint" style={{ marginTop: -4 }}>
          Version 과 Revision 형식은 시스템이 강제하지 않습니다. 문서에 적힌 값을 그대로
          입력하세요. 둘 중 하나는 필수입니다. 파일명에서 추정 가능한 값은 자동으로
          채워지며, 다르면 직접 수정하면 됩니다.
        </p>

        <Field label="Revision Description" hint="이번 개정에서 변경된 내용">
          <textarea
            value={revisionDescription}
            onChange={(e) => setRevisionDescription(e.target.value)}
            placeholder={'- Image Tool 설명 변경\n- Setting 내용 변경'}
          />
        </Field>
        <Field label="Comment">
          <textarea value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>

        <label className="check">
          <input
            type="checkbox"
            checked={setAsCurrent}
            onChange={(e) => setSetAsCurrent(e.target.checked)}
          />
          이 버전을 Current 로 지정 (기본)
        </label>
        <p className="small faint" style={{ marginTop: -8 }}>
          {setAsCurrent
            ? `업로드 후 Current 가 ${currentLabel ?? '없음'} → 이 버전으로 바뀝니다. 기존 버전은 이력으로 보존됩니다.`
            : '과거 Legacy 문서를 뒤늦게 등록할 때 체크를 해제하세요. Current 는 그대로 유지됩니다.'}
        </p>

        <Alert kind="info">
          업로더는 현재 로그인한 계정으로 자동 기록됩니다. 이름을 직접 입력하지 않습니다.
        </Alert>
      </form>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// edit forms
// --------------------------------------------------------------------------- //
function DocumentEditForm({
  doc,
  categories,
  onClose,
  onSaved,
}: {
  doc: Detail
  categories: Category[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(doc.name)
  const [categoryId, setCategoryId] = useState(doc.category_id)
  const [description, setDescription] = useState(doc.description ?? '')
  const { busy, error, onSubmit } = useAsyncAction()

  const submit = onSubmit(async () => {
    await api.updateDocument(doc.id, {
      name: name.trim(),
      category_id: categoryId,
      description: description.trim() || null,
    })
    onSaved()
  })

  return (
    <Modal
      title="문서 정보 수정"
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button type="submit" form="doc-edit" className="btn-primary" disabled={busy}>
            {busy ? '저장 중...' : '저장'}
          </button>
        </>
      }
    >
      <form id="doc-edit" onSubmit={submit}>
        {error && <Alert kind="error">{error}</Alert>}
        <Field label="Document Name *">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={255}
          />
        </Field>
        <Field label="Document Category">
          <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
            {!categories.some((c) => c.id === doc.category_id) && (
              <option value={doc.category_id}>{doc.category_name} (비활성)</option>
            )}
          </select>
        </Field>
        <Field label="Description">
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
      </form>
    </Modal>
  )
}

function VersionEditForm({
  documentId,
  version,
  onClose,
  onSaved,
}: {
  documentId: string
  version: Version
  onClose: () => void
  onSaved: () => void
}) {
  const [rev, setRev] = useState(version.revision ?? '')
  const [ver, setVer] = useState(version.version ?? '')
  const [docNo, setDocNo] = useState(version.document_number ?? '')
  const [lang, setLang] = useState(version.language ?? '')
  const [revDate, setRevDate] = useState(version.revision_date ?? '')
  const [revDesc, setRevDesc] = useState(version.revision_description ?? '')
  const [comment, setComment] = useState(version.comment ?? '')
  const { busy, error, onSubmit } = useAsyncAction()

  const submit = onSubmit(async () => {
    await api.updateVersion(documentId, version.id, {
      revision: rev.trim() || null,
      version: ver.trim() || null,
      document_number: docNo.trim() || null,
      language: lang.trim() || null,
      revision_date: revDate || null,
      revision_description: revDesc.trim() || null,
      comment: comment.trim() || null,
    })
    onSaved()
  })

  return (
    <Modal
      title={`버전 메타데이터 수정 — ${version.version ?? version.revision}`}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button type="submit" form="ver-edit" className="btn-primary" disabled={busy}>
            {busy ? '저장 중...' : '저장'}
          </button>
        </>
      }
    >
      <form id="ver-edit" onSubmit={submit}>
        {error && <Alert kind="error">{error}</Alert>}
        <div className="form-grid">
          <Field label="Version">
            <input type="text" value={ver} onChange={(e) => setVer(e.target.value)} maxLength={64} />
          </Field>
          <Field label="Revision">
            <input type="text" value={rev} onChange={(e) => setRev(e.target.value)} maxLength={64} />
          </Field>
          <Field label="Document Number">
            <input
              type="text"
              value={docNo}
              onChange={(e) => setDocNo(e.target.value)}
              maxLength={128}
            />
          </Field>
          <Field label="Language">
            <input type="text" value={lang} onChange={(e) => setLang(e.target.value)} maxLength={32} />
          </Field>
          <Field label="Revision Date">
            <input type="date" value={revDate} onChange={(e) => setRevDate(e.target.value)} />
          </Field>
        </div>
        <Field label="Revision Description">
          <textarea value={revDesc} onChange={(e) => setRevDesc(e.target.value)} />
        </Field>
        <Field label="Comment">
          <textarea value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
        <Alert kind="info">
          파일 자체는 변경되지 않습니다. 파일을 바꾸려면 새 버전으로 업로드하세요. 수정 내역은
          Audit Log 에 기록됩니다.
        </Alert>
      </form>
    </Modal>
  )
}
