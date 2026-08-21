'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'

export default function Home() {
  const { isAuthenticated, loading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (loading) return
    router.replace(isAuthenticated ? '/checkin' : '/login')
  }, [loading, isAuthenticated, router])

  return (
    <main className="min-h-screen flex items-center justify-center">
      <p className="text-gray-500">Loading SAIV...</p>
    </main>
  )
}
