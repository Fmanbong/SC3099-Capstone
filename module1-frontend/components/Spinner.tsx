interface SpinnerProps {
  className?: string
}

/** A small spinning ring. Takes the surrounding text colour. */
export default function Spinner({ className = 'h-4 w-4' }: SpinnerProps) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block shrink-0 animate-spin rounded-full border-2 border-current border-r-transparent ${className}`}
    />
  )
}

/** Centred spinner with a message, for sections that are still loading. */
export function LoadingState({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="status"
      className="flex flex-col items-center justify-center gap-3 py-10 text-sm text-slate-500"
    >
      <Spinner className="h-6 w-6 text-blue-700" />
      <p>{children}</p>
    </div>
  )
}
