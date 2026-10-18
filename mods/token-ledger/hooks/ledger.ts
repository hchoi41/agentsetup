// Pure ledger logic: task typing, cost, summaries and recommendations.
// No `$` here, so the same code runs in hooks and in tests.

export type Usage = {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
}

export type Issue = {
  kind: 'tool-error' | 'denied' | 'retry' | 'turn-end'
  tool?: string
  detail: string
}

export type Outcome = 'answer' | 'aborted' | 'refusal' | 'error'

export type LedgerRecord = {
  v: 1
  id: string
  at: number
  cwd: string
  type: string
  typeSource: 'tag' | 'auto'
  promptChars: number
  model: string
  effort: string
  applied: boolean
  steps: number
  durationMs: number
  outcome: Outcome
  usage: Usage
  subagents: { runs: number; usage: Usage; byModel?: Record<string, Usage> }
  tools: Record<string, number>
  commands: Record<string, number>
  issues: Issue[]
  rating?: 'good' | 'bad'
  /** Set when the next prompt soon after reads like a complaint about this answer. */
  suspect?: boolean
  /** Which machine recorded it (a sanitized computer name), set when the record is made. */
  machine?: string
}

export type Config = {
  model: string
  effort: string
}

export type Recommendation = Config & {
  type: string
  samples: number
  cost: number
  currentCost?: number
  savingsPct?: number
  successRate: number
  /** Where a setting with no local evidence came from: the main machine, or the provider guide. */
  source?: 'learned' | 'guide'
}

/** The main machine's learned settings, shipped with the setup. Choices only, no records. */
export type Learned = {
  generated: string
  types: Record<string, Config & { samples: number; successRate: number }>
}

export type Settings = {
  minSamples: number
  minSavingsPct: number
  minSuccessRate: number
  window: number
  modelWeights: Record<string, number>
}

export const MAX_RECORDS = 1500
export const MAX_ISSUES = 8
export const DEFAULT_EFFORT = 'default'

const TYPE_RULES: ReadonlyArray<[string, RegExp]> = [
  ['debug', /\b(debug|fix|bug|error|fail(s|ing|ed)?|broken|crash|traceback|exception)\b/i],
  ['test', /\b(tests?|pytest|unit test|coverage|spec)\b/i],
  ['review', /\b(review|audit|critique|check over)\b/i],
  ['refactor', /\b(refactor|clean ?up|simplify|rename|restructure)\b/i],
  ['docs', /\b(readme|docs?|documentation|write[- ]?up|draft|summari[sz]e)\b/i],
  ['research', /\b(research|investigate|compare|find out|look up|feasib)/i],
  ['build', /\b(implement|add|build|create|make|scaffold|set ?up|install|write (a|the|an))\b/i],
  ['ops', /\b(deploy|release|publish|push|commit|merge|pr\b|pull request|ci)\b/i],
  ['question', /^(what|why|how|when|where|who|which|is|are|can|does|do|should)\b|\?\s*$/i],
]

export const TYPES: readonly string[] = [...TYPE_RULES.map(([t]) => t), 'other']

// `#debug` for a known type, `#type:anything` for a custom one; `#include` stays text.
const TAG = /^\s*#(type:)?([a-z][a-z0-9_-]{1,31})(?=\s|$)/i

/** Splits a leading type tag off the prompt; `undefined` when none. */
export function splitTag(text: string): { tag?: string; rest: string } {
  const m = TAG.exec(text)
  const tag = (m?.[2] ?? '').toLowerCase()
  if (!m || (!m[1] && !TYPES.includes(tag))) return { rest: text }
  return { tag, rest: text.slice(m[0].length).replace(/^\s+/, '') }
}

/** Keyword classifier: free, deterministic, and good enough to group jobs. */
export function classify(text: string): string {
  const head = text.slice(0, 600)
  for (const [type, rule] of TYPE_RULES) if (rule.test(head)) return type
  return 'other'
}

export function emptyUsage(): Usage {
  return { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }
}

export function addUsage(a: Usage, b: Usage): Usage {
  return {
    input: a.input + b.input,
    output: a.output + b.output,
    cacheRead: a.cacheRead + b.cacheRead,
    cacheWrite: a.cacheWrite + b.cacheWrite,
  }
}

export function fromApiUsage(u: {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
}): Usage {
  return {
    input: u.input_tokens,
    output: u.output_tokens,
    cacheRead: u.cache_read_input_tokens,
    cacheWrite: u.cache_creation_input_tokens,
  }
}

export function totalTokens(u: Usage): number {
  return u.input + u.output + u.cacheRead + u.cacheWrite
}

