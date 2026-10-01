'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { requestCameraStream, stopStream, captureFrame, CameraError } from '@/lib/camera'
import Alert from './Alert'

interface CameraCaptureProps {
  onCapture: (base64Image: string) => void
  instructions?: string
  /** Seconds to count down before auto-capturing, giving the liveness cue (e.g. "blink now") time to happen. */
  countdownSeconds?: number
}

export default function CameraCapture({
  onCapture,
  instructions = 'Look directly at the camera and blink naturally.',
  countdownSeconds = 3,
}: CameraCaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [countdown, setCountdown] = useState<number | null>(null)
  const [capturedPreview, setCapturedPreview] = useState<string | null>(null)

  // The camera runs only while the live view is on screen: it is released as
  // soon as a frame is captured (or the component unmounts) and re-acquired
  // on Retake, when a fresh <video> element is mounted.
  useEffect(() => {
    if (capturedPreview) return

    let active = true
    let stream: MediaStream | null = null
    requestCameraStream()
      .then((s) => {
        if (!active) {
          stopStream(s)
          return
        }
        stream = s
        if (videoRef.current) videoRef.current.srcObject = s
      })
      .catch((err) =>
        setError(err instanceof CameraError ? err.message : 'Could not start the camera.')
      )

    return () => {
      active = false
      stopStream(stream)
      setReady(false)
    }
  }, [capturedPreview])

  const doCapture = useCallback(() => {
    if (!videoRef.current) return
    try {
      const base64 = captureFrame(videoRef.current)
      setCapturedPreview(`data:image/jpeg;base64,${base64}`)
      onCapture(base64)
    } catch {
      setError('Could not capture image. Please try again.')
    }
  }, [onCapture])

  const startCountdown = useCallback(() => {
    setCountdown(countdownSeconds)
  }, [countdownSeconds])

  useEffect(() => {
    if (countdown === null) return
    if (countdown === 0) {
      doCapture()
      setCountdown(null)
      return
    }
    const timer = setTimeout(() => setCountdown((c) => (c ?? 1) - 1), 1000)
    return () => clearTimeout(timer)
  }, [countdown, doCapture])

  if (error) {
    return (
      <Alert type="error">
        {error} Please allow camera access in your browser settings and reload the page.
      </Alert>
    )
  }

  if (capturedPreview) {
    return (
      <div className="space-y-2">
        <img src={capturedPreview} alt="Captured frame" className="rounded w-full max-w-xs mx-auto" />
        <button
          type="button"
          onClick={() => setCapturedPreview(null)}
          className="text-sm text-blue-700 underline block mx-auto"
        >
          Retake
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-3 text-center">
      <p className="text-sm text-gray-600">{instructions}</p>
      <div className="relative inline-block">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          onLoadedMetadata={() => setReady(true)}
          className="rounded w-full max-w-xs mx-auto -scale-x-100"
        />
        {countdown !== null && countdown > 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/30 rounded">
            <span className="text-white text-4xl font-bold">{countdown}</span>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={startCountdown}
        disabled={!ready || countdown !== null}
        className="bg-blue-700 text-white px-4 py-2 rounded disabled:opacity-50"
      >
        {countdown !== null ? 'Capturing...' : 'Capture Photo'}
      </button>
    </div>
  )
}
