'use client'

import { useEffect } from 'react'
import { flushQueue } from '@/lib/offline-queue'

export default function PwaSetup() {
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        // Non-fatal: app still works without offline caching.
      })
    }

    const trySync = () => {
      flushQueue().catch(() => {})
    }

    window.addEventListener('online', trySync)
    trySync()

    return () => window.removeEventListener('online', trySync)
  }, [])

  return null
}
