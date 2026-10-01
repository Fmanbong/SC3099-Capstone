'use client'

import { useEffect, useState } from 'react'
import ProtectedRoute from '@/components/ProtectedRoute'
import Alert from '@/components/Alert'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'
import { displayText } from '@/lib/text'
import type { Device, User } from '@/lib/types'

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
    <label className="flex items-center justify-between py-2">
      <span className="text-sm">{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-5 w-5"
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
    return <p className="text-sm text-gray-500">No devices registered yet.</p>
  }

  return (
    <ul className="space-y-2">
      {devices.map((d) => (
        <li key={d.id} className="border rounded p-3 text-sm flex justify-between items-center">
          <div>
            <p className="font-medium">{displayText(d.device_name)}</p>
            <p className="text-xs text-gray-500">
              {d.platform} · {d.total_checkins ?? 0} check-ins
            </p>
          </div>
          <span
            className={`text-xs px-2 py-1 rounded-full ${
              d.is_trusted ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'
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
      <div className="bg-white border rounded-lg p-4">
        <h2 className="font-semibold mb-2">Account</h2>
        <p className="text-sm">{displayText(user.full_name)}</p>
        <p className="text-sm text-gray-500">{user.email}</p>
        <p className="text-xs text-gray-400 capitalize mt-1">{user.role}</p>
        <p className="text-xs mt-2">
          Face enrolled:{' '}
          <span className={user.face_enrolled ? 'text-green-700' : 'text-gray-500'}>
            {user.face_enrolled ? 'Yes' : 'No'}
          </span>
        </p>
      </div>

      <div className="bg-white border rounded-lg p-4">
        <h2 className="font-semibold mb-2">Privacy & Consent</h2>
        {error && <Alert type="error">{error}</Alert>}
        {saved && !error && <Alert type="success">Saved.</Alert>}
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
        {saving && <p className="text-xs text-gray-400 mt-1">Saving...</p>}
        <p className="text-xs text-gray-400 mt-2">
          Both must be enabled to check in. You can withdraw consent at any time, but you
          won&apos;t be able to check in until it&apos;s re-enabled. Turning off camera access
          also deletes your face enrollment.
        </p>
      </div>

      <div className="bg-white border rounded-lg p-4">
        <h2 className="font-semibold mb-2">Registered Devices</h2>
        <DeviceList />
      </div>
    </div>
  )
}

export default function ProfilePage() {
  return (
    <ProtectedRoute>
      <main className="max-w-md mx-auto p-4 space-y-4">
        <h1 className="text-xl font-bold">Profile</h1>
        <ProfileContent />
      </main>
    </ProtectedRoute>
  )
}
