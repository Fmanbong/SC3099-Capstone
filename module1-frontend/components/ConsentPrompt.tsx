interface ConsentPromptProps {
  title: string
  description: string
  onGrant: () => void
  granting?: boolean
}

/**
 * Explains WHY a permission is needed before the browser prompt fires,
 * per the "explain before requesting" best practice. Consent is recorded
 * on the backend (camera_consent / geolocation_consent) before the
 * corresponding browser API is invoked.
 */
export default function ConsentPrompt({ title, description, onGrant, granting }: ConsentPromptProps) {
  return (
    <div className="bg-white border rounded-lg p-5 text-center space-y-3">
      <h3 className="font-semibold text-lg">{title}</h3>
      <p className="text-sm text-gray-600">{description}</p>
      <button
        type="button"
        onClick={onGrant}
        disabled={granting}
        className="bg-blue-700 text-white px-4 py-2 rounded disabled:opacity-50"
      >
        {granting ? 'Please wait...' : 'Allow'}
      </button>
    </div>
  )
}
