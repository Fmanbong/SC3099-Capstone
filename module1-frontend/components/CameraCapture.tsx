'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { requestCameraStream, stopStream, captureFrame, CameraError } from '@/lib/camera'
import Alert from './Alert'
import Spinner from './Spinner'
import { CameraIcon, RefreshIcon } from './icons'

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

  // Same rounded frame for the live view and the captured photo.
  const frame = 'relative mx-auto w-full max-w-xs overflow-hidden rounded-2xl bg-slate-900 shadow-sm'

  if (capturedPreview) {
    return (
      <div className="space-y-3 text-center">
        <div className={frame}>
          <img src={capturedPreview} alt="Captured frame" className="block w-full" />
        </div>
        <button
          type="button"
          onClick={() => setCapturedPreview(null)}
          className="btn btn-secondary"
        >
          <RefreshIcon className="h-4 w-4" />
          Retake
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4 text-center">
      <p className="text-sm text-slate-600">{instructions}</p>
      <div className={frame}>
        {/* Until the stream reports its size the video has no height, so hold a 4:3 box open. */}
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          onLoadedMetadata={() => setReady(true)}
          className={`block w-full -scale-x-100 ${ready ? '' : 'aspect-[4/3]'}`}
        />
        {!ready && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm text-slate-300">
            <Spinner className="h-6 w-6" />
            Starting camera...
          </div>
        )}
        {ready && (
          // Oval guide showing where to position the face.
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 m-auto h-[78%] w-[52%] rounded-[50%] border-2 border-dashed border-white/70"
          />
        )}
        {countdown !== null && countdown > 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40">
            <span className="text-6xl font-bold text-white drop-shadow">{countdown}</span>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={startCountdown}
        disabled={!ready || countdown !== null}
        className="btn btn-primary w-full max-w-xs"
      >
        <CameraIcon />
        {countdown !== null ? 'Capturing...' : 'Capture Photo'}
      </button>
    </div>
  )
}
