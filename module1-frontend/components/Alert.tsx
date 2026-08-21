interface AlertProps {
  type?: 'error' | 'success' | 'info' | 'warning'
  children: React.ReactNode
}

const styles: Record<string, string> = {
  error: 'bg-red-50 border-red-500 text-red-800',
  success: 'bg-green-50 border-green-500 text-green-800',
  info: 'bg-blue-50 border-blue-500 text-blue-800',
  warning: 'bg-yellow-50 border-yellow-500 text-yellow-800',
}

export default function Alert({ type = 'info', children }: AlertProps) {
  return (
    <div className={`border-l-4 p-3 rounded text-sm ${styles[type]}`} role="alert">
      {children}
    </div>
  )
}
