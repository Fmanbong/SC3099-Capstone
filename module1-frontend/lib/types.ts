export type Role = 'student' | 'ta' | 'instructor' | 'admin'

export interface User {
  id: string
  email: string
  full_name: string
  role: Role
  is_active?: boolean
  camera_consent?: boolean
  geolocation_consent?: boolean
  face_enrolled?: boolean
  created_at?: string
}

export interface AuthTokens {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface LoginResponse extends AuthTokens {
  user: User
}

export interface Session {
  id: string
  course_id: string
  course_code?: string
  course_name?: string
  instructor_id?: string
  name: string
  session_type?: string
  status: 'scheduled' | 'active' | 'closed' | 'cancelled'
  scheduled_start: string
  scheduled_end: string
  checkin_opens_at?: string
  checkin_closes_at?: string
  venue_name?: string
  venue_latitude?: number
  venue_longitude?: number
  geofence_radius_meters?: number
  require_liveness_check?: boolean
  require_face_match?: boolean
}

export interface RiskFactor {
  type: string
  weight?: number
  severity?: string
}

export interface CheckinResult {
  id: string
  session_id: string
  student_id?: string
  status: 'pending' | 'approved' | 'flagged' | 'rejected'
  checked_in_at: string
  latitude?: number
  longitude?: number
  distance_from_venue_meters?: number
  liveness_passed?: boolean
  liveness_score?: number
  risk_score?: number
  risk_factors?: RiskFactor[]
}

export interface CheckinHistoryItem {
  id: string
  session_id: string
  session_name?: string
  course_code?: string
  status: string
  checked_in_at: string
  risk_score?: number
}

export interface Device {
  id: string
  device_fingerprint?: string
  device_name: string
  platform: string
  is_trusted?: boolean
  trust_score?: string
  is_active?: boolean
  first_seen_at?: string
  last_seen_at?: string
  total_checkins?: number
}

export interface ApiError {
  detail?: string | { loc: (string | number)[]; msg: string; type: string }[]
}
