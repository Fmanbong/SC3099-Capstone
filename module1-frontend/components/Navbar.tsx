'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { displayText } from '@/lib/text'
import { CheckCircleIcon, ClockIcon, FaceScanIcon, LogoMark, LogoutIcon, UserIcon } from './icons'

// `short` is the label used in the phone tab bar, where space is tight.
const links = [
  { href: '/checkin', label: 'Check In', short: 'Check In', icon: CheckCircleIcon },
  { href: '/history', label: 'History', short: 'History', icon: ClockIcon },
  { href: '/enroll', label: 'Face Enrollment', short: 'Enroll', icon: FaceScanIcon },
  { href: '/profile', label: 'Profile', short: 'Profile', icon: UserIcon },
]

export default function Navbar() {
  const { user, logout, isAuthenticated } = useAuth()
  const pathname = usePathname()

  if (!isAuthenticated) return null

  return (
    <>
      <header className="sticky top-0 z-20 bg-blue-700 text-white shadow-sm">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between gap-3 px-4">
          <Link href="/checkin" className="flex items-center gap-2 text-lg font-bold tracking-tight">
            <LogoMark className="h-8 w-8 rounded-lg ring-1 ring-white/30" />
            SAIV
          </Link>

          {/* Links sit in the header on wider screens; phones use the tab bar below. */}
          <nav aria-label="Main" className="hidden items-center gap-1 text-sm sm:flex">
            {links.map((link) => {
              const active = pathname === link.href
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? 'page' : undefined}
                  className={`rounded-lg px-3 py-1.5 transition-colors ${
                    active ? 'bg-white/20 font-semibold' : 'text-blue-100 hover:bg-white/10 hover:text-white'
                  }`}
                >
                  {link.label}
                </Link>
              )
            })}
          </nav>

          <div className="flex min-w-0 items-center gap-2 text-sm">
            <span className="truncate text-blue-100">{displayText(user?.full_name)}</span>
            <button
              onClick={logout}
              className="flex shrink-0 items-center gap-1.5 rounded-lg bg-white/15 px-3 py-1.5 font-medium transition-colors hover:bg-white/25"
            >
              <LogoutIcon className="h-4 w-4" />
              Logout
            </button>
          </div>
        </div>
      </header>

      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] sm:hidden"
      >
        <div className="mx-auto grid max-w-md grid-cols-4">
          {links.map((link) => {
            const active = pathname === link.href
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? 'page' : undefined}
                className={`flex flex-col items-center gap-1 py-2.5 text-xs font-medium transition-colors ${
                  active ? 'text-blue-700' : 'text-slate-500 hover:text-slate-900'
                }`}
              >
                <link.icon className="h-6 w-6" />
                {link.short}
              </Link>
            )
          })}
        </div>
      </nav>
    </>
  )
}
