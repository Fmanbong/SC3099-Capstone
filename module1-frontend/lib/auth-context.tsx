'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react'
import { useRouter } from 'next/navigation'
import { api, extractErrorMessage } from './api'
import {
  hydrate,
  getAccessToken,
  setTokens,
  setStoredUser,
  getStoredUser,
  clearTokens,
  registerSessionExpiredHandler,
} from './token-store'
import type { LoginResponse, Role, User } from './types'

interface AuthContextValue {
  user: User | null
  loading: boolean
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName: string, role?: Role) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  updateUser: (partial: Partial<User>) => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  const logout = useCallback(async () => {
    // Revoke the login session on the server before dropping the tokens, so
    // they stop working everywhere. Awaited (not fire-and-forget) because the
    // request needs the tokens that clearTokens() removes; signing out locally
    // must still succeed if the server cannot be reached.
    try {
      await api.post('/auth/logout', undefined, { timeout: 5000 })
    } catch {
      // Already expired/revoked, or offline.
    }
    clearTokens()
    setUser(null)
    router.push('/login')
  }, [router])

  useEffect(() => {
    hydrate()
    registerSessionExpiredHandler(() => {
      setUser(null)
      router.push('/login?expired=1')
    })

    const token = getAccessToken()
    const storedUser = getStoredUser()
    if (token && storedUser) {
      setUser(storedUser)
    }
    setLoading(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    try {
      const res = await api.post<LoginResponse>('/auth/login', { email, password })
      const { user: loggedInUser, ...tokens } = res.data
      setTokens(tokens)
      setStoredUser(loggedInUser)
      setUser(loggedInUser)
    } catch (err) {
      throw new Error(extractErrorMessage(err))
    }
  }, [])

  const register = useCallback(
    async (email: string, password: string, fullName: string, role: Role = 'student') => {
      try {
        await api.post('/auth/register', {
          email,
          password,
          full_name: fullName,
          role,
        })
      } catch (err) {
        throw new Error(extractErrorMessage(err))
      }
    },
    []
  )

  const refreshUser = useCallback(async () => {
    try {
      const res = await api.get<User>('/users/me')
      setUser(res.data)
      setStoredUser(res.data)
    } catch {
      // Interceptor handles expiry/logout; nothing else to do here.
    }
  }, [])

  const updateUser = useCallback((partial: Partial<User>) => {
    setUser((prev) => {
      if (!prev) return prev
      const next = { ...prev, ...partial }
      setStoredUser(next)
      return next
    })
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isAuthenticated: !!user,
        login,
        register,
        logout,
        refreshUser,
        updateUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
