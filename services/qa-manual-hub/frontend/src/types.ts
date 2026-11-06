// Shapes mirror the FastAPI response models in backend/app/schemas.py.

export interface Me {
  id: string
  login_id: string
  display_name: string
  role: string
  is_active: boolean
  must_change_password: boolean
  last_login_at: string | null
  created_at: string
  updated_at: string
  is_admin: boolean
}

export interface UserRow {
  id: string
  login_id: string
  display_name: string
  role: string
  is_active: boolean
  must_change_password: boolean
  last_login_at: string | null
  created_at: string
  updated_at: string
}

export interface Product {
  id: string
  name: string
  code: string | null
  description: string | null
  is_active: boolean
  sort_order: number
  created_at: string
  updated_at: string
  document_count: number
  version_count: number
  created_by_display_name: string | null
  last_upload_at: string | null
}

export interface Category {
  id: string
  name: string
  description: string | null
  is_active: boolean
  sort_order: number
  created_at: string
  updated_at: string
  document_count: number
}

export interface StoredFile {
  id: string
  sha256: string
  byte_size: number
  original_file_name: string
  file_extension: string
  mime_type: string | null
  storage_backend: string
}

export interface Version {
  id: string
  document_id: string
  revision: string | null
  version: string | null
  document_number: string | null
  language: string | null
  revision_date: string | null
  revision_description: string | null
  comment: string | null
  uploaded_by_user_id: string | null
  uploaded_by_login_id: string
  uploaded_by_display_name: string
  upload_date: string
  status: string
  created_at: string
  stored_file: StoredFile
  is_current: boolean
  can_preview: boolean
}

export interface DocumentRow {
  id: string
  product_id: string
  category_id: string
  name: string
  description: string | null
  status: string
  created_at: string
  updated_at: string
  product_name: string
  category_name: string
  category_sort_order: number
  current_version_id: string | null
  current_revision: string | null
  current_version_label: string | null
  current_document_number: string | null
  current_language: string | null
  revision_date: string | null
  uploaded_by_display_name: string | null
  upload_date: string | null
  version_count: number
  created_by_display_name: string | null
  updated_by_display_name: string | null
}

export interface DocumentDetail extends DocumentRow {
  versions: Version[]
}

export interface DuplicateFileInfo {
  sha256: string
  product_name: string | null
  document_name: string | null
  version_label: string | null
  original_file_name: string | null
  upload_date: string | null
  uploaded_by_display_name: string | null
}

export interface VersionUploadResult {
  version: Version
  became_current: boolean
  duplicate_of: DuplicateFileInfo[]
  warning: string | null
}

export interface SearchHit {
  snippet?: string | null
  body_status?: BodyStatus
  body_detail?: string | null
  document_id: string
  document_name: string
  product_id: string
  product_name: string
  category_name: string
  document_status: string
  version_id: string | null
  revision: string | null
  version: string | null
  document_number: string | null
  language: string | null
  revision_date: string | null
  upload_date: string | null
  uploaded_by_display_name: string | null
  original_file_name: string | null
  version_status: string | null
  is_current: boolean
}

export type BodyStatus = 'pending' | 'ready' | 'truncated' | 'no_text' | 'encrypted' | 'unsupported' | 'too_large' | 'failed' | 'timeout' | 'missing' | 'hash_mismatch'
export interface BodyIndexResult {
  processed: number
  results: { id: string; status: BodyStatus; detail?: string | null }[]
  next_cursor: string | null
  done: boolean
  current_only: boolean
  remaining: number
}

export interface DashboardCounts {
  products: number
  products_active: number
  documents: number
  documents_active: number
  documents_archived: number
  documents_with_current: number
  versions: number
  users_active: number
  storage_bytes: number
}

export interface RecentUpload {
  version_id: string
  document_id: string
  document_name: string
  product_id: string
  product_name: string
  version_label: string | null
  revision: string | null
  version: string | null
  uploaded_by_display_name: string
  upload_date: string
  is_current: boolean
}

export interface ActivityEntry {
  id: number
  created_at: string
  action: string
  action_label: string
  actor_display_name: string | null
  actor_login_id: string | null
  product_name: string | null
  document_name: string | null
  version_label: string | null
  target_label: string | null
  detail: string | null
}

export interface Dashboard {
  counts: DashboardCounts
  recent_uploads: RecentUpload[]
  recent_current_changes: ActivityEntry[]
  recent_documents: DocumentRow[]
  recent_activity: ActivityEntry[]
}

export interface AuditRow {
  id: number
  created_at: string
  action: string
  action_label: string
  actor_login_id: string | null
  actor_display_name: string | null
  product_name: string | null
  document_name: string | null
  version_label: string | null
  target_label: string | null
  ip_address: string | null
  before_value: Record<string, unknown> | null
  after_value: Record<string, unknown> | null
  detail: string | null
}

export interface AppSettings {
  max_upload_mb: number
  allowed_extensions: string[]
  session_lifetime_hours: number
  password_min_length: number
  storage_root: string
  storage_backend: string
  app_version: string
}
