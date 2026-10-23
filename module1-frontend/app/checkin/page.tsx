'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import ProtectedRoute from '@/components/ProtectedRoute'
import ConsentPrompt from '@/components/ConsentPrompt'
import CameraCapture from '@/components/CameraCapture'
import Alert from '@/components/Alert'
import { LoadingState } from '@/components/Spinner'
import {
  CalendarIcon,
  CameraIcon,
  CheckCircleIcon,
  ChevronRightIcon,
  ClockIcon,
  InfoIcon,
  MapPinIcon,
  RefreshIcon,
  WarningIcon,
  XCircleIcon,
} from '@/components/icons'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'
import { getCurrentPosition, GeolocationError, type GeoResult } from '@/lib/geolocation'
import { createDeviceProof, getDeviceIdentity } from '@/lib/device'
import { displayText } from '@/lib/text'
import type { CheckinHistoryItem, CheckinResult, Course, Enrollment, Session } from '@/lib/types'

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

const flowSteps = ['Photo', 'Location', 'Done']

/** Progress bar for the check-in flow. `current` is the index of the step in progress. */
function StepIndicator({ current }: { current: number }) {
  return (
    <ol className="flex items-center gap-2 text-xs font-medium">
      {flowSteps.map((label, i) => {
        const done = i < current
        const reached = i <= current
        return (
          <li key={label} className="flex flex-1 items-center gap-2 last:flex-none">
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                reached ? 'bg-blue-700 text-white' : 'bg-slate-200 text-slate-500'
              }`}
            >
              {done ? '✓' : i + 1}
            </span>
            <span className={reached ? 'text-slate-900' : 'text-slate-400'}>{label}</span>
            {i < flowSteps.length - 1 && (
              <span className={`h-0.5 flex-1 rounded ${done ? 'bg-blue-700' : 'bg-slate-200'}`} />
            )}
          </li>
        )
      })}
    </ol>
  )
}

function ResultCard({ result, onDone }: { result: CheckinResult; onDone: () => void }) {
  const statusLook: Record<string, { icon: typeof InfoIcon; tone: string }> = {
    approved: { icon: CheckCircleIcon, tone: 'bg-green-100 text-green-700' },
    pending: { icon: ClockIcon, tone: 'bg-amber-100 text-amber-700' },
    flagged: { icon: WarningIcon, tone: 'bg-amber-100 text-amber-700' },
    rejected: { icon: XCircleIcon, tone: 'bg-red-100 text-red-700' },
  }
  const statusMessage: Record<string, string> = {
    approved: 'You are checked in.',
    pending: 'Your check-in is being processed.',
    flagged: 'Your check-in was flagged for instructor review.',
    rejected: 'Your check-in was not accepted.',
  }
  const look = statusLook[result.status] ?? { icon: InfoIcon, tone: 'bg-blue-100 text-blue-700' }

  return (
    <div className="space-y-4">
      <div className="card overflow-hidden">
        <div className="space-y-3 px-6 py-7 text-center" role="alert">
          <div
            className={`mx-auto flex h-16 w-16 items-center justify-center rounded-full ${look.tone}`}
          >
            <look.icon className="h-9 w-9" />
          </div>
          <div>
            <p className="text-xl font-bold capitalize">{result.status}</p>
            <p className="mt-1 text-sm text-slate-600">{statusMessage[result.status] ?? ''}</p>
          </div>
        </div>
        <dl className="divide-y divide-slate-100 border-t border-slate-100 text-sm">
          {typeof result.risk_score === 'number' && (
            <div className="flex justify-between px-5 py-3">
              <dt className="text-slate-500">Risk score</dt>
              <dd className="font-mono font-medium">{result.risk_score.toFixed(2)}</dd>
            </div>
          )}
          {typeof result.distance_from_venue_meters === 'number' && (
            <div className="flex justify-between px-5 py-3">
              <dt className="text-slate-500">Distance from venue</dt>
              <dd className="font-medium">{result.distance_from_venue_meters.toFixed(0)}m</dd>
            </div>
          )}
          {typeof result.liveness_passed === 'boolean' && (
            <div className="flex justify-between px-5 py-3">
              <dt className="text-slate-500">Liveness check</dt>
              <dd
                className={`font-medium capitalize ${
                  result.liveness_passed ? 'text-green-700' : 'text-red-700'
                }`}
              >
                {result.liveness_passed ? 'passed' : 'failed'}
              </dd>
            </div>
          )}
          <div className="flex justify-between px-5 py-3">
            <dt className="text-slate-500">Checked in at</dt>
            <dd className="font-medium">{new Date(result.checked_in_at).toLocaleTimeString()}</dd>
          </div>
        </dl>
      </div>
      {(result.status === 'flagged' || result.status === 'rejected') && (
        <p className="text-center text-xs text-slate-500">
          If you believe this is a mistake, you can appeal from your check-in history.
        </p>
      )}
      <button onClick={onDone} className="btn btn-primary w-full">
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
  const [faceRequiredCourseIds, setFaceRequiredCourseIds] = useState<Set<string>>(new Set())
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
      const visible = courseIds ? active.data.filter((s) => courseIds.has(s.course_id)) : active.data
      // A course can require face recognition for all of its sessions, which
      // the session payload does not carry. A failed lookup falls back to the
      // session's own flag; the backend still rejects unenrolled faces.
      const courses = await Promise.all(
        Array.from(new Set(visible.map((s) => s.course_id))).map((id) =>
          api.get<Course>(`/courses/${id}`).then((r) => r.data).catch(() => null),
        ),
      )
      setFaceRequiredCourseIds(
        new Set(courses.filter((c): c is Course => !!c?.require_face_recognition).map((c) => c.id)),
      )
      setSessions(visible)
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
    const faceRequired = session.require_face_match || faceRequiredCourseIds.has(session.course_id)
    if (faceRequired && !user?.face_enrolled) {
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
    <button onClick={reset} className="btn btn-ghost mx-auto flex w-fit">
      Back to sessions
    </button>
  )

  // Shown above every step once a session has been picked.
  const progress = (
    <StepIndicator
      current={
        step === 'result' ? flowSteps.length : step === 'camera-consent' || step === 'camera' ? 0 : 1
      }
    />
  )

  if (step === 'loading') {
    return <LoadingState>Loading sessions...</LoadingState>
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
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Open Check-ins
          </h2>
          <button onClick={loadSessions} className="btn btn-ghost -mr-2 px-2.5 py-1.5">
            <RefreshIcon className="h-4 w-4" />
            Refresh
          </button>
        </div>
        {sessions.length === 0 && !error && (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-10 text-center">
            <CalendarIcon className="mx-auto h-10 w-10 text-slate-300" />
            <p className="mt-3 font-semibold">No open sessions</p>
            <p className="mt-1 text-sm text-slate-500">
              No sessions in your courses are open for check-in right now. Ask your instructor to
              activate the session, then refresh.
            </p>
          </div>
        )}
        <ul className="space-y-3">
          {sessions.map((session) => {
            const checkedIn = checkedInSessionIds.has(session.id)
            const remaining = timeRemaining(session.checkin_closes_at)
            return (
              <li key={session.id}>
                <button
                  onClick={() => selectSession(session)}
                  disabled={checkedIn}
                  className="card group flex w-full items-center gap-3 p-4 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 enabled:hover:border-blue-400 enabled:hover:shadow-md disabled:opacity-70"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-semibold">{displayText(session.name)}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-500">
                      {session.course_code && (
                        <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-xs font-semibold text-slate-700">
                          {session.course_code}
                        </span>
                      )}
                      {session.venue_name && (
                        <span className="flex items-center gap-1">
                          <MapPinIcon className="h-4 w-4" />
                          {session.venue_name}
                        </span>
                      )}
                    </p>
                    {checkedIn ? (
                      <span className="badge mt-2.5 bg-green-50 normal-case text-green-700">
                        <CheckCircleIcon className="h-3.5 w-3.5" />
                        Already checked in
                      </span>
                    ) : (
                      remaining && (
                        <span className="badge mt-2.5 bg-blue-50 normal-case text-blue-700">
                          <ClockIcon className="h-3.5 w-3.5" />
                          {remaining}
                        </span>
                      )
                    )}
                  </div>
                  {!checkedIn && (
                    <ChevronRightIcon className="h-5 w-5 text-slate-400 transition-colors group-hover:text-blue-700" />
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      </div>
    )
  }

  if (step === 'camera-consent') {
    return (
      <div className="space-y-4">
        {progress}
        {error && <Alert type="error">{error}</Alert>}
        <ConsentPrompt
          icon={<CameraIcon className="h-7 w-7" />}
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
      <div className="space-y-4">
        {progress}
        <div className="card space-y-4 p-5">
          <p className="text-center text-sm text-slate-500">
            Checking in to{' '}
            <span className="font-semibold text-slate-900">
              {displayText(selectedSession?.name)}
            </span>
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
        {backToSessions}
      </div>
    )
  }

  if (step === 'location-consent') {
    return (
      <div className="space-y-4">
        {progress}
        {error && <Alert type="error">{error}</Alert>}
        <ConsentPrompt
          icon={<MapPinIcon className="h-7 w-7" />}
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
      <div className="space-y-4">
        {progress}
        {error ? (
          <>
            <Alert type="error">{error}</Alert>
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => requestLocationAndSubmit(capturedImage)}
                className="btn btn-primary"
              >
                Try again
              </button>
              <button
                onClick={() => {
                  setError(null)
                  setStep('camera')
                }}
                className="btn btn-secondary"
              >
                Retake photo
              </button>
            </div>
            {backToSessions}
          </>
        ) : (
          <div className="card">
            <LoadingState>
              {step === 'submitting' ? 'Submitting check-in...' : 'Getting your location...'}
            </LoadingState>
          </div>
        )}
      </div>
    )
  }

  if (step === 'result' && result) {
    return (
      <div className="space-y-4">
        {progress}
        <ResultCard result={result} onDone={reset} />
      </div>
    )
  }

  return null
}

export default function CheckinPage() {
  return (
    <ProtectedRoute>
      <main className="page">
        <div>
          <h1 className="page-title">Check In</h1>
          <p className="page-subtitle">Pick an open session to mark your attendance.</p>
        </div>
        <CheckinFlow />
      </main>
    </ProtectedRoute>
  )
}
