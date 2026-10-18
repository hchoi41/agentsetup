import type { EngineInterface, Register } from 'claude-code'

import {
  DEFAULT_EFFORT,
  MAX_ISSUES,
  MAX_RECORDS,
  addUsage,
  capBytes,
  classify,
  clip,
  commandNames,
  configKey,
  describe,
  emptyUsage,
  fromApiUsage,
  k,
  modelWeight,
  parseWeights,
  recommend,
  redact,
  splitTag,
  summary,
  toJsonl,
  totalTokens,
} from './ledger.ts'
import type { Config, Issue, LedgerRecord, Recommendation, Settings } from './ledger.ts'

const STORE_KEY = 'records'
const NAMED_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
type NamedEffort = (typeof NAMED_EFFORTS)[number]
// In auto mode every Nth turn of a type keeps your own setting, so it keeps being measured.
const EXPLORE_EVERY = 5
const PERMISSION_DENIAL = /permission|denied|rejected|declined|doesn't want|does not want/i

/** Keeps the newest records in $.store and, when asked, mirrors them to a JSONL file. */
async function persist($: EngineInterface, records: LedgerRecord[], mirror: string): Promise<string | undefined> {
  try {
    await $.store.set(STORE_KEY, records)
    if (mirror) await $.fs.write(mirror, toJsonl(records))
    return undefined
  } catch (err) {
    const why = clip(String(err), 80)
    $.ui.status(`ledger: not saved: ${why}`)
    return why
  }
}

type Live = {
  record: LedgerRecord
  rec?: Recommendation
  apply: boolean
  decided: boolean
  lastFailedShell?: string
}

