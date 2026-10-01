import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { API_BASE } from './config'
import {
  getAccessToken,
  getRefreshToken,
  setTokens,
  notifySessionExpired,
} from './token-store'
import type { AuthTokens } from './types'

export const api = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
})

// Plain client (no interceptors) used only for the refresh call itself,
// to avoid infinite refresh loops.
const rawClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken()
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refresh_token = getRefreshToken()
  if (!refresh_token) return null

  if (!refreshPromise) {
    refreshPromise = rawClient
      .post<AuthTokens>('/auth/refresh', { refresh_token })
      .then((res) => {
        setTokens(res.data)
        return res.data.access_token
      })
      .catch(() => {
        notifySessionExpired()
        return null
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true
      const newToken = await refreshAccessToken()
      if (newToken) {
        originalRequest.headers = originalRequest.headers ?? {}
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return api(originalRequest)
      }
    }

    return Promise.reject(error)
  }
)

/** The backend's machine-readable error code (e.g. 'CHECKIN_EXISTS'), if it sent one. */
export function extractErrorCode(err: unknown): string | undefined {
  if (!axios.isAxiosError(err)) return undefined
  const code = (err.response?.data as { code?: unknown } | undefined)?.code
  return typeof code === 'string' ? code : undefined
}

export function extractErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const status = err.response?.status
    const data = err.response?.data as { detail?: unknown } | undefined

    if (status === 429) {
      const retryAfter = err.response?.headers?.['retry-after']
      return retryAfter
        ? `Too many requests. Please try again in ${retryAfter}s.`
        : 'Too many requests. Please slow down and try again shortly.'
    }

    if (data?.detail) {
      if (typeof data.detail === 'string') return data.detail
      if (Array.isArray(data.detail)) {
        return data.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ')
      }
    }

    if (err.message === 'Network Error') {
      return 'Cannot reach the server. Check your connection and try again.'
    }
  }
  return 'Something went wrong. Please try again.'
}
