import localforage from 'localforage'
import { api } from './api'

const QUEUE_KEY = 'saiv_pending_checkins'

export interface PendingCheckin {
  localId: string
  payload: {
    session_id: string
    latitude: number
    longitude: number
    location_accuracy_meters: number
    device_fingerprint: string
    liveness_challenge_response?: string | null
  }
  queuedAt: string
}

/**
 * Check-ins must be real-time per the offline-caching guidance, so they are
 * never served from cache. Instead, if a submission fails because the
 * device is offline, it's queued locally and retried automatically once
 * connectivity returns.
 */
export async function queueCheckin(payload: PendingCheckin['payload']): Promise<PendingCheckin> {
  const queue = (await localforage.getItem<PendingCheckin[]>(QUEUE_KEY)) ?? []
  const entry: PendingCheckin = {
    localId: crypto.randomUUID(),
    payload,
    queuedAt: new Date().toISOString(),
  }
  queue.push(entry)
  await localforage.setItem(QUEUE_KEY, queue)
  return entry
}

export async function getQueuedCheckins(): Promise<PendingCheckin[]> {
  return (await localforage.getItem<PendingCheckin[]>(QUEUE_KEY)) ?? []
}

async function removeFromQueue(localId: string) {
  const queue = (await localforage.getItem<PendingCheckin[]>(QUEUE_KEY)) ?? []
  await localforage.setItem(
    QUEUE_KEY,
    queue.filter((item) => item.localId !== localId)
  )
}

/** Attempts to flush all queued check-ins. Leaves still-failing ones queued. */
export async function flushQueue(): Promise<{ succeeded: number; failed: number }> {
  const queue = await getQueuedCheckins()
  let succeeded = 0
  let failed = 0

  for (const item of queue) {
    try {
      await api.post('/checkins/', item.payload)
      await removeFromQueue(item.localId)
      succeeded++
    } catch {
      failed++
    }
  }

  return { succeeded, failed }
}
