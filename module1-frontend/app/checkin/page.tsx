'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import ProtectedRoute from '@/components/ProtectedRoute'
import ConsentPrompt from '@/components/ConsentPrompt'
import CameraCapture from '@/components/CameraCapture'
import Alert from '@/components/Alert'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'
import { getCurrentPosition, GeolocationError, type GeoResult } from '@/lib/geolocation'
import { createDeviceProof, getDeviceIdentity } from '@/lib/device'
import { displayText } from '@/lib/text'
import type { CheckinHistoryItem, CheckinResult, Enrollment, Session } from '@/lib/types'

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
  const { user, updateUser, refreshUser } = useAuth()
  const [step, setStep] = useState<Step>('loading')
  const [sessions, setSessions] = useState<Session[]>([])
  const [checkedInSessionIds, setCheckedInSessionIds] = useState<Set<string>>(new Set())
  const [selectedSession, setSelectedSession] = useState<Session | null>(null)
  const [capturedImage, setCapturedImage] = useState<string | null>(null)
  const [location, setLocation] = useState<GeoResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<CheckinResult | null>(null)
  const [grantingConsent, setGrantingConsent] = useState(false)
  const [faceEnrollmentNeeded, setFaceEnrollmentNeeded] = useState(false)

  const loadSessions = useCallback(async () => {
    setStep('loading')
    setError(null)
    setFaceEnrollmentNeeded(false)
    try {
      // /sessions/active lists every open session in the system, so it is
      // narrowed to the student's own courses. The two extra lookups are
      // optional: if either fails, the list is shown unfiltered and the
      // backend still enforces enrollment and duplicates on submit.
      const [active, enrollments, history] = await Promise.all([
        api.get<Session[]>('/sessions/active'),
        api.get<Enrollment[]>('/enrollments/my-enrollments').catch(() => null),
        api.get<CheckinHistoryItem[]>('/checkins/my-checkins').catch(() => null),
      ])
      const courseIds = enrollments ? new Set(enrollments.data.map((e) => e.course_id)) : null
      setSessions(courseIds ? active.data.filter((s) => courseIds.has(s.course_id)) : active.data)
      setCheckedInSessionIds(new Set(history?.data.map((c) => c.session_id) ?? []))
      setStep('select')
    } catch (err) {
      setError(extractErrorMessage(err))
      setStep('select')
    }
  }, [])

  useEffect(() => {
    loadSessions()
    // Consent and face enrollment may have changed on another device.
    refreshUser()
  }, [loadSessions, refreshUser])

  function selectSession(session: Session) {
    setError(null)
    if (session.require_face_match && !user?.face_enrolled) {
      setFaceEnrollmentNeeded(true)
      return
    }
    setFaceEnrollmentNeeded(false)
    setSelectedSession(session)
    setStep(user?.camera_consent ? 'camera' : 'camera-consent')
  }

  async function submitCheckin(image: string | null, geo: GeoResult) {
    if (!selectedSession) return
    setStep('submitting')
    setError(null)

    // Check-ins are only ever submitted live: the photo and location must be
    // current, and the device proof below expires within minutes.
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      setError("You're offline. Reconnect and try again - check-ins can't be saved for later.")
      setStep('location')
      return
    }

    try {
      const [identity, proof] = await Promise.all([
        getDeviceIdentity(),
        createDeviceProof(selectedSession.id),
      ])
      const res = await api.post<CheckinResult>('/checkins/', {
        session_id: selectedSession.id,
        latitude: geo.latitude,
        longitude: geo.longitude,
        location_accuracy_meters: geo.accuracy,
        device_fingerprint: identity.fingerprint,
        liveness_challenge_response: image,
        ...(proof ?? {}),
      })
      setResult(res.data)
      setStep('result')
    } catch (err) {
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
    loadSessions()
  }

  const backToSessions = (
    <button onClick={reset} className="text-sm text-blue-700 underline block mx-auto">
      Back to sessions
    </button>
  )

  if (step === 'loading') {
    return <p className="text-center text-gray-500">Loading sessions...</p>
  }

  if (step === 'select') {
    return (
      <div className="space-y-3">
        {error && <Alert type="error">{error}</Alert>}
        {faceEnrollmentNeeded && (
          <Alert type="warning">
            This session requires face verification.{' '}
            <Link href="/enroll" className="underline font-medium">
              Enroll your face
            </Link>{' '}
            first, then come back to check in.
          </Alert>
        )}
        <h2 className="font-semibold">Open Check-ins</h2>
        {sessions.length === 0 && (
          <p className="text-sm text-gray-500">
            No sessions in your courses are open for check-in right now. Ask your instructor to
            activate the session, then refresh.
          </p>
        )}
        <ul className="space-y-2">
          {sessions.map((session) => {
            const checkedIn = checkedInSessionIds.has(session.id)
            return (
              <li key={session.id}>
                <button
                  onClick={() => selectSession(session)}
                  disabled={checkedIn}
                  className="w-full text-left bg-white border rounded-lg p-4 hover:border-blue-500 disabled:opacity-60 disabled:hover:border-gray-200"
                >
                  <p className="font-medium">{displayText(session.name)}</p>
                  <p className="text-sm text-gray-500">
                    {session.course_code} {session.venue_name ? `· ${session.venue_name}` : ''}
                  </p>
                  <p className="text-xs text-blue-700 mt-1">
                    {checkedIn ? 'Already checked in' : timeRemaining(session.checkin_closes_at)}
                  </p>
                </button>
              </li>
            )
          })}
        </ul>
        <button onClick={loadSessions} className="text-sm text-blue-700 underline">
          Refresh
        </button>
      </div>
    )
  }

  if (step === 'camera-consent') {
    return (
      <div className="space-y-3">
        {error && <Alert type="error">{error}</Alert>}
        <ConsentPrompt
          title="Camera access needed"
          description="We use your camera to confirm it's really you checking in. Your photo is analyzed and never stored."
          granting={grantingConsent}
          onGrant={async () => {
            setGrantingConsent(true)
            setError(null)
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
        {backToSessions}
      </div>
    )
  }

  if (step === 'camera') {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-500">
          Checking in to{' '}
          <span className="font-medium">{displayText(selectedSession?.name)}</span>
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
        {backToSessions}
      </div>
    )
  }

  if (step === 'location-consent') {
    return (
      <div className="space-y-3">
        {error && <Alert type="error">{error}</Alert>}
        <ConsentPrompt
          title="Location access needed"
          description="We check that you're within range of the venue. Your location is only used for this check-in."
          granting={grantingConsent}
          onGrant={async () => {
            setGrantingConsent(true)
            setError(null)
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
        {backToSessions}
      </div>
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
          <>
            <div className="flex justify-center gap-2 flex-wrap">
              <button
                onClick={() => requestLocationAndSubmit(capturedImage)}
                className="bg-blue-700 text-white px-4 py-2 rounded"
              >
                Try again
              </button>
              <button
                onClick={() => {
                  setError(null)
                  setStep('camera')
                }}
                className="border border-blue-700 text-blue-700 px-4 py-2 rounded"
              >
                Retake photo
              </button>
            </div>
            {backToSessions}
          </>
        )}
      </div>
    )
  }

  if (step === 'result' && result) {
    return <ResultCard result={result} onDone={reset} />
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
