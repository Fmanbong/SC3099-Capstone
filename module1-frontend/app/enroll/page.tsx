'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import ProtectedRoute from '@/components/ProtectedRoute'
import ConsentPrompt from '@/components/ConsentPrompt'
import CameraCapture from '@/components/CameraCapture'
import Alert from '@/components/Alert'
import Spinner from '@/components/Spinner'
import { CameraIcon, CheckCircleIcon, ClockIcon } from '@/components/icons'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'
import { displayDate, displayText } from '@/lib/text'

/** Green tick card used for both "already enrolled" and "just enrolled". */
function EnrolledCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card space-y-4 px-6 py-8 text-center" role="alert">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-green-100 text-green-700">
        <CheckCircleIcon className="h-9 w-9" />
      </div>
      <p className="text-lg font-semibold">{title}</p>
      {children}
    </div>
  )
}

function EnrollFlow() {
  const { user, updateUser, refreshUser } = useAuth()
  const router = useRouter()
  const [grantingConsent, setGrantingConsent] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [reenrolling, setReenrolling] = useState(false)
  const reviewNote = displayText(user?.face_enrollment_review_notes)

  // The instructor decides elsewhere, so reload the status whenever this page opens.
  useEffect(() => {
    refreshUser()
  }, [refreshUser])

  // Shown while an instructor still has to approve the enrollment in person.
  if (user?.face_enrollment_status === 'pending' && !reenrolling) {
    return (
      <div className="card space-y-4 px-6 py-8 text-center" role="status">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-amber-100 text-amber-700">
          <ClockIcon className="h-9 w-9" />
        </div>
        <p className="text-lg font-semibold">Waiting for instructor approval</p>
        <p className="text-sm text-slate-600">
          Show this screen and your student card to your instructor so they can approve your face
          enrollment.
        </p>
        <p className="text-sm text-slate-500">
          {user.face_enrolled
            ? 'Your previously approved face keeps working for check-in until then.'
            : 'Check-ins that require face verification will be available once it is approved.'}
        </p>
        <button onClick={() => setReenrolling(true)} className="btn btn-secondary w-full">
          Retake photo
        </button>
      </div>
    )
  }

  // Checked before `face_enrolled` below: a successful enrollment sets both,
  // and the user should see the confirmation with the button to check in.
  if (success) {
    return (
      <EnrolledCard title="Face enrolled successfully!">
        <button onClick={() => router.push('/checkin')} className="btn btn-primary w-full">
          Go to Check-in
        </button>
      </EnrolledCard>
    )
  }

  if (user?.face_enrolled && !reenrolling) {
    return (
      <EnrolledCard title="Your face is already enrolled">
        <p className="text-sm text-slate-600">
          You&apos;re all set for check-ins that require face verification.
        </p>
        {user.face_enrollment_status === 'approved' && user.face_enrollment_reviewed_at && (
          <Alert type="success">
            Approved by your instructor on {displayDate(user.face_enrollment_reviewed_at)}
            {reviewNote ? `: ${reviewNote}` : '.'}
          </Alert>
        )}
        {user.face_enrollment_status === 'rejected' && (
          <Alert type="warning">
            Your latest re-enrollment was not approved{reviewNote ? `: ${reviewNote}` : '.'} Your
            previously approved face is still in use.
          </Alert>
        )}
        {/* Matching works best when enrollment looks like a check-in, e.g. glasses on or off. */}
        <p className="text-sm text-slate-500">
          Changed your look (glasses, hairstyle) or keep getting flagged? Re-enroll to replace your
          saved face.
        </p>
        <button onClick={() => setReenrolling(true)} className="btn btn-secondary w-full">
          Re-enroll face
        </button>
      </EnrolledCard>
    )
  }

  if (!user?.camera_consent) {
    return (
      <div className="space-y-3">
        {error && <Alert type="error">{error}</Alert>}
        <ConsentPrompt
          icon={<CameraIcon className="h-7 w-7" />}
          title="Camera access needed"
          description="We use your camera to enroll your face so future check-ins can be verified as you. Your photo is processed to generate a one-way hash and is never stored."
          granting={grantingConsent}
          onGrant={async () => {
            setGrantingConsent(true)
            setError(null)
            try {
              await api.put('/users/me', { camera_consent: true })
              updateUser({ camera_consent: true })
            } catch (err) {
              setError(extractErrorMessage(err))
            } finally {
              setGrantingConsent(false)
            }
          }}
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {error && <Alert type="error">{error}</Alert>}
      {user?.face_enrollment_status === 'rejected' && !user.face_enrolled && (
        <Alert type="warning">
          Your instructor did not approve your last enrollment{reviewNote ? `: ${reviewNote}` : '.'}{' '}
          Please enroll again, then show your instructor.
        </Alert>
      )}
      <div className="card space-y-4 p-5">
        <CameraCapture
          instructions={
            reenrolling
              ? 'Look the way you usually do when checking in (glasses on or off), center your face with good lighting, then capture.'
              : 'Center your face in frame with good lighting, then capture.'
          }
          onCapture={async (image) => {
            setSubmitting(true)
            setError(null)
            try {
              const res = await api.post('/users/me/face/enroll', { image })
              if (res.data?.approval_pending) {
                // Held for in-person approval; the pending card replaces the camera.
                updateUser({ face_enrollment_status: 'pending', face_enrollment_review_notes: null })
                setReenrolling(false)
              } else if (res.data?.face_enrolled) {
                updateUser({ face_enrolled: true })
                setSuccess(true)
              } else {
                setError(res.data?.message || 'Enrollment did not succeed. Please retake the photo.')
              }
            } catch (err) {
              setError(extractErrorMessage(err))
            } finally {
              setSubmitting(false)
            }
          }}
        />
        {submitting && (
          <p className="flex items-center justify-center gap-2 text-sm text-slate-500">
            <Spinner />
            Enrolling face...
          </p>
        )}
      </div>
    </div>
  )
}

export default function EnrollPage() {
  return (
    <ProtectedRoute>
      <main className="page">
        <div>
          <h1 className="page-title">Face Enrollment</h1>
          <p className="page-subtitle">
            Enroll once so sessions with face verification can confirm it&apos;s you.
          </p>
        </div>
        <EnrollFlow />
      </main>
    </ProtectedRoute>
  )
}