/**
 * Compute in input-token equivalents at one model's prices, using the API's
 * standard ratios: output 5x, cache write 1.25x, cache read 0.1x.
 */
export function weightedTokens(u: Usage): number {
  return u.input + 5 * u.output + 1.25 * u.cacheWrite + 0.1 * u.cacheRead
}

/** The model's relative price from `modelWeights`, matched by substring; 1 if unknown. */
export function modelWeight(model: string, weights: Record<string, number>): number {
  const id = model.toLowerCase()
  for (const [key, w] of Object.entries(weights)) if (id.includes(key.toLowerCase())) return w
  return 1
}

/** The figure the optimizer minimises: weighted tokens, each model at its own weight. */
export function cost(r: LedgerRecord, weights: Record<string, number>): number {
  const main = weightedTokens(r.usage) * modelWeight(r.model, weights)
  const by = r.subagents.byModel
  if (!by) return main + weightedTokens(r.subagents.usage) * modelWeight(r.model, weights)
  return Object.entries(by).reduce((sum, [model, u]) => sum + weightedTokens(u) * modelWeight(model, weights), main)
}

/** Parses `haiku=1,sonnet=3,opus=5` into weights; bad pairs are skipped. */
export function parseWeights(text: string): Record<string, number> {
  const out: Record<string, number> = {}
  for (const pair of text.split(',')) {
    const [k, v] = pair.split('=').map(s => s.trim())
    const n = Number(v)
    if (k && Number.isFinite(n) && n > 0) out[k] = n
  }
  return out
}

export function isSuccess(r: LedgerRecord): boolean {
  return r.outcome === 'answer' && r.rating !== 'bad' && !(r.suspect && r.rating !== 'good')
}

