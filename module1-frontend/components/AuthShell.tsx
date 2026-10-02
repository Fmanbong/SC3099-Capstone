import { LogoMark } from './icons'

interface AuthShellProps {
  title: string
  subtitle: string
  children: React.ReactNode
  /** Line under the card, e.g. the link to the other auth page. */
  footer: React.ReactNode
}

/** Shared layout for the signed-out pages (login and register). */
export default function AuthShell({ title, subtitle, children, footer }: AuthShellProps) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-b from-blue-50 to-slate-50 px-4 py-10">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <LogoMark className="h-14 w-14 rounded-[23%] shadow-md shadow-blue-700/20" />
          <div>
            <p className="text-xl font-bold tracking-tight">SAIV</p>
            <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
              Secure Attendance
            </p>
          </div>
        </div>

        <div className="card space-y-5 p-6">
          <div>
            <h1 className="text-xl font-bold tracking-tight">{title}</h1>
            <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
          </div>
          {children}
        </div>

        <p className="text-center text-sm text-slate-600">{footer}</p>
      </div>
    </main>
  )
}
