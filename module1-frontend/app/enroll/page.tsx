'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import ProtectedRoute from '@/components/ProtectedRoute'
import ConsentPrompt from '@/components/ConsentPrompt'
import CameraCapture from '@/components/CameraCapture'
import Alert from '@/components/Alert'
import Spinner from '@/components/Spinner'
import { CameraIcon, CheckCircleIcon } from '@/components/icons'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'

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
  const { user, updateUser } = useAuth()
  const router = useRouter()
  const [grantingConsent, setGrantingConsent] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

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

  if (user?.face_enrolled) {
    return (
      <EnrolledCard title="Your face is already enrolled">
        <p className="text-sm text-slate-600">
          You&apos;re all set for check-ins that require face verification.
        </p>
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
      <div className="card space-y-4 p-5">
        <CameraCapture
          instructions="Center your face in frame with good lighting, then capture."
          onCapture={async (image) => {
            setSubmitting(true)
            setError(null)
            try {
              const res = await api.post('/users/me/face/enroll', { image })
              if (res.data?.face_enrolled) {
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
