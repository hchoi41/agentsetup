import type { EngineInterface, Register } from 'claude-code'

import {
  DEFAULT_EFFORT,
  MAX_ISSUES,
  MAX_RECORDS,
  addUsage,
  classify,
  clip,
  commandNames,
  describe,
  emptyUsage,
  fromApiUsage,
  k,
  parseWeights,
  recommend,
  splitTag,
  summary,
  toJsonl,
  totalTokens,
} from './ledger.ts'
import type { Config, Issue, LedgerRecord, Recommendation, Settings } from './ledger.ts'

const STORE_KEY = 'records'
const COMMAND = 'token-ledger'

/** Keeps the newest records in $.store and, when asked, mirrors them to a JSONL file. */
async function persist($: EngineInterface, records: LedgerRecord[], mirror: string): Promise<void> {
  await $.store.set(STORE_KEY, records)
  if (mirror) await $.fs.write(mirror, toJsonl(records))
}

type Live = {
  record: LedgerRecord
  rec?: Recommendation
  apply: boolean
  lastFailedBash?: string
}

export const register: Register = (on, options) => {
  const mode = String(options.mode ?? 'suggest')
  const settings: Settings = {
    minSamples: Number(options.minSamples ?? 3),
    minSavingsPct: Number(options.minSavingsPct ?? 15),
    minSuccessRate: 0.8,
    window: 50,
    modelWeights: parseWeights(String(options.modelWeights ?? '')),
  }
  const logPath = String(options.logPath ?? '')

  // Module state: rebuilt from $.store at every (re)load's session.start.
  let records: LedgerRecord[] = []
  let cwd = ''
  let current: Config | undefined
  let pendingTag: string | undefined
  let lastType = 'other'
  let mainTurn: string | undefined
  const live = new Map<string, Live>()

  const resolvePath = (p: string) => (/^([a-zA-Z]:)?[\\/]/.test(p) || p.startsWith('~') ? p : `${cwd}/${p}`)

  const issue = (l: Live, i: Issue) => {
    if (l.record.issues.length < MAX_ISSUES) l.record.issues.push(i)
  }

  on('session.start', async ($, e, next) => {
    const stored = await $.store.get(STORE_KEY)
    records = Array.isArray(stored) ? (stored as LedgerRecord[]) : []
    cwd = e.cwd
    try {
      await $.command.register({
        name: COMMAND,
        description: 'Token use by task type, issues, and the cheapest reliable model/effort',
        argumentHint: '[report|last|recommend [type]|rate good|bad|export [path]|reset confirm]',
      })
    } catch {
      $.ui.status('ledger: recording (the /token-ledger command could not be registered)')
    }
    return next(e)
  })

  // A leading `#type` tag names the task type; it is removed before the model reads it.
  on('prompt.submit', ($, e, next) => {
    if (e.command || e.isMeta) return next(e)
    const { tag, rest } = splitTag(e.text)
    if (!tag || rest === '') return next(e)
    pendingTag = tag
    return next({ ...e, text: rest })
  }).catch(($, e, next) => next(e))

  on('turn.start', async ($, e, next) => {
    if (e.agentId !== undefined) return next(e)
    const tagged = pendingTag
    pendingTag = undefined
    const type = tagged ?? (e.text.trim() === '' ? lastType : classify(e.text))
    lastType = type
    const record: LedgerRecord = {
      v: 1,
      id: e.turnId,
      at: await $.clock.now(),
      cwd,
      type,
      typeSource: tagged ? 'tag' : 'auto',
      promptChars: e.text.length,
      model: current?.model ?? 'unknown',
      effort: current?.effort ?? DEFAULT_EFFORT,
      applied: false,
      steps: 0,
      durationMs: 0,
      outcome: 'answer',
      usage: emptyUsage(),
      subagents: { runs: 0, usage: emptyUsage() },
      tools: {},
      commands: {},
      issues: [],
    }
    const rec = mode === 'record' ? undefined : recommend(records, type, current, settings)
    live.set(e.turnId, { record, rec, apply: mode === 'auto' && rec !== undefined })
    mainTurn = e.turnId
    if (rec) {
      const saving = rec.savingsPct === undefined ? '' : ` (−${rec.savingsPct}% est.)`
      const verb = mode === 'auto' ? 'using' : 'try'
      $.ui.status(`ledger: ${type} → ${verb} ${rec.model} / ${rec.effort}${saving}`)
      if (mode !== 'auto' && rec.savingsPct !== undefined)
        $.ui.toast(`token-ledger: past ${type} jobs ran cheapest on ${rec.model} / effort ${rec.effort}${saving}, n=${rec.samples}`)
    } else {
      $.ui.status(`ledger: ${type}`)
    }
    return next(e)
  })

  on('turn.step', async function* ($, e, next) {
    const l = e.agentId === undefined ? live.get(e.turnId) : undefined
    if (!l) return yield* next(e)
    const effort = e.effort === undefined ? DEFAULT_EFFORT : String(e.effort)
    current = { model: e.model, effort }
    let step = e
    if (l.apply && l.rec) {
      step = { ...e, model: l.rec.model }
      if (l.rec.effort !== DEFAULT_EFFORT) {
        const n = Number(l.rec.effort)
        step = { ...step, effort: Number.isFinite(n) ? n : (l.rec.effort as 'low' | 'medium' | 'high' | 'xhigh' | 'max') }
      }
      l.record.applied = true
    }
    l.record.steps += 1
    l.record.model = step.model
    l.record.effort = step.effort === undefined ? DEFAULT_EFFORT : String(step.effort)
    return yield* next(step)
  })

  on('tool.call', async ($, e, next) => {
    const l = mainTurn === undefined ? undefined : live.get(mainTurn)
    if (!l) return next(e)
    const tool = String(e.tool)
    l.record.tools[tool] = (l.record.tools[tool] ?? 0) + 1
    const shell = tool === 'Bash' || tool === 'PowerShell' ? String((e as { command?: unknown }).command ?? '') : ''
    for (const name of commandNames(shell)) l.record.commands[name] = (l.record.commands[name] ?? 0) + 1
    if (shell && l.lastFailedBash === shell) issue(l, { kind: 'retry', tool, detail: clip(shell) })

    const ran = await next(e)
    if (ran.deny !== undefined) {
      issue(l, { kind: 'denied', tool, detail: clip(ran.deny) })
    } else if (ran.isError === true) {
      issue(l, { kind: 'tool-error', tool, detail: clip(`${shell ? `${clip(shell, 60)} → ` : ''}${ran.text ?? ''}`) })
      if (shell) l.lastFailedBash = shell
    } else if (shell) {
      l.lastFailedBash = undefined
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined) {
      const l = mainTurn === undefined ? undefined : live.get(mainTurn)
      if (l) {
        l.record.subagents.runs += 1
        if (e.usage) l.record.subagents.usage = addUsage(l.record.subagents.usage, fromApiUsage(e.usage))
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
      if (r.model === 'unknown') r.model = e.usage.model
    }
    if (e.reason !== 'answer') issue(l, { kind: 'turn-end', detail: e.reason })
    records = [...records, r].slice(-MAX_RECORDS)
    await persist($, records, logPath && resolvePath(logPath))
    const total = totalTokens(addUsage(r.usage, r.subagents.usage))
    $.ui.status(`ledger: ${r.type} · ${k(total)} tok · ${r.issues.length} issue${r.issues.length === 1 ? '' : 's'}`)
    return result
  })

  on('command.run', { command: COMMAND }, async ($, e) => {
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
          return `- ${t}: ${r.model} / effort ${r.effort} — median cost ${k(r.medianCost)}, n=${r.samples}${saving}`
        })
        const now = current ? `${current.model} / effort ${current.effort}` : 'not seen yet'
        return { text: [`**Recommendations** (current: ${now}; mode: ${mode})`, ...lines].join('\n') }
      }
      case 'rate': {
        if (!last) return { text: 'token-ledger: no turn to rate yet.' }
        if (arg !== 'good' && arg !== 'bad') return { text: 'Usage: /token-ledger rate good|bad' }
        // The rating command runs after the turn it rates, so the latest record is that turn.
        last.rating = arg
        await persist($, records, logPath && resolvePath(logPath))
        return { text: `token-ledger: rated the last \`${last.type}\` turn ${arg}.` }
      }
      case 'export': {
        const path = resolvePath(arg || 'token-ledger.jsonl')
        await $.fs.write(path, toJsonl(records))
        return { text: `token-ledger: wrote ${records.length} records to ${path}` }
      }
      case 'reset': {
        if (arg !== 'confirm') return { text: `token-ledger: this deletes ${records.length} records. Run \`/token-ledger reset confirm\` to proceed.` }
        records = []
        await persist($, records, logPath && resolvePath(logPath))
        return { text: 'token-ledger: all records deleted.' }
      }
      default:
        return {
          text: [
            'Usage: /token-ledger [report|last|recommend [type]|rate good|bad|export [path]|reset confirm]',
            'Tag a prompt with a leading `#type` (e.g. `#debug fix the login test`) to set its task type.',
          ].join('\n'),
        }
    }
  })
}
