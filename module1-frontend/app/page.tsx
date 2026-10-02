'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { LoadingState } from '@/components/Spinner'

export default function Home() {
  const { isAuthenticated, loading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (loading) return
    router.replace(isAuthenticated ? '/checkin' : '/login')
  }, [loading, isAuthenticated, router])

  return (
    <main className="flex min-h-screen items-center justify-center">
      <LoadingState>Loading SAIV...</LoadingState>
    </main>
  )
}
