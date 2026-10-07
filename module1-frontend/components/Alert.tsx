import { CheckCircleIcon, InfoIcon, WarningIcon, XCircleIcon } from './icons'

interface AlertProps {
  type?: 'error' | 'success' | 'info' | 'warning'
  children: React.ReactNode
}

const styles: Record<string, string> = {
  error: 'bg-red-50 border-red-200 text-red-800',
  success: 'bg-green-50 border-green-200 text-green-800',
  info: 'bg-blue-50 border-blue-200 text-blue-800',
  warning: 'bg-amber-50 border-amber-200 text-amber-900',
}

const icons: Record<string, (props: { className?: string }) => JSX.Element> = {
  error: XCircleIcon,
  success: CheckCircleIcon,
  info: InfoIcon,
  warning: WarningIcon,
}

export default function Alert({ type = 'info', children }: AlertProps) {
  const TypeIcon = icons[type]
  return (
    <div
      className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-left text-sm ${styles[type]}`}
      role="alert"
    >
      <TypeIcon className="mt-px h-5 w-5" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}
