'use client'

import { useEffect, useState } from 'react'
import ProtectedRoute from '@/components/ProtectedRoute'
import Alert from '@/components/Alert'
import Spinner, { LoadingState } from '@/components/Spinner'
import { ClockIcon } from '@/components/icons'
import { api, extractErrorMessage } from '@/lib/api'
import { displayText } from '@/lib/text'
import type { CheckinHistoryItem } from '@/lib/types'

const statusColor: Record<string, string> = {
  approved: 'bg-green-50 text-green-700 ring-green-600/20',
  pending: 'bg-amber-50 text-amber-800 ring-amber-600/20',
  flagged: 'bg-amber-50 text-amber-800 ring-amber-600/20',
  rejected: 'bg-red-50 text-red-700 ring-red-600/20',
  appealed: 'bg-blue-50 text-blue-700 ring-blue-600/20',
}

function AppealForm({ checkinId, onDone }: { checkinId: string; onDone: () => void }) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (reason.trim().length < 10) {
      setError('Please provide a bit more detail (at least 10 characters).')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.post(`/checkins/${checkinId}/appeal`, { appeal_reason: reason })
      onDone()
    } catch (err) {
      setError(extractErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mt-3 space-y-3 border-t border-slate-100 pt-3">
      {error && <Alert type="error">{error}</Alert>}
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Explain why this check-in should be reconsidered..."
        aria-label="Appeal reason"
        className="field-input text-sm"
        rows={3}
      />
      <button onClick={submit} disabled={submitting} className="btn btn-primary">
        {submitting && <Spinner />}
        {submitting ? 'Submitting...' : 'Submit Appeal'}
      </button>
    </div>
  )
}

function CheckinRow({ item, onAppealed }: { item: CheckinHistoryItem; onAppealed: () => void }) {
  const [appealing, setAppealing] = useState(false)
  const canAppeal = item.status === 'flagged' || item.status === 'rejected'

  return (
    <li className="card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words font-semibold">
            {displayText(item.session_name) || item.session_id}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
            {item.course_code && (
              <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-semibold text-slate-700">
                {item.course_code}
              </span>
            )}
            <span>{new Date(item.checked_in_at).toLocaleString()}</span>
            {typeof item.risk_score === 'number' && (
              <span>· risk {item.risk_score.toFixed(2)}</span>
            )}
          </p>
        </div>
        <span
          className={`badge ring-1 ring-inset ${
            statusColor[item.status] ?? 'bg-slate-100 text-slate-700 ring-slate-500/20'
          }`}
        >
          {item.status}
        </span>
      </div>
      {canAppeal && !appealing && (
        <button
          onClick={() => setAppealing(true)}
          className="btn btn-secondary mt-3 px-3 py-1.5"
        >
          Appeal this check-in
        </button>
      )}
      {appealing && (
        <AppealForm
          checkinId={item.id}
          onDone={() => {
            setAppealing(false)
            onAppealed()
          }}
        />
      )}
    </li>
  )
}

function HistoryList() {
  const [items, setItems] = useState<CheckinHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<CheckinHistoryItem[]>('/checkins/my-checkins')
      setItems(res.data)
    } catch (err) {
      setError(extractErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  if (loading) return <LoadingState>Loading...</LoadingState>
  if (error) return <Alert type="error">{error}</Alert>
  if (items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-10 text-center">
        <ClockIcon className="mx-auto h-10 w-10 text-slate-300" />
        <p className="mt-3 font-semibold">No check-ins yet</p>
        <p className="mt-1 text-sm text-slate-500">
          You haven&apos;t checked in to any sessions yet.
        </p>
      </div>
    )
  }

  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <CheckinRow key={item.id} item={item} onAppealed={load} />
      ))}
    </ul>
  )
}

export default function HistoryPage() {
  return (
    <ProtectedRoute>
      <main className="page">
        <div>
          <h1 className="page-title">Check-in History</h1>
          <p className="page-subtitle">Your past check-ins and their review status.</p>
        </div>
        <HistoryList />
      </main>
    </ProtectedRoute>
  )
}
