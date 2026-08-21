'use client'

import { useEffect, useState } from 'react'
import ProtectedRoute from '@/components/ProtectedRoute'
import Alert from '@/components/Alert'
import { api, extractErrorMessage } from '@/lib/api'
import type { CheckinHistoryItem } from '@/lib/types'

const statusColor: Record<string, string> = {
  approved: 'bg-green-100 text-green-800',
  pending: 'bg-yellow-100 text-yellow-800',
  flagged: 'bg-yellow-100 text-yellow-800',
  rejected: 'bg-red-100 text-red-800',
  appealed: 'bg-blue-100 text-blue-800',
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
    <div className="mt-2 space-y-2">
      {error && <Alert type="error">{error}</Alert>}
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Explain why this check-in should be reconsidered..."
        className="w-full border rounded px-3 py-2 text-sm"
        rows={3}
      />
      <button
        onClick={submit}
        disabled={submitting}
        className="bg-blue-700 text-white px-3 py-1.5 rounded text-sm disabled:opacity-50"
      >
        {submitting ? 'Submitting...' : 'Submit Appeal'}
      </button>
    </div>
  )
}

function CheckinRow({ item, onAppealed }: { item: CheckinHistoryItem; onAppealed: () => void }) {
  const [appealing, setAppealing] = useState(false)
  const canAppeal = item.status === 'flagged' || item.status === 'rejected'

  return (
    <li className="bg-white border rounded-lg p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium">{item.session_name ?? item.session_id}</p>
          <p className="text-xs text-gray-500">
            {item.course_code ? `${item.course_code} · ` : ''}
            {new Date(item.checked_in_at).toLocaleString()}
          </p>
        </div>
        <span className={`text-xs px-2 py-1 rounded-full font-medium ${statusColor[item.status] ?? 'bg-gray-100'}`}>
          {item.status}
        </span>
      </div>
      {typeof item.risk_score === 'number' && (
        <p className="text-xs text-gray-400 mt-1">risk {item.risk_score.toFixed(2)}</p>
      )}
      {canAppeal && !appealing && (
        <button
          onClick={() => setAppealing(true)}
          className="text-sm text-blue-700 underline mt-2"
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

  if (loading) return <p className="text-center text-gray-500">Loading...</p>
  if (error) return <Alert type="error">{error}</Alert>
  if (items.length === 0) {
    return <p className="text-sm text-gray-500">You haven&apos;t checked in to any sessions yet.</p>
  }

  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <CheckinRow key={item.id} item={item} onAppealed={load} />
      ))}
    </ul>
  )
}

export default function HistoryPage() {
  return (
    <ProtectedRoute>
      <main className="max-w-md mx-auto p-4 space-y-4">
        <h1 className="text-xl font-bold">Check-in History</h1>
        <HistoryList />
      </main>
    </ProtectedRoute>
  )
}
