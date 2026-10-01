'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import ProtectedRoute from '@/components/ProtectedRoute'
import ConsentPrompt from '@/components/ConsentPrompt'
import CameraCapture from '@/components/CameraCapture'
import Alert from '@/components/Alert'
import { useAuth } from '@/lib/auth-context'
import { api, extractErrorMessage } from '@/lib/api'

function EnrollFlow() {
  const { user, updateUser } = useAuth()
  const router = useRouter()
  const [grantingConsent, setGrantingConsent] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  if (user?.face_enrolled) {
    return (
      <Alert type="success">
        Your face is already enrolled. You&apos;re all set for check-ins that require face
        verification.
      </Alert>
    )
  }

  if (!user?.camera_consent) {
    return (
      <div className="space-y-3">
        {error && <Alert type="error">{error}</Alert>}
        <ConsentPrompt
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

  if (success) {
    return (
      <div className="space-y-3">
        <Alert type="success">Face enrolled successfully!</Alert>
        <button
          onClick={() => router.push('/checkin')}
          className="bg-blue-700 text-white px-4 py-2 rounded"
        >
          Go to Check-in
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {error && <Alert type="error">{error}</Alert>}
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
      {submitting && <p className="text-center text-sm text-gray-500">Enrolling face...</p>}
    </div>
  )
}

export default function EnrollPage() {
  return (
    <ProtectedRoute>
      <main className="max-w-md mx-auto p-4 space-y-4">
        <h1 className="text-xl font-bold">Face Enrollment</h1>
        <EnrollFlow />
      </main>
    </ProtectedRoute>
  )
}
