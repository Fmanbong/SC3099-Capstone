const ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  '#x27': "'",
  '#39': "'",
}

/**
 * The backend HTML-escapes names before storing them (O'Brien is returned as
 * O&#x27;Brien). React escapes text again when rendering, so the entities
 * would be shown literally; decode them for display. This is safe because the
 * result is only ever rendered as text, never as HTML.
 */
export function displayText(value: string | null | undefined): string {
  if (!value) return ''
  return value.replace(/&(amp|lt|gt|quot|#x27|#39);/g, (_, entity: string) => ENTITIES[entity])
}
