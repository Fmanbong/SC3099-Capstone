'use client'

import { useCallback, useEffect, useState } from 'react'
import ProtectedRoute from '@/components/ProtectedRoute'
import ConsentPrompt from '@/components/ConsentPrompt'
import CameraCapture from '@/components/CameraCapture'
import Alert from '@/components/Alert'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'
import { getCurrentPosition, GeolocationError, type GeoResult } from '@/lib/geolocation'
import { getDeviceFingerprint, getOrCreateDeviceKeyPair, detectPlatform } from '@/lib/device'
import { queueCheckin } from '@/lib/offline-queue'
import type { CheckinResult, Session } from '@/lib/types'

type Step =
  | 'loading'
  | 'select'
  | 'camera-consent'
  | 'camera'
  | 'location-consent'
  | 'location'
  | 'submitting'
  | 'result'

function timeRemaining(closesAt?: string): string {
  if (!closesAt) return ''
  const diffMs = new Date(closesAt).getTime() - Date.now()
  if (diffMs <= 0) return 'closing soon'
  const mins = Math.round(diffMs / 60000)
  return `${mins} min left`
}

function ResultCard({ result, onDone }: { result: CheckinResult; onDone: () => void }) {
  const statusStyle: Record<string, 'success' | 'warning' | 'error'> = {
    approved: 'success',
    pending: 'warning',
    flagged: 'warning',
    rejected: 'error',
  }
  const statusMessage: Record<string, string> = {
    approved: 'You are checked in.',
    pending: 'Your check-in is being processed.',
    flagged: 'Your check-in was flagged for instructor review.',
    rejected: 'Your check-in was not accepted.',
  }

  return (
    <div className="space-y-3">
      <Alert type={statusStyle[result.status] ?? 'info'}>
        <span className="font-semibold capitalize">{result.status}</span> —{' '}
        {statusMessage[result.status] ?? ''}
      </Alert>
      <div className="bg-white border rounded-lg p-4 text-sm space-y-1">
        {typeof result.risk_score === 'number' && (
          <p>
            Risk score: <span className="font-mono">{result.risk_score.toFixed(2)}</span>
          </p>
        )}
        {typeof result.distance_from_venue_meters === 'number' && (
          <p>Distance from venue: {result.distance_from_venue_meters.toFixed(0)}m</p>
        )}
        {typeof result.liveness_passed === 'boolean' && (
          <p>Liveness check: {result.liveness_passed ? 'passed' : 'failed'}</p>
        )}
        <p>Checked in at: {new Date(result.checked_in_at).toLocaleTimeString()}</p>
      </div>
      {(result.status === 'flagged' || result.status === 'rejected') && (
        <p className="text-xs text-gray-500">
          If you believe this is a mistake, you can appeal from your check-in history.
        </p>
      )}
      <button onClick={onDone} className="w-full bg-blue-700 text-white py-2 rounded font-medium">
        Done
      </button>
    </div>
  )
}