const COMPLAINT = /\b(wrong|incorrect|doesn'?t work|does not work|didn'?t work|still (fails|broken|wrong|not)|not what i|that'?s not|try again|you missed|broke|undo|revert)\b/i

/** A prompt that, sent soon after an answer, says the answer failed. */
export function isComplaint(text: string): boolean {
  return COMPLAINT.test(text.slice(0, 400))
}

export const SMALL_PROMPT = 400

/** Small vs large prompts, so cheap settings proven on small jobs are not pushed onto large ones. */
export function sizeBucket(promptChars: number): 'small' | 'large' {
  return promptChars < SMALL_PROMPT ? 'small' : 'large'
}

/** Linear-interpolated quantile, q in [0, 1]. */
export function quantile(xs: number[], q: number): number {
  if (xs.length === 0) return 0
  const s = [...xs].sort((a, b) => a - b)
  const at = (s.length - 1) * q
  const lo = s[Math.floor(at)] ?? 0
  const hi = s[Math.ceil(at)] ?? lo
  return lo + (hi - lo) * (at - Math.floor(at))
}

export function median(xs: number[]): number {
  return quantile(xs, 0.5)
}

export function configKey(c: Config): string {
  return `${c.model}|${c.effort}`
}

type Group = Config & { samples: number; successRate: number; cost: number; p75: number }

// Aborted turns are mostly steering, not failure: they are left out of the evidence.
function group(rs: LedgerRecord[], s: Settings): Group | undefined {
  const done = rs.filter(r => r.outcome !== 'aborted')
  const first = done[0]
  if (!first) return undefined
  const ok = done.filter(isSuccess)
  const successRate = ok.length / done.length
  const costs = ok.map(r => cost(r, s.modelWeights))
  return {
    model: first.model,
    effort: first.effort,
    samples: done.length,
    successRate,
    // Expected cost of getting the job done: failed runs have to be paid for again.
    cost: successRate > 0 ? median(costs) / successRate : Infinity,
    p75: quantile(costs, 0.75),
  }
}

function groups(records: LedgerRecord[], type: string, s: Settings): Group[] {
  const by = new Map<string, LedgerRecord[]>()
  for (const r of records.filter(r => r.type === type).slice(-s.window)) by.set(configKey(r), [...(by.get(configKey(r)) ?? []), r])
  return [...by.values()].map(rs => group(rs, s)).filter((g): g is Group => g !== undefined)
}

/**
 * The cheapest model/effort that has done this type of job reliably, when it beats
 * `current` by `minSavingsPct` after `switchPenalty` (the cost of re-caching the
 * context on another model); `undefined` while evidence is thin or not convincing.
 */
export function recommend(
  records: LedgerRecord[],
  type: string,
  current: Config | undefined,
  s: Settings,
  switchPenalty = 0,
  promptChars?: number,
): Recommendation | undefined {
  if (promptChars !== undefined) {
    // Use only same-size jobs once there is enough of them for a fair comparison.
    const bucket = sizeBucket(promptChars)
    const same = records.filter(r => r.type !== type || sizeBucket(r.promptChars) === bucket)
    if (same.filter(r => r.type === type).length >= 2 * s.minSamples) records = same
  }
  const eligible = groups(records, type, s).filter(g => g.samples >= s.minSamples && g.successRate >= s.minSuccessRate)
  if (eligible.length === 0) return undefined
  const best = eligible.reduce((a, b) => (b.cost < a.cost ? b : a))
  const rec: Recommendation = { type, model: best.model, effort: best.effort, samples: best.samples, cost: best.cost, successRate: best.successRate }
  if (!current) return rec
  if (configKey(best) === configKey(current)) return undefined
  // The current setting is judged on its own recent runs, inside the window or not.
  const mine = records.filter(r => r.type === type && configKey(r) === configKey(current)).slice(-s.window)
  const here = group(mine, s)
  if (!here || here.samples < s.minSamples) return rec
  const proposed = best.cost + (best.model === current.model ? 0 : switchPenalty)
  const savingsPct = Math.round((1 - proposed / here.cost) * 100)
  // Robustness: the proposal's typical bad run must still beat the current setting's typical run.
  const hereMedian = median(mine.filter(isSuccess).map(r => cost(r, s.modelWeights)))
  if (savingsPct < s.minSavingsPct || best.p75 >= hereMedian) return undefined
  return { ...rec, currentCost: here.cost, savingsPct }
}

export function k(n: number): string {
  return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${Math.round(n)}`
}

function top(counts: Record<string, number>, n: number): string {
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, n)
  return rows.length ? rows.map(([name, n]) => `${name}×${n}`).join(', ') : '-'
}

function merge(into: Record<string, number>, from: Record<string, number>): void {
  for (const [key, c] of Object.entries(from)) into[key] = (into[key] ?? 0) + c
}

/** Markdown report by task type: quantity (tokens, tools) and quality (issues). */
export function summary(records: LedgerRecord[], s: Settings): string {
  if (records.length === 0) return 'token-ledger: no turns recorded yet.'
  const types = [...new Set(records.map(r => r.type))].sort()
  const lines = [
    `**token-ledger** — ${records.length} turns recorded`,
    '',
    '| type | turns | success | median tokens | median cost | top tools | top commands | issues/turn |',
    '|---|---:|---:|---:|---:|---|---|---:|',
  ]
  for (const type of types) {
    const rs = records.filter(r => r.type === type)
    const tools: Record<string, number> = {}
    const commands: Record<string, number> = {}
    for (const r of rs) {
      merge(tools, r.tools)
      merge(commands, r.commands)
    }
    const issues = rs.reduce((n, r) => n + r.issues.length, 0)
    lines.push(
      `| ${type} | ${rs.length} | ${Math.round((rs.filter(isSuccess).length / rs.length) * 100)}% | ` +
        `${k(median(rs.map(r => totalTokens(addUsage(r.usage, r.subagents.usage)))))} | ` +
        `${k(median(rs.map(r => cost(r, s.modelWeights))))} | ${top(tools, 3)} | ${top(commands, 3)} | ` +
        `${(issues / rs.length).toFixed(1)} |`,
    )
  }
  const recs = types
    .map(t => recommend(records, t, undefined, s))
    .filter((r): r is Recommendation => r !== undefined)
  lines.push('', '**Cheapest reliable settings by type**')
  if (recs.length === 0) lines.push(`- not enough evidence yet (need ${s.minSamples}+ runs of one setting per type)`)
  for (const r of recs)
    lines.push(`- ${r.type}: ${r.model} / effort ${r.effort} — expected cost ${k(r.cost)}, n=${r.samples}, success ${Math.round(r.successRate * 100)}%`)
  const kinds: Record<string, number> = {}
  for (const r of records) for (const i of r.issues) kinds[`${i.kind}${i.tool ? `:${i.tool}` : ''}`] = (kinds[`${i.kind}${i.tool ? `:${i.tool}` : ''}`] ?? 0) + 1
  lines.push('', `**Most common issues:** ${top(kinds, 5)}`)
  lines.push('', '_cost = input + 5×output + 1.25×cache write + 0.1×cache read, × model weight; expected cost = median cost ÷ success rate_')
  return lines.join('\n')
}

/** Markdown breakdown of one turn. */
export function describe(r: LedgerRecord, s: Settings): string {
  const u = addUsage(r.usage, r.subagents.usage)
  const lines = [
    `**Last turn** — type \`${r.type}\` (${r.typeSource}), ${r.model} / effort ${r.effort}${r.applied ? ' (applied by token-ledger)' : ''}`,
    `- outcome: ${r.outcome}${r.rating ? `, rated ${r.rating}` : ''}; ${r.steps} model requests; ${(r.durationMs / 1000).toFixed(1)}s`,
    `- tokens: ${k(totalTokens(u))} total (in ${k(u.input)}, out ${k(u.output)}, cache read ${k(u.cacheRead)}, cache write ${k(u.cacheWrite)}); cost ${k(cost(r, s.modelWeights))}`,
    `- subagents: ${r.subagents.runs} runs, ${k(totalTokens(r.subagents.usage))} tokens`,
    `- tools: ${top(r.tools, 8)}`,
    `- commands: ${top(r.commands, 8)}`,
    `- issues: ${r.issues.length ? '' : 'none'}`,
  ]
  for (const i of r.issues) lines.push(`  - ${i.kind}${i.tool ? ` (${i.tool})` : ''}: ${i.detail}`)
  return lines.join('\n')
}

