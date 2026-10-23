'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { requestCameraStream, stopStream, captureFrame, recordFrames, CameraError } from '@/lib/camera'
import type { LivenessChallenge } from '@/lib/types'
import Alert from './Alert'
import Spinner from './Spinner'
import { CameraIcon, RefreshIcon } from './icons'

// 12 frames over ~3 s: long enough to blink or turn and come back, and within
// the 12-frame limit the face service accepts.
const CHALLENGE_FRAMES = 12
const CHALLENGE_INTERVAL_MS = 250

const challengeCopy: Record<LivenessChallenge, { before: string; during: string }> = {
  blink: {
    before: 'When recording starts, look at the camera and blink twice.',
    during: 'Blink now',
  },
  head_turn: {
    before: 'When recording starts, slowly turn your head to one side, then back to the camera.',
    during: 'Turn your head now',
  },
}

interface CameraCaptureProps {
  /** Receives the first (neutral) frame and, in challenge mode, the whole recorded sequence. */
  onCapture: (base64Image: string, frames?: string[]) => void
  instructions?: string
  /** Seconds to count down before auto-capturing, giving the liveness cue (e.g. "blink now") time to happen. */
  countdownSeconds?: number
  /** Record a short sequence while the user performs this action, instead of a single photo. */
  challenge?: LivenessChallenge
}

export default function CameraCapture({
  onCapture,
  instructions = 'Look directly at the camera and blink naturally.',
  countdownSeconds = 3,
  challenge,
}: CameraCaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [countdown, setCountdown] = useState<number | null>(null)
  const [recording, setRecording] = useState(false)
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

  const doRecord = useCallback(async () => {
    setRecording(true)
    try {
      const frames = await recordFrames(() => videoRef.current, CHALLENGE_FRAMES, CHALLENGE_INTERVAL_MS)
      if (frames.length < CHALLENGE_FRAMES) return // camera went away mid-recording
      setCapturedPreview(`data:image/jpeg;base64,${frames[0]}`)
      onCapture(frames[0], frames)
    } catch {
      setError('Could not record from the camera. Please try again.')
    } finally {
      setRecording(false)
    }
  }, [onCapture])

  const startCountdown = useCallback(() => {
    setCountdown(countdownSeconds)
  }, [countdownSeconds])

  useEffect(() => {
    if (countdown === null) return
    if (countdown === 0) {
      if (challenge) doRecord()
      else doCapture()
      setCountdown(null)
      return
    }
    const timer = setTimeout(() => setCountdown((c) => (c ?? 1) - 1), 1000)
    return () => clearTimeout(timer)
  }, [countdown, doCapture, doRecord, challenge])

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
      <p className="text-sm text-slate-600">{challenge ? challengeCopy[challenge].before : instructions}</p>
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
        {recording && challenge && (
          <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 bg-black/55 py-3 text-lg font-semibold text-white">
            <span aria-hidden="true" className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-500" />
            {challengeCopy[challenge].during}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={startCountdown}
        disabled={!ready || countdown !== null || recording}
        className="btn btn-primary w-full max-w-xs"
      >
        <CameraIcon />
        {recording ? 'Recording...' : countdown !== null ? 'Get ready...' : challenge ? 'Start' : 'Capture Photo'}
      </button>
    </div>
  )
}