export const register: Register = (on, options) => {
  const mode = String(options.mode ?? 'suggest')
  const settings: Settings = {
    minSamples: Math.max(1, Number(options.minSamples) || 5),
    minSavingsPct: Math.max(0, Number(options.minSavingsPct) || 15),
    minSuccessRate: 0.8,
    window: 50,
    modelWeights: parseWeights(String(options.modelWeights ?? '')),
  }
  const logPath = String(options.logPath ?? '')

  // Module state: rebuilt from $.store at every (re)load's session.start.
  let records: LedgerRecord[] = []
  let cwd = ''
  let current: Config | undefined
  let pending: { tag: string; rest: string } | undefined
  let lastType = 'other'
  let lastCacheRead = 0
  let mainTurn: string | undefined
  const live = new Map<string, Live>()
  const suspended = new Set<string>()
  const toasted = new Set<string>()
  const turnsByType = new Map<string, number>()

  const keep = (r: LedgerRecord[]) => capBytes(r.slice(-MAX_RECORDS))
  const issue = (l: Live, i: Issue) => {
    if (l.record.issues.length < MAX_ISSUES) l.record.issues.push(i)
  }

  on('session.start', async ($, e, next) => {
    const stored = await $.store.get(STORE_KEY)
    records = Array.isArray(stored) ? (stored as LedgerRecord[]) : []
    cwd = e.cwd
    try {
      await $.command.register({
        name: 'token-ledger',
        description: 'Token use by task type, issues, and the cheapest reliable model/effort',
        argumentHint: '[report|last|recommend [type]|rate good|bad|export [path]|reset confirm]',
        immediate: true,
      })
    } catch {
      $.ui.status('ledger: recording (the /token-ledger command could not be registered)')
    }
    return next(e)
  })

  // A leading type tag (`#debug`, `#type:custom`) names the task type; it is removed
  // before the model reads it, and kept only once the prompt is really sent on its own.
  on('prompt.submit', async ($, e, next) => {
    if (e.command || e.isMeta || e.turnId !== undefined) return next(e)
    const { tag, rest } = splitTag(e.text)
    if (!tag || rest === '') return next(e)
    const sent = await next({ ...e, text: rest })
    if (sent.drop === undefined) pending = { tag, rest }
    return sent
  }).catch(($, e, next) => next(e))

  on('turn.start', ($, e, next) => {
    if (e.agentId !== undefined) return next(e)
    const tag = pending && e.text === pending.rest ? pending.tag : undefined
    pending = undefined
    const type = tag ?? (e.text.trim() === '' ? lastType : classify(e.text))
    lastType = type
    const record: LedgerRecord = {
      v: 1,
      id: e.turnId,
      at: Date.now(),
      cwd,
      type,
      typeSource: tag ? 'tag' : 'auto',
      promptChars: e.text.length,
      model: 'unknown',
      effort: DEFAULT_EFFORT,
      applied: false,
      steps: 0,
      durationMs: 0,
      outcome: 'answer',
      usage: emptyUsage(),
      subagents: { runs: 0, usage: emptyUsage(), byModel: {} },
      tools: {},
      commands: {},
      issues: [],
    }
    live.set(e.turnId, { record, apply: false, decided: false })
    mainTurn = e.turnId
    $.ui.status(`ledger: ${type}`)
    return next(e)
  })

  on('turn.step', async function* ($, e, next) {
    const l = e.agentId === undefined ? live.get(e.turnId) : undefined
    if (!l) return yield* next(e)
    const type = l.record.type
    if (!l.decided) {
      // The first request names the session's own model and effort: the baseline.
      l.decided = true
      current = { model: e.model, effort: e.effort === undefined ? DEFAULT_EFFORT : String(e.effort) }
      if (mode !== 'record') {
        const n = (turnsByType.get(type) ?? 0) + 1
        turnsByType.set(type, n)
        // Moving to another model re-caches the context: ~1.15x the tokens last read from cache.
        const penalty = 1.15 * lastCacheRead * modelWeight(current.model, settings.modelWeights)
        const rec = recommend(records, type, current, settings, penalty)
        if (rec && !suspended.has(configKey(rec))) {
          l.rec = rec
          l.apply = mode === 'auto' && n % EXPLORE_EVERY !== 0
          const saving = rec.savingsPct === undefined ? '' : ` (−${rec.savingsPct}% est.)`
          $.ui.status(`ledger: ${type} → ${l.apply ? 'using' : 'try'} ${rec.model} / ${rec.effort}${saving}`)
          if (!l.apply && rec.savingsPct !== undefined && !toasted.has(type)) {
            toasted.add(type)
            $.ui.toast(`token-ledger: past ${type} jobs ran cheapest on ${rec.model} / effort ${rec.effort}${saving}, n=${rec.samples}`)
          }
        }
      }
    }
    let step = e
    if (l.apply && l.rec) {
      step = { ...e, model: l.rec.model }
      const effort = NAMED_EFFORTS.find(x => x === l.rec?.effort)
      if (effort) step = { ...step, effort: effort as NamedEffort }
    }
    const result = yield* next(step)
    // Reached only when the engine took the request as rewritten.
    l.record.steps += 1
    l.record.model = step.model
    l.record.effort = step.effort === undefined ? DEFAULT_EFFORT : String(step.effort)
    l.record.applied ||= step !== e
    return result
  })

  on('tool.call', async ($, e, next) => {
    const l = mainTurn === undefined ? undefined : live.get(mainTurn)
    if (!l) return next(e)
    const tool = String(e.tool)
    const sub = e.agentId !== undefined
    l.record.tools[tool] = (l.record.tools[tool] ?? 0) + 1
    const shell = tool === 'Bash' || tool === 'PowerShell' ? String((e as { command?: unknown }).command ?? '') : ''
    const names = commandNames(shell)
    for (const name of names) l.record.commands[name] = (l.record.commands[name] ?? 0) + 1
    const what = `${sub ? '[subagent] ' : ''}${names.join(' ')}`.trim()
    if (shell && !sub && l.lastFailedShell === shell) issue(l, { kind: 'retry', tool, detail: what })

    const ran = await next(e)
    const text = redact(ran.deny ?? ran.text ?? '')
    if (ran.deny !== undefined || (ran.isError === true && PERMISSION_DENIAL.test(text))) {
      issue(l, { kind: 'denied', tool, detail: clip(`${what ? `${what} → ` : ''}${text}`) })
    } else if (ran.isError === true) {
      issue(l, { kind: 'tool-error', tool, detail: clip(`${what ? `${what} → ` : ''}${text}`) })
      if (shell && !sub) l.lastFailedShell = shell
    } else if (shell && !sub) {
      l.lastFailedShell = undefined
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined) {
      // Counted against the main turn running when the subagent's turn ends.
      const l = mainTurn === undefined ? undefined : live.get(mainTurn)
      if (l) {
        const sub = l.record.subagents
        sub.runs += 1
        if (e.usage) {
          const u = fromApiUsage(e.usage)
          sub.usage = addUsage(sub.usage, u)
          const by = (sub.byModel ??= {})
          by[e.usage.model] = addUsage(by[e.usage.model] ?? emptyUsage(), u)
        }
      }
      return result
    }
    const l = live.get(e.turnId)
    if (!l) return result
    live.delete(e.turnId)
    if (mainTurn === e.turnId) mainTurn = undefined
    const r = l.record
    r.outcome = e.reason
    r.durationMs = e.durationMs
    if (e.usage) {
      r.usage = fromApiUsage(e.usage)
      lastCacheRead = r.usage.cacheRead
      if (r.model === 'unknown') r.model = e.usage.model
    }
    if (e.reason !== 'answer') issue(l, { kind: 'turn-end', detail: e.reason })
    // Circuit breaker: an applied setting that errors is not applied again this session.
    if (r.applied && l.rec && (e.reason === 'error' || e.reason === 'refusal')) suspended.add(configKey(l.rec))
    records = keep([...records, r])
    const failed = await persist($, records, logPath)
    if (!failed) {
      const total = totalTokens(addUsage(r.usage, r.subagents.usage))
      $.ui.status(`ledger: ${r.type} · ${k(total)} tok · ${r.issues.length} issue${r.issues.length === 1 ? '' : 's'}`)
    }
    return result
  })

  on('command.run', { command: 'token-ledger' }, async ($, e) => {
    const [verb = 'report', ...rest] = e.args.trim().split(/\s+/).filter(Boolean)
    const arg = rest.join(' ')
    const last = records[records.length - 1]
    switch (verb) {
      case 'report':
        return { text: summary(records, settings) }
      case 'last':
        return { text: last ? describe(last, settings) : 'token-ledger: no turns recorded yet.' }
      case 'recommend': {
        const types = arg ? [arg] : [...new Set(records.map(r => r.type))].sort()
        const lines = types.map(t => {
          const r = recommend(records, t, current, settings)
          if (!r) return `- ${t}: keep current settings (no cheaper reliable setting with ${settings.minSamples}+ runs)`
          const saving = r.savingsPct === undefined ? '' : `, ~${r.savingsPct}% cheaper than now`
          return `- ${t}: ${r.model} / effort ${r.effort} — expected cost ${k(r.cost)}, n=${r.samples}${saving}`
        })
        const now = current ? `${current.model} / effort ${current.effort}` : 'not seen yet'
        return { text: [`**Recommendations** (current: ${now}; mode: ${mode})`, ...lines].join('\n') }
      }
      case 'rate': {
        // Runs at once, even mid-turn, so it rates the last finished turn.
        if (!last) return { text: 'token-ledger: no turn to rate yet.' }
        if (arg !== 'good' && arg !== 'bad') return { text: 'Usage: /token-ledger rate good|bad' }
        last.rating = arg
        const failed = await persist($, records, logPath)
        return { text: failed ? `token-ledger: rating not saved: ${failed}` : `token-ledger: rated the last \`${last.type}\` turn ${arg}.` }
      }
      case 'export': {
        const path = arg || 'token-ledger.jsonl'
        await $.fs.write(path, toJsonl(records))
        return { text: `token-ledger: wrote ${records.length} records to ${path}` }
      }
      case 'reset': {
        if (arg !== 'confirm') return { text: `token-ledger: this deletes ${records.length} records. Run \`/token-ledger reset confirm\` to proceed.` }
        records = []
        const failed = await persist($, records, logPath)
        return { text: failed ? `token-ledger: reset not saved: ${failed}` : 'token-ledger: all records deleted.' }
      }
      default:
        return {
          text: [
            'Usage: /token-ledger [report|last|recommend [type]|rate good|bad|export [path]|reset confirm]',
            'Tag a prompt with a leading type (e.g. `#debug fix the login test`, or `#type:migration ...`).',
          ].join('\n'),
        }
    }
  }).catch(() => ({ text: 'token-ledger: the command failed (see `claude --debug`).' }))
}
