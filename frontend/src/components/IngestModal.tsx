import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, IngestReport, IngestRegistryItem } from '../lib/api'

interface Props {
  onClose: () => void
}

type Tab = 'upload' | 'library'

const ACCEPTED = [
  '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv', '.tsv',
  '.pptx', '.ppt', '.md', '.mdx', '.txt', '.log',
  '.png', '.jpg', '.jpeg', '.gif', '.webp',
].join(',')

const STATUS_COLOR: Record<string, string> = {
  SUCCESS:          'var(--green, #22c55e)',
  SKIPPED:          'var(--text-muted)',
  FAILED:           '#ef4444',
  EMPTY_DOCUMENT:   '#f59e0b',
  UNSUPPORTED_TYPE: '#f59e0b',
}

export function IngestModal({ onClose }: Props) {
  const [tab, setTab] = useState<Tab>('upload')
  const [dragging, setDragging] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [reports, setReports] = useState<IngestReport[]>([])
  const [library, setLibrary] = useState<IngestRegistryItem[]>([])
  const [libLoading, setLibLoading] = useState(false)
  const [libError, setLibError] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Load library when switching to that tab
  useEffect(() => {
    if (tab === 'library') loadLibrary()
  }, [tab])

  async function loadLibrary() {
    setLibLoading(true)
    setLibError('')
    try {
      const data = await api.getIngestRegistry()
      setLibrary(data.documents)
    } catch (e: any) {
      setLibError(e.message || 'Failed to load library')
    } finally {
      setLibLoading(false)
    }
  }

  // ── Drag & drop ──────────────────────────────────────────────────────────

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(true)
  }, [])

  const onDragLeave = useCallback(() => setDragging(false), [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const dropped = Array.from(e.dataTransfer.files)
    setFiles(prev => [...prev, ...dropped])
    setReports([])
  }, [])

  const onFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files || [])
    setFiles(prev => [...prev, ...selected])
    setReports([])
  }

  const removeFile = (i: number) => setFiles(prev => prev.filter((_, idx) => idx !== i))

  // ── Upload ───────────────────────────────────────────────────────────────

  async function handleUpload() {
    if (!files.length) return
    setUploading(true)
    setReports([])
    try {
      const result = await api.ingestDocuments(files)
      setReports(result.reports)
      setFiles([])
    } catch (e: any) {
      setReports([{
        doc_id: '', file_name: 'upload', doc_type: '', mode: 'INSERT',
        status: 'FAILED', chunks_created: 0, chunks_skipped: 0,
        chunks_failed: 0, embedding_model: '', chunk_strategy: '',
        chunk_size: 512, overlap_pct: 20, version: 1, processing_ms: 0,
        warnings: [], indexed_at: '', error: e.message,
      }])
    } finally {
      setUploading(false)
    }
  }

  // ── Delete ───────────────────────────────────────────────────────────────

  async function handleDelete(filename: string) {
    if (!confirm(`Delete "${filename}" from the knowledge base?`)) return
    setDeleting(filename)
    try {
      await api.deleteIngestEntry(filename)
      setLibrary(prev => prev.filter(d => d.file_name !== filename))
    } catch (e: any) {
      alert(`Delete failed: ${e.message}`)
    } finally {
      setDeleting(null)
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div style={s.overlay} onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div style={s.modal}>
        {/* Header */}
        <div style={s.header}>
          <span style={s.title}>Knowledge Base</span>
          <button style={s.closeBtn} onClick={onClose}>✕</button>
        </div>

        {/* Tabs */}
        <div style={s.tabs}>
          <button style={{ ...s.tab, ...(tab === 'upload' ? s.tabActive : {}) }} onClick={() => setTab('upload')}>
            Upload Documents
          </button>
          <button style={{ ...s.tab, ...(tab === 'library' ? s.tabActive : {}) }} onClick={() => setTab('library')}>
            Document Library
          </button>
        </div>

        {/* ── Upload tab ── */}
        {tab === 'upload' && (
          <div style={s.body}>
            {/* Drop zone */}
            <div
              style={{ ...s.dropzone, ...(dragging ? s.dropzoneActive : {}) }}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED}
                multiple
                style={{ display: 'none' }}
                onChange={onFileInput}
              />
              <span style={s.dropIcon}>📄</span>
              <span style={s.dropText}>
                {dragging ? 'Drop files here' : 'Drag & drop files or click to browse'}
              </span>
              <span style={s.dropHint}>
                PDF · Word · Excel · CSV · PowerPoint · Markdown · TXT · Images
              </span>
            </div>

            {/* File queue */}
            {files.length > 0 && (
              <div style={s.fileList}>
                {files.map((f, i) => (
                  <div key={i} style={s.fileRow}>
                    <span style={s.fileName}>{f.name}</span>
                    <span style={s.fileSize}>{_fmtSize(f.size)}</span>
                    <button style={s.removeBtn} onClick={() => removeFile(i)}>✕</button>
                  </div>
                ))}
              </div>
            )}

            {/* Action */}
            <button
              style={{ ...s.uploadBtn, ...(uploading || !files.length ? s.uploadBtnDisabled : {}) }}
              onClick={handleUpload}
              disabled={uploading || !files.length}
            >
              {uploading ? 'Indexing…' : `Index ${files.length} file${files.length !== 1 ? 's' : ''}`}
            </button>

            {/* Reports */}
            {reports.length > 0 && (
              <div style={s.reports}>
                <div style={s.reportsTitle}>Ingestion Results</div>
                {reports.map((r, i) => (
                  <div key={i} style={s.reportCard}>
                    <div style={s.reportHeader}>
                      <span style={s.reportFile}>{r.file_name}</span>
                      <span style={{ ...s.reportStatus, color: STATUS_COLOR[r.status] || '#888' }}>
                        {r.status}
                      </span>
                    </div>
                    {r.status === 'SUCCESS' && (
                      <div style={s.reportDetail}>
                        {r.chunks_created} chunks indexed · {r.embedding_model} ·{' '}
                        {r.chunk_strategy} · v{r.version} · {r.processing_ms}ms
                      </div>
                    )}
                    {r.status === 'SKIPPED' && (
                      <div style={s.reportDetail}>Content unchanged — already up to date</div>
                    )}
                    {r.error && <div style={s.reportError}>{r.error}</div>}
                    {r.warnings.map((w, wi) => (
                      <div key={wi} style={s.reportWarn}>⚠ {w}</div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Library tab ── */}
        {tab === 'library' && (
          <div style={s.body}>
            <div style={s.libHeader}>
              <span style={s.libCount}>
                {libLoading ? 'Loading…' : `${library.length} documents indexed`}
              </span>
              <button style={s.refreshBtn} onClick={loadLibrary} disabled={libLoading}>
                Refresh
              </button>
            </div>

            {libError && <div style={s.reportError}>{libError}</div>}

            {!libLoading && library.length === 0 && !libError && (
              <div style={s.empty}>No documents indexed yet. Upload some files to get started.</div>
            )}

            {library.map((doc) => (
              <div key={doc.id} style={s.libCard}>
                <div style={s.libCardLeft}>
                  <span style={s.libFileName}>{doc.file_name}</span>
                  <span style={s.libMeta}>
                    {doc.doc_type.toUpperCase()} · {doc.chunk_count} chunks · v{doc.version}
                    {doc.indexed_at ? ` · ${_fmtDate(doc.indexed_at)}` : ''}
                  </span>
                </div>
                <button
                  style={{ ...s.deleteBtn, ...(deleting === doc.file_name ? s.deleteBtnDisabled : {}) }}
                  onClick={() => handleDelete(doc.file_name)}
                  disabled={deleting === doc.file_name}
                  title="Remove from knowledge base"
                >
                  {deleting === doc.file_name ? '…' : '🗑'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function _fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString()
  } catch {
    return iso
  }
}

// ── Styles ────────────────────────────────────────────────────────────────────

const s: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed', inset: 0, zIndex: 1000,
    background: 'rgba(0,0,0,0.55)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  },
  modal: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: '12px',
    width: '600px', maxWidth: '96vw',
    maxHeight: '90vh',
    display: 'flex', flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    padding: '16px 20px',
    borderBottom: '1px solid var(--border)',
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    flexShrink: 0,
  },
  title: { fontWeight: 700, fontSize: '16px' },
  closeBtn: {
    color: 'var(--text-muted)', fontSize: '18px',
    padding: '0 4px', cursor: 'pointer',
  },
  tabs: {
    display: 'flex',
    borderBottom: '1px solid var(--border)',
    flexShrink: 0,
  },
  tab: {
    padding: '10px 20px', fontSize: '13px',
    color: 'var(--text-muted)', cursor: 'pointer',
    borderBottom: '2px solid transparent',
  },
  tabActive: {
    color: 'var(--primary)',
    borderBottom: '2px solid var(--primary)',
    fontWeight: 600,
  },
  body: {
    padding: '20px', overflowY: 'auto', flex: 1,
    display: 'flex', flexDirection: 'column', gap: '14px',
  },
  dropzone: {
    border: '2px dashed var(--border)',
    borderRadius: '10px',
    padding: '36px 20px',
    textAlign: 'center',
    cursor: 'pointer',
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '6px',
    transition: 'border-color 0.15s, background 0.15s',
  },
  dropzoneActive: {
    borderColor: 'var(--primary)',
    background: 'var(--surface-2, rgba(99,102,241,0.06))',
  },
  dropIcon: { fontSize: '32px', marginBottom: '4px' },
  dropText: { fontSize: '14px', fontWeight: 500 },
  dropHint: { fontSize: '12px', color: 'var(--text-muted)' },
  fileList: {
    display: 'flex', flexDirection: 'column', gap: '6px',
    maxHeight: '160px', overflowY: 'auto',
  },
  fileRow: {
    display: 'flex', alignItems: 'center', gap: '8px',
    background: 'var(--surface-2, rgba(255,255,255,0.04))',
    borderRadius: '6px', padding: '6px 10px',
  },
  fileName: { flex: 1, fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  fileSize: { fontSize: '12px', color: 'var(--text-muted)', flexShrink: 0 },
  removeBtn: { color: 'var(--text-muted)', fontSize: '12px', padding: '0 4px', cursor: 'pointer', flexShrink: 0 },
  uploadBtn: {
    background: 'var(--primary)', color: '#fff',
    padding: '10px 24px', borderRadius: 'var(--radius, 8px)',
    fontWeight: 600, fontSize: '14px', cursor: 'pointer',
    alignSelf: 'flex-start',
    transition: 'opacity 0.15s',
  },
  uploadBtnDisabled: { opacity: 0.45, cursor: 'not-allowed' },
  reports: { display: 'flex', flexDirection: 'column', gap: '8px' },
  reportsTitle: { fontWeight: 600, fontSize: '13px', marginBottom: '2px' },
  reportCard: {
    background: 'var(--surface-2, rgba(255,255,255,0.03))',
    border: '1px solid var(--border)',
    borderRadius: '8px', padding: '10px 14px',
    display: 'flex', flexDirection: 'column', gap: '4px',
  },
  reportHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  reportFile: { fontWeight: 500, fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '70%' },
  reportStatus: { fontSize: '12px', fontWeight: 700, flexShrink: 0 },
  reportDetail: { fontSize: '12px', color: 'var(--text-muted)' },
  reportError: { fontSize: '12px', color: '#ef4444', marginTop: '2px' },
  reportWarn: { fontSize: '12px', color: '#f59e0b' },
  // Library tab
  libHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  libCount: { fontSize: '13px', color: 'var(--text-muted)' },
  refreshBtn: {
    padding: '4px 12px', borderRadius: 'var(--radius, 8px)',
    fontSize: '12px', border: '1px solid var(--border)',
    color: 'var(--text-muted)', cursor: 'pointer',
  },
  libCard: {
    border: '1px solid var(--border)',
    borderRadius: '8px', padding: '10px 14px',
    display: 'flex', alignItems: 'center', gap: '12px',
  },
  libCardLeft: { flex: 1, display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 },
  libFileName: { fontSize: '13px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  libMeta: { fontSize: '11px', color: 'var(--text-muted)' },
  deleteBtn: { fontSize: '16px', cursor: 'pointer', padding: '4px', flexShrink: 0, opacity: 0.7 },
  deleteBtnDisabled: { opacity: 0.3, cursor: 'not-allowed' },
  empty: { fontSize: '13px', color: 'var(--text-muted)', textAlign: 'center', padding: '24px 0' },
}
