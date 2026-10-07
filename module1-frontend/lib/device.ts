import localforage from 'localforage'
import { api, extractErrorCode } from './api'
import type { Device, DeviceChallenge } from './types'

const IDENTITY_KEY = 'identity'
// Written by earlier builds, which kept only the public key. A device
// registered from those can never sign, so they are dropped and replaced.
const LEGACY_KEYS = ['saiv_device_fingerprint', 'saiv_device_keypair']

// A CryptoKey survives only structured-clone storage, so this store is pinned
// to IndexedDB instead of letting localforage fall back to localStorage.
const deviceStore = localforage.createInstance({
  name: 'saiv',
  storeName: 'device',
  driver: localforage.INDEXEDDB,
})

export interface DeviceIdentity {
  fingerprint: string
  publicKeyPem: string
  privateKey: CryptoKey
}

export interface DeviceProof {
  device_challenge_id: string
  device_signature: string
}

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
async function generateFingerprint(): Promise<string> {
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
  return sha256Hex(parts.join('||'))
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

async function loadOrCreateIdentity(): Promise<DeviceIdentity> {
  try {
    const stored = await deviceStore.getItem<DeviceIdentity>(IDENTITY_KEY)
    if (stored?.fingerprint && stored.publicKeyPem && stored.privateKey) return stored
  } catch {
    // Storage unavailable (e.g. private browsing): fall through to a fresh identity.
  }

  // The fingerprint and key pair are created together and never mixed with
  // older values: the backend binds one public key to one fingerprint.
  const keyPair = await crypto.subtle.generateKey(
    { name: 'ECDSA', namedCurve: 'P-256' },
    false,
    ['sign', 'verify']
  )
  const spki = await crypto.subtle.exportKey('spki', keyPair.publicKey)
  const identity: DeviceIdentity = {
    fingerprint: await generateFingerprint(),
    publicKeyPem: toPem(arrayBufferToBase64(spki)),
    privateKey: keyPair.privateKey,
  }

  try {
    await deviceStore.setItem(IDENTITY_KEY, identity)
    await Promise.all(LEGACY_KEYS.map((key) => localforage.removeItem(key)))
  } catch {
    // Not persisted: the identity still works until the page is closed.
  }
  return identity
}

let identityPromise: Promise<DeviceIdentity> | null = null

/**
 * Loads (or creates) this browser's device identity: a fingerprint plus an
 * ECDSA P-256 key pair. The private key is generated non-extractable and is
 * stored as a CryptoKey handle, so its key material cannot be read by script
 * or sent anywhere; it can only be used to sign on this device.
 */
export function getDeviceIdentity(): Promise<DeviceIdentity> {
  if (!identityPromise) {
    identityPromise = loadOrCreateIdentity().catch((err) => {
      identityPromise = null
      throw err
    })
  }
  return identityPromise
}

async function registerDevice(identity: DeviceIdentity): Promise<string | null> {
  try {
    const res = await api.post<Device>('/devices/register', {
      device_fingerprint: identity.fingerprint,
      device_name: `${detectPlatform()} browser`,
      platform: detectPlatform(),
      public_key: identity.publicKeyPem,
    })
    return res.data.id
  } catch {
    // Most likely the fingerprint belongs to another account on a shared
    // device. The check-in still goes through and the backend flags it.
    return null
  }
}

/** Returns the backend id of this device for the signed-in user, registering it if needed. */
export async function ensureDeviceRegistered(): Promise<string | null> {
  const identity = await getDeviceIdentity()
  const res = await api.get<Device[]>('/devices/my-devices')
  const existing = res.data.find((d) => d.device_fingerprint === identity.fingerprint)
  return existing?.id ?? registerDevice(identity)
}

function requestChallenge(deviceId: string, sessionId: string): Promise<DeviceChallenge> {
  return api
    .post<DeviceChallenge>(`/devices/${deviceId}/challenge`, { session_id: sessionId })
    .then((res) => res.data)
}

/**
 * Proves this check-in comes from the registered device: asks the backend
 * for a one-use nonce tied to the session and signs it with the device key.
 * Returns null if no proof could be produced; the check-in is then submitted
 * without one and the backend flags it for review instead of trusting it.
 */
export async function createDeviceProof(sessionId: string): Promise<DeviceProof | null> {
  try {
    const identity = await getDeviceIdentity()
    let deviceId = await ensureDeviceRegistered()
    if (!deviceId) return null

    let challenge: DeviceChallenge
    try {
      challenge = await requestChallenge(deviceId, sessionId)
    } catch (err) {
      if (extractErrorCode(err) !== 'DEVICE_KEY_REQUIRED') throw err
      // The backend created this device itself from an earlier unsigned
      // check-in, so it has no public key. Replace it with a keyed one.
      await api.delete(`/devices/${deviceId}`)
      deviceId = await registerDevice(identity)
      if (!deviceId) return null
      challenge = await requestChallenge(deviceId, sessionId)
    }

    const signature = await crypto.subtle.sign(
      { name: 'ECDSA', hash: 'SHA-256' },
      identity.privateKey,
      new TextEncoder().encode(challenge.nonce)
    )
    return {
      device_challenge_id: challenge.id,
      device_signature: arrayBufferToBase64(signature),
    }
  } catch {
    return null
  }
}
