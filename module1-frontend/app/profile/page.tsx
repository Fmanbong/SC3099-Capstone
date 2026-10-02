'use client'

import { useEffect, useState } from 'react'
import ProtectedRoute from '@/components/ProtectedRoute'
import Alert from '@/components/Alert'
import Spinner from '@/components/Spinner'
import { MonitorIcon, PhoneIcon } from '@/components/icons'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'
import { displayText } from '@/lib/text'
import type { Device, User } from '@/lib/types'

const sectionHeading = 'text-sm font-semibold uppercase tracking-wide text-slate-500'

/** First letters of the first and last name, for the avatar circle. */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  const first = words[0][0]
  const last = words.length > 1 ? words[words.length - 1][0] : ''
  return (first + last).toUpperCase()
}

function ConsentToggle({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-4 py-3">
      <span className="text-sm">{label}</span>
      {/* Still a real checkbox, hidden visually; the span after it draws the switch. */}
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden="true"
        className="relative h-6 w-11 shrink-0 rounded-full bg-slate-300 transition-colors after:absolute after:left-0.5 after:top-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:shadow after:transition-transform peer-checked:bg-blue-700 peer-checked:after:translate-x-5 peer-focus-visible:ring-2 peer-focus-visible:ring-blue-600 peer-focus-visible:ring-offset-2"
      />
    </label>
  )
}

function DeviceList() {
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .get<Device[]>('/devices/my-devices')
      .then((res) => setDevices(res.data))
      .catch(() => setDevices([]))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return null
  if (devices.length === 0) {
    return <p className="text-sm text-slate-500">No devices registered yet.</p>
  }

  return (
    <ul className="divide-y divide-slate-100">
      {devices.map((d) => (
        <li key={d.id} className="flex items-center gap-3 py-3 text-sm first:pt-0 last:pb-0">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
            {d.platform === 'desktop' ? <MonitorIcon /> : <PhoneIcon />}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{displayText(d.device_name)}</p>
            <p className="text-xs text-slate-500">
              {d.platform} · {d.total_checkins ?? 0} check-ins
            </p>
          </div>
          <span
            className={`badge ${
              d.is_trusted ? 'bg-green-50 text-green-700' : 'bg-slate-100 text-slate-600'
            }`}
          >
            {d.trust_score ?? (d.is_trusted ? 'trusted' : 'unverified')}
          </span>
        </li>
      ))}
    </ul>
  )
}

function ProfileContent() {
  const { user, updateUser } = useAuth()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  if (!user) return null

  async function updateConsent(field: 'camera_consent' | 'geolocation_consent', value: boolean) {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      // Use the server's copy: withdrawing camera consent also deletes the
      // face enrollment there, which a single-field local patch would miss.
      const res = await api.put<User>('/users/me', { [field]: value })
      updateUser(res.data)
      setSaved(true)
    } catch (err) {
      setError(extractErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="card flex items-center gap-4 p-5">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-blue-700 text-lg font-semibold text-white">
          {initials(displayText(user.full_name))}
        </div>
        <div className="min-w-0">
          <p className="truncate font-semibold">{displayText(user.full_name)}</p>
          <p className="truncate text-sm text-slate-500">{user.email}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <span className="badge bg-slate-100 text-slate-700">{user.role}</span>
            <span
              className={`badge normal-case ${
                user.face_enrolled ? 'bg-green-50 text-green-700' : 'bg-slate-100 text-slate-600'
              }`}
            >
              Face enrolled: {user.face_enrolled ? 'Yes' : 'No'}
            </span>
          </div>
        </div>
      </div>

      <div className="card p-5">
        <h2 className={sectionHeading}>Privacy & Consent</h2>
        <div className="mt-3 space-y-2">
          {error && <Alert type="error">{error}</Alert>}
          {saved && !error && <Alert type="success">Saved.</Alert>}
        </div>
        <div className="divide-y divide-slate-100">
          <ConsentToggle
            label="Allow camera access for check-in verification"
            checked={!!user.camera_consent}
            onChange={(v) => updateConsent('camera_consent', v)}
          />
          <ConsentToggle
            label="Allow location access for geofence verification"
            checked={!!user.geolocation_consent}
            onChange={(v) => updateConsent('geolocation_consent', v)}
          />
        </div>
        {saving && (
          <p className="mt-1 flex items-center gap-2 text-xs text-slate-500">
            <Spinner className="h-3 w-3" />
            Saving...
          </p>
        )}
        <p className="mt-3 text-xs leading-relaxed text-slate-500">
          Both must be enabled to check in. You can withdraw consent at any time, but you
          won&apos;t be able to check in until it&apos;s re-enabled. Turning off camera access
          also deletes your face enrollment.
        </p>
      </div>

      <div className="card p-5">
        <h2 className={`${sectionHeading} mb-4`}>Registered Devices</h2>
        <DeviceList />
      </div>
    </div>
  )
}

export default function ProfilePage() {
  return (
    <ProtectedRoute>
      <main className="page">
        <div>
          <h1 className="page-title">Profile</h1>
          <p className="page-subtitle">Your account, privacy settings and devices.</p>
        </div>
        <ProfileContent />
      </main>
    </ProtectedRoute>
  )
}
