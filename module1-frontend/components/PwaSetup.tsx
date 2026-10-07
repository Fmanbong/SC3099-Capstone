'use client'

import { useEffect } from 'react'
import localforage from 'localforage'

// Earlier builds queued failed check-ins here, including the captured face
// image. Check-ins are now only ever submitted live, so anything left over
// is deleted rather than replayed.
const LEGACY_QUEUE_KEY = 'saiv_pending_checkins'

export default function PwaSetup() {
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        // Non-fatal: app still works without offline caching.
      })
    }

    localforage.removeItem(LEGACY_QUEUE_KEY).catch(() => {})
  }, [])

  return null
}
