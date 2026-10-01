'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { displayText } from '@/lib/text'

const links = [
  { href: '/checkin', label: 'Check In' },
  { href: '/history', label: 'History' },
  { href: '/enroll', label: 'Face Enrollment' },
  { href: '/profile', label: 'Profile' },
]

export default function Navbar() {
  const { user, logout, isAuthenticated } = useAuth()
  const pathname = usePathname()

  if (!isAuthenticated) return null

  return (
    <nav className="bg-blue-700 text-white">
      <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between flex-wrap gap-2">
        <span className="font-bold">SAIV</span>
        <div className="flex gap-4 text-sm flex-wrap">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={pathname === link.href ? 'font-semibold underline' : 'opacity-90 hover:opacity-100'}
            >
              {link.label}
            </Link>
          ))}
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="opacity-90">{displayText(user?.full_name)}</span>
          <button onClick={logout} className="bg-blue-900 px-3 py-1 rounded hover:bg-blue-800">
            Logout
          </button>
        </div>
      </div>
    </nav>
  )
}
