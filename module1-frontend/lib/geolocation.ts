export interface GeoResult {
  latitude: number
  longitude: number
  accuracy: number
}

export class GeolocationError extends Error {
  code: 'unsupported' | 'denied' | 'unavailable' | 'timeout'
  constructor(code: GeolocationError['code'], message: string) {
    super(message)
    this.code = code
  }
}

/**
 * Requests the current position. Must be called from a user-initiated
 * action (e.g. a button click) after explicit consent has been recorded,
 * per the browser-permissions best practices in the briefing.
 */
export function getCurrentPosition(): Promise<GeoResult> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new GeolocationError('unsupported', 'Geolocation is not supported on this device.'))
      return
    }

    navigator.geolocation.getCurrentPosition(
      (position) => {
        resolve({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        })
      },
      (error) => {
        if (error.code === error.PERMISSION_DENIED) {
          reject(new GeolocationError('denied', 'Location permission was denied.'))
        } else if (error.code === error.TIMEOUT) {
          reject(new GeolocationError('timeout', 'Location request timed out.'))
        } else {
          reject(new GeolocationError('unavailable', 'Location is currently unavailable.'))
        }
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    )
  })
}

export async function queryPermission(name: 'camera' | 'geolocation'): Promise<PermissionState | 'unsupported'> {
  if (!('permissions' in navigator)) return 'unsupported'
  try {
    // Camera isn't in the TS PermissionName union in all lib versions.
    const status = await navigator.permissions.query({ name: name as PermissionName })
    return status.state
  } catch {
    return 'unsupported'
  }
}