function CheckinFlow() {
  const { user, updateUser } = useAuth()
  const [step, setStep] = useState<Step>('loading')
  const [sessions, setSessions] = useState<Session[]>([])
  const [selectedSession, setSelectedSession] = useState<Session | null>(null)
  const [capturedImage, setCapturedImage] = useState<string | null>(null)
  const [location, setLocation] = useState<GeoResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<CheckinResult | null>(null)
  const [grantingConsent, setGrantingConsent] = useState(false)
  const [offlineNotice, setOfflineNotice] = useState(false)

  const loadSessions = useCallback(async () => {
    setStep('loading')
    setError(null)
    try {
      const res = await api.get<Session[]>('/sessions/active')
      setSessions(res.data)
      setStep('select')
    } catch (err) {
      setError(extractErrorMessage(err))
      setStep('select')
    }
  }, [])

  useEffect(() => {
    loadSessions()
    // Best-effort device registration; failures shouldn't block check-in.
    ;(async () => {
      try {
        const [fingerprint, keyPair] = await Promise.all([
          getDeviceFingerprint(),
          getOrCreateDeviceKeyPair(),
        ])
        await api.post('/devices/register', {
          device_fingerprint: fingerprint,
          device_name: `${detectPlatform()} browser`,
          platform: detectPlatform(),
          public_key: keyPair.publicKeyPem,
        })
      } catch {
        // Device may already be registered, or endpoint may reject duplicates - ignore.
      }
    })()
  }, [loadSessions])

  function selectSession(session: Session) {
    setSelectedSession(session)
    setError(null)
    setStep(user?.camera_consent ? 'camera' : 'camera-consent')
  }

  async function submitCheckin(image: string | null, geo: GeoResult) {
    if (!selectedSession) return
    setStep('submitting')
    setError(null)

    const fingerprint = await getDeviceFingerprint()
    const payload = {
      session_id: selectedSession.id,
      latitude: geo.latitude,
      longitude: geo.longitude,
      location_accuracy_meters: geo.accuracy,
      device_fingerprint: fingerprint,
      liveness_challenge_response: image,
    }

    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      await queueCheckin(payload)
      setOfflineNotice(true)
      setStep('result')
      setResult({
        id: 'queued',
        session_id: selectedSession.id,
        status: 'pending',
        checked_in_at: new Date().toISOString(),
      })
      return
    }

    try {
      const res = await api.post<CheckinResult>('/checkins/', payload)
      setResult(res.data)
      setStep('result')
    } catch (err) {
      if (err instanceof Error && err.message === 'Network Error') {
        await queueCheckin(payload)
        setOfflineNotice(true)
        setResult({
          id: 'queued',
          session_id: selectedSession.id,
          status: 'pending',
          checked_in_at: new Date().toISOString(),
        })
        setStep('result')
        return
      }
      setError(extractErrorMessage(err))
      setStep('location')
    }
  }

  async function requestLocationAndSubmit(image: string | null) {
    setStep('location')
    setError(null)
    try {
      const geo = await getCurrentPosition()
      setLocation(geo)
      await submitCheckin(image, geo)
    } catch (err) {
      if (err instanceof GeolocationError) {
        setError(err.message)
      } else {
        setError('Could not get your location.')
      }
      setStep('location')
    }
  }

  function reset() {
    setSelectedSession(null)
    setCapturedImage(null)
    setLocation(null)
    setResult(null)
    setOfflineNotice(false)
    loadSessions()
  }

  if (step === 'loading') {
    return <p className="text-center text-gray-500">Loading sessions...</p>
  }

  if (step === 'select') {
    return (
      <div className="space-y-3">
        {error && <Alert type="error">{error}</Alert>}
        <h2 className="font-semibold">Open Check-ins</h2>
        {sessions.length === 0 && (
          <p className="text-sm text-gray-500">
            No sessions are open for check-in right now. Ask your instructor to activate the
            session, then refresh.
          </p>
        )}
        <ul className="space-y-2">
          {sessions.map((session) => (
            <li key={session.id}>
              <button
                onClick={() => selectSession(session)}
                className="w-full text-left bg-white border rounded-lg p-4 hover:border-blue-500"
              >
                <p className="font-medium">{session.name}</p>
                <p className="text-sm text-gray-500">
                  {session.course_code} {session.venue_name ? `· ${session.venue_name}` : ''}
                </p>
                <p className="text-xs text-blue-700 mt-1">{timeRemaining(session.checkin_closes_at)}</p>
              </button>
            </li>
          ))}
        </ul>
        <button onClick={loadSessions} className="text-sm text-blue-700 underline">
          Refresh
        </button>
      </div>
    )
  }

  if (step === 'camera-consent') {
    return (
      <ConsentPrompt
        title="Camera access needed"
        description="We use your camera to confirm it's really you checking in. Your photo is analyzed and never stored."
        granting={grantingConsent}
        onGrant={async () => {
          setGrantingConsent(true)
          try {
            await api.put('/users/me', { camera_consent: true })
            updateUser({ camera_consent: true })
            setStep('camera')
          } catch (err) {
            setError(extractErrorMessage(err))
          } finally {
            setGrantingConsent(false)
          }
        }}
      />
    )
  }

  if (step === 'camera') {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-500">
          Checking in to <span className="font-medium">{selectedSession?.name}</span>
        </p>
        <CameraCapture
          onCapture={(image) => {
            setCapturedImage(image)
            if (!user?.geolocation_consent) {
              setStep('location-consent')
            } else {
              requestLocationAndSubmit(image)
            }
          }}
        />
      </div>
    )
  }

  if (step === 'location-consent') {
    return (
      <ConsentPrompt
        title="Location access needed"
        description="We check that you're within range of the venue. Your location is only used for this check-in."
        granting={grantingConsent}
        onGrant={async () => {
          setGrantingConsent(true)
          try {
            await api.put('/users/me', { geolocation_consent: true })
            updateUser({ geolocation_consent: true })
            await requestLocationAndSubmit(capturedImage)
          } catch (err) {
            setError(extractErrorMessage(err))
          } finally {
            setGrantingConsent(false)
          }
        }}
      />
    )
  }

  if (step === 'location' || step === 'submitting') {
    return (
      <div className="space-y-3 text-center">
        {error && <Alert type="error">{error}</Alert>}
        <p className="text-gray-500">
          {step === 'location' && !error ? 'Getting your location...' : ''}
          {step === 'submitting' ? 'Submitting check-in...' : ''}
        </p>
        {error && (
          <button
            onClick={() => requestLocationAndSubmit(capturedImage)}
            className="bg-blue-700 text-white px-4 py-2 rounded"
          >
            Try again
          </button>
        )}
      </div>
    )
  }

  if (step === 'result' && result) {
    return (
      <div className="space-y-2">
        {offlineNotice && (
          <Alert type="warning">
            You&apos;re offline. This check-in was saved and will be submitted automatically once
            you&apos;re back online.
          </Alert>
        )}
        <ResultCard result={result} onDone={reset} />
      </div>
    )
  }

  return null
}

export default function CheckinPage() {
  return (
    <ProtectedRoute>
      <main className="max-w-md mx-auto p-4 space-y-4">
        <h1 className="text-xl font-bold">Check In</h1>
        <CheckinFlow />
      </main>
    </ProtectedRoute>
  )
}
