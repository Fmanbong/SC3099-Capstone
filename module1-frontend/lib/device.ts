import localforage from 'localforage'

const FINGERPRINT_KEY = 'saiv_device_fingerprint'
const KEYPAIR_KEY = 'saiv_device_keypair'

async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

function canvasSignature(): string {
  try {
    const canvas = document.createElement('canvas')
    canvas.width = 220
    canvas.height = 30
    const ctx = canvas.getContext('2d')
    if (!ctx) return 'no-canvas'
    ctx.textBaseline = 'top'
    ctx.font = '14px Arial'
    ctx.fillStyle = '#f60'
    ctx.fillRect(0, 0, 100, 20)
    ctx.fillStyle = '#069'
    ctx.fillText('SAIV-device-fp', 2, 2)
    return canvas.toDataURL()
  } catch {
    return 'canvas-unavailable'
  }
}

/**
 * Builds a stable, non-identifying fingerprint from browser/device
 * characteristics. Persisted locally so it stays constant across sessions
 * for THIS browser install, without needing a hardware ID.
 */
export async function getDeviceFingerprint(): Promise<string> {
  const cached = await localforage.getItem<string>(FINGERPRINT_KEY)
  if (cached) return cached

  const parts = [
    navigator.userAgent,
    navigator.language,
    String(screen.width),
    String(screen.height),
    String(screen.colorDepth),
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    String(navigator.hardwareConcurrency || 0),
    canvasSignature(),
    // Random component so two identical devices don't collide, and the
    // fingerprint can't be used to track the user across sites.
    crypto.randomUUID(),
  ]

  const fingerprint = await sha256Hex(parts.join('||'))
  await localforage.setItem(FINGERPRINT_KEY, fingerprint)
  return fingerprint
}

export function detectPlatform(): 'ios' | 'android' | 'web' | 'desktop' {
  if (typeof navigator === 'undefined') return 'web'
  const ua = navigator.userAgent.toLowerCase()
  if (/iphone|ipad|ipod/.test(ua)) return 'ios'
  if (/android/.test(ua)) return 'android'
  if (/mobi/.test(ua)) return 'web'
  return 'desktop'
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  bytes.forEach((b) => (binary += String.fromCharCode(b)))
  return btoa(binary)
}

function toPem(base64: string): string {
  const lines = base64.match(/.{1,64}/g) ?? [base64]
  return `-----BEGIN PUBLIC KEY-----\n${lines.join('\n')}\n-----END PUBLIC KEY-----`
}

interface StoredKeyPair {
  publicKeyPem: string
}

/**
 * Generates (or loads) an ECDSA P-256 key pair for this device, used to
 * identify the device on POST /devices/register. The private key is
 * generated non-extractable and never leaves this function or touches
 * storage/network; only the exported public key (PEM) is persisted and
 * sent to the backend.
 */
export async function getOrCreateDeviceKeyPair(): Promise<StoredKeyPair> {
  const cached = await localforage.getItem<StoredKeyPair>(KEYPAIR_KEY)
  if (cached) return cached

  const keyPair = await crypto.subtle.generateKey(
    { name: 'ECDSA', namedCurve: 'P-256' },
    false,
    ['sign', 'verify']
  )

  const spki = await crypto.subtle.exportKey('spki', keyPair.publicKey)
  const publicKeyPem = toPem(arrayBufferToBase64(spki))

  const stored: StoredKeyPair = { publicKeyPem }
  await localforage.setItem(KEYPAIR_KEY, stored)
  return stored
}
