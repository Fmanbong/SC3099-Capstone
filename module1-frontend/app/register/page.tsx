'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import Alert from '@/components/Alert'
import AuthShell from '@/components/AuthShell'
import Spinner from '@/components/Spinner'

export default function RegisterPage() {
  const { register } = useAuth()
  const router = useRouter()

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }

    setSubmitting(true)
    try {
      await register(email, password, fullName, 'student')
      setSuccess(true)
      setTimeout(() => router.replace('/login'), 1200)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell
      title="Create account"
      subtitle="Register as a student"
      footer={
        <>
          Already have an account?{' '}
          <Link href="/login" className="font-semibold text-blue-700 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      {error && <Alert type="error">{error}</Alert>}
      {success && <Alert type="success">Account created! Redirecting to sign in...</Alert>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="field-label" htmlFor="fullName">
            Full Name
          </label>
          <input
            id="fullName"
            required
            autoComplete="name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="field-input"
          />
        </div>
        <div>
          <label className="field-label" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            placeholder="name@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="field-input"
          />
        </div>
        <div>
          <label className="field-label" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="field-input"
          />
          <p className="mt-1.5 text-xs text-slate-500">Minimum 8 characters.</p>
        </div>
        <button type="submit" disabled={submitting} className="btn btn-primary w-full">
          {submitting && <Spinner />}
          {submitting ? 'Creating account...' : 'Register'}
        </button>
      </form>
    </AuthShell>
  )
}
