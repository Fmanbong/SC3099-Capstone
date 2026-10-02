import Spinner from './Spinner'

interface ConsentPromptProps {
  title: string
  description: string
  onGrant: () => void
  granting?: boolean
  /** Icon shown above the title, e.g. a camera or map pin. */
  icon?: React.ReactNode
}

/**
 * Explains WHY a permission is needed before the browser prompt fires,
 * per the "explain before requesting" best practice. Consent is recorded
 * on the backend (camera_consent / geolocation_consent) before the
 * corresponding browser API is invoked.
 */
export default function ConsentPrompt({
  title,
  description,
  onGrant,
  granting,
  icon,
}: ConsentPromptProps) {
  return (
    <div className="card space-y-4 p-6 text-center">
      {icon && (
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-blue-50 text-blue-700">
          {icon}
        </div>
      )}
      <div className="space-y-1.5">
        <h3 className="text-lg font-semibold">{title}</h3>
        <p className="text-sm leading-relaxed text-slate-600">{description}</p>
      </div>
      <button
        type="button"
        onClick={onGrant}
        disabled={granting}
        className="btn btn-primary w-full"
      >
        {granting && <Spinner />}
        {granting ? 'Please wait...' : 'Allow'}
      </button>
    </div>
  )
}
