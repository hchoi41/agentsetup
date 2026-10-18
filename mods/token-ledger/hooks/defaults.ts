// Starting settings per task type, from the provider guide in ../guide/MODEL_GUIDE.md.
// Used only until the ledger has `minSamples` runs of a type; evidence then takes over.
// Refreshed monthly by the scheduled guide check: keep ids current and keys in step with TYPES.

export const GUIDE_CHECKED = '2026-10-18'

export const CLAUDE_DEFAULTS: Readonly<Record<string, { model: string; effort: string }>> = {
  debug: { model: 'claude-sonnet-5-5', effort: 'high' },
  test: { model: 'claude-sonnet-5-5', effort: 'medium' },
  review: { model: 'claude-opus-5-5', effort: 'medium' },
  refactor: { model: 'claude-opus-5-5', effort: 'medium' },
  docs: { model: 'claude-sonnet-5-5', effort: 'low' },
  research: { model: 'claude-fable-5-1', effort: 'high' },
  build: { model: 'claude-opus-5-5', effort: 'medium' },
  ops: { model: 'claude-sonnet-5-5', effort: 'low' },
  question: { model: 'claude-haiku-5-5', effort: 'low' },
}
