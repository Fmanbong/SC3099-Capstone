/**
 * Central token store. Kept separate from React context so the axios
 * interceptor (lib/api.ts) can read/write tokens without importing React.
 *
 * Per the security briefing: access token lives in memory + sessionStorage
 * (tab-scoped, cleared on browser close), never in localStorage or the URL.
 */
import type { AuthTokens, User } from './types'

const ACCESS_KEY = 'saiv_access_token'
const REFRESH_KEY = 'saiv_refresh_token'
const USER_KEY = 'saiv_user'

let accessToken: string | null = null
let refreshToken: string | null = null

function hasStorage() {
  return typeof window !== 'undefined' && !!window.sessionStorage
}

export function hydrate() {
  if (!hasStorage()) return
  accessToken = window.sessionStorage.getItem(ACCESS_KEY)
  refreshToken = window.sessionStorage.getItem(REFRESH_KEY)
}

export function getAccessToken() {
  return accessToken
}

export function getRefreshToken() {
  return refreshToken
}

export function setTokens(tokens: AuthTokens) {
  accessToken = tokens.access_token
  refreshToken = tokens.refresh_token
  if (hasStorage()) {
    window.sessionStorage.setItem(ACCESS_KEY, tokens.access_token)
    window.sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token)
  }
}

export function getStoredUser(): User | null {
  if (!hasStorage()) return null
  const raw = window.sessionStorage.getItem(USER_KEY)
  return raw ? JSON.parse(raw) : null
}

export function setStoredUser(user: User) {
  if (hasStorage()) {
    window.sessionStorage.setItem(USER_KEY, JSON.stringify(user))
  }
}

export function clearTokens() {
  accessToken = null
  refreshToken = null
  if (hasStorage()) {
    window.sessionStorage.removeItem(ACCESS_KEY)
    window.sessionStorage.removeItem(REFRESH_KEY)
    window.sessionStorage.removeItem(USER_KEY)
  }
}

/** Called by the axios interceptor when the refresh token is also invalid. */
let onSessionExpired: (() => void) | null = null
export function registerSessionExpiredHandler(fn: () => void) {
  onSessionExpired = fn
}
export function notifySessionExpired() {
  clearTokens()
  onSessionExpired?.()
}
