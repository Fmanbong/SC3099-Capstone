export class CameraError extends Error {
  code: 'unsupported' | 'denied' | 'unavailable'
  constructor(code: CameraError['code'], message: string) {
    super(message)
    this.code = code
  }
}

/**
 * Requests a front-facing camera stream. Caller is responsible for
 * releasing it via stopStream() once the frame(s) needed have been
 * captured (see WebRTC guidance in the briefing).
 */
export async function requestCameraStream(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new CameraError('unsupported', 'Camera access is not supported in this browser.')
  }

  try {
    return await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    })
  } catch (err) {
    const name = err instanceof DOMException ? err.name : ''
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      throw new CameraError('denied', 'Camera permission was denied.')
    }
    throw new CameraError('unavailable', 'No camera is available on this device.')
  }
}

export function stopStream(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop())
}

/** Captures the current frame of a playing <video> element as base64 JPEG (no data-URL prefix). */
export function captureFrame(video: HTMLVideoElement): string {
  // A <video> with no frame yet reports 0x0 and would yield an empty image.
  if (!video.videoWidth || !video.videoHeight) throw new Error('Camera is not ready')
  const canvas = document.createElement('canvas')
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas not supported')
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  const dataUrl = canvas.toDataURL('image/jpeg', 0.9)
  return dataUrl.replace(/^data:image\/\w+;base64,/, '')
}