const SHELL_WORDS = new Set(['sudo', 'for', 'do', 'done', 'if', 'then', 'else', 'elif', 'fi', 'while', 'until', 'case', 'esac', 'time', 'exec'])

/** First word of each command in a shell line, skipping env assignments and sudo. */
export function commandNames(line: string): string[] {
  const names: string[] = []
  for (const part of line.split(/&&|\|\||;|\|/)) {
    if (/^\s*(for|case|select)\s/.test(part)) continue // a loop header names a variable, not a command
    const words = part.trim().split(/\s+/).filter(w => !/^[A-Z_][A-Z0-9_]*=/.test(w) && !SHELL_WORDS.has(w))
    const head = words[0]?.replace(/^.*[\\/]/, '')
    if (head && /^[\w.+-]+$/.test(head)) names.push(head)
  }
  return names
}

/** Masks values that look like credentials: `key=value` secrets, bearer tokens, long hex/base64 runs. */
export function redact(text: string): string {
  return text
    .replace(/\bBearer\s+[^\s"']+/gi, 'Bearer ***')
    .replace(/\b(\w*(?:key|token|secret|password|passwd|pwd|auth)\w*)\s*[=:]\s*(?!Bearer\b)[^\s"']+/gi, '$1=***')
    .replace(/\b[A-Za-z0-9+/_-]{32,}={0,2}/g, '***')
}

export function clip(text: string, n = 160): string {
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > n ? `${one.slice(0, n - 1)}…` : one
}

export function toJsonl(records: LedgerRecord[]): string {
  return records.map(r => JSON.stringify(r)).join('\n') + (records.length ? '\n' : '')
}

/** The newest records whose JSON fits in `maxBytes` (the store holds 4 MiB in all). */
export function capBytes(records: LedgerRecord[], maxBytes = 3_000_000): LedgerRecord[] {
  let size = 2
  let i = records.length
  while (i > 0) {
    const next = JSON.stringify(records[i - 1]).length + 1
    if (size + next > maxBytes) break
    size += next
    i -= 1
  }
  return records.slice(i)
}

/** A file-name-safe machine id. */
export function machineId(name: string | undefined): string {
  return (name ?? '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'machine'
}

/** Per task type, the cheapest reliable setting this machine's evidence supports. */
export function learnedDefaults(records: LedgerRecord[], s: Settings, today = new Date()): Learned {
  const types: Learned['types'] = {}
  for (const type of [...new Set(records.map(r => r.type))].sort()) {
    const r = recommend(records, type, undefined, s)
    if (r) types[type] = { model: r.model, effort: r.effort, samples: r.samples, successRate: Math.round(r.successRate * 100) / 100 }
  }
  return { generated: today.toISOString().slice(0, 10), types }
}

/** Reads a learned-settings file; anything malformed is ignored, not fatal. */
export function parseLearned(text: string): Learned | undefined {
  try {
    const raw = JSON.parse(text) as Partial<Learned>
    if (!raw || typeof raw.types !== 'object' || raw.types === null) return undefined
    const types: Learned['types'] = {}
    for (const [type, v] of Object.entries(raw.types)) {
      if (v && typeof v.model === 'string' && typeof v.effort === 'string') types[type] = { model: v.model, effort: v.effort, samples: Number(v.samples) || 0, successRate: Number(v.successRate) || 0 }
    }
    return { generated: String(raw.generated ?? ''), types }
  } catch {
    return undefined
  }
}
