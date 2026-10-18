import type { EngineInterface, Register } from 'claude-code'

import { CLAUDE_DEFAULTS } from './defaults.ts'

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
  isComplaint,
  evidenceFor,
  k,
  machineId,
  mergeById,
  modelWeight,
  parseJsonl,
  parseWeights,
  recommend,
  redact,
  splitTag,
  summary,
  toJsonl,
  toSyncRecord,
  totalTokens,
} from './ledger.ts'
import type { Config, Issue, LedgerRecord, Recommendation, Settings } from './ledger.ts'

const STORE_KEY = 'records'
const NAMED_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
type NamedEffort = (typeof NAMED_EFFORTS)[number]
// In auto mode every Nth turn of a type keeps your own setting, so it keeps being measured.
const EXPLORE_EVERY = 5
// A complaint sent within this long after an answer marks that answer as a likely failure.
const FOLLOW_UP_MS = 10 * 60 * 1000
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

// Shared folder (e.g. in the OneDrive hub): <shareDir>/<machine>/<session>.jsonl.
// The mod only writes files; it never runs git or reaches the network.
const SHARE_EVERY_TURNS = 5
const SHARE_MAX_FILE = 1_000_000
const SHARE_MAX_AGE_MS = 180 * 24 * 60 * 60 * 1000

/** Reads every machine's session files from the shared folder; skips large, old or unreadable ones. */
async function readShared($: EngineInterface, dir: string): Promise<LedgerRecord[]> {
  const now = Date.now()
  const all: LedgerRecord[][] = []
  for (const m of await $.fs.list(dir).catch(() => [])) {
    if (m.kind !== 'dir') continue
    for (const f of await $.fs.list(`${dir}/${m.name}`).catch(() => [])) {
      if (f.kind !== 'file' || !f.name.endsWith('.jsonl') || f.size > SHARE_MAX_FILE || now - f.mtimeMs > SHARE_MAX_AGE_MS) continue
      all.push(parseJsonl(await $.fs.read(`${dir}/${m.name}/${f.name}`).catch(() => '')))
    }
  }
  return mergeById(...all)
}

/** Writes this session's records, trimmed for sharing, to its own file in the shared folder. */
async function writeShared($: EngineInterface, file: string, mine: LedgerRecord[]): Promise<string | undefined> {
  try {
    await $.fs.write(file, toJsonl(capBytes(mine.map(toSyncRecord), SHARE_MAX_FILE)))
    return undefined
  } catch (err) {
    const why = clip(String(err), 80)
    $.ui.status(`ledger: not shared: ${why}`)
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
    minSamples: Math.max(1, Number(options.minSamples) || 8),
    minSavingsPct: Math.max(0, Number(options.minSavingsPct) || 15),
    minSuccessRate: 0.8,
    window: 50,
    modelWeights: parseWeights(String(options.modelWeights ?? '')),
  }
  const logPath = String(options.logPath ?? '')
  const shareDir = String(options.shareDir ?? '').replace(/[\\/]+$/, '')

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
  let shared: LedgerRecord[] = []
  let machine = 'machine'
  let shareFile = ''
  let unshared = 0
  let home = ''
  // This session's own records: what its shared file holds.
  const mine = () => records.filter(r => r.machine === machine && sessionTurns.has(r.id))
  const sessionTurns = new Set<string>()
  // Local evidence first; every machine's once local is thin (local wins on the same id).
  const pooled = () => (shared.length ? mergeById(shared, records) : records)
  const evidence = (type: string) => evidenceFor(type, records, pooled(), settings.minSamples)
  // Subagent id -> the main turn whose Agent call started it.
  const agentOwner = new Map<string, string>()

  const keep = (r: LedgerRecord[]) => capBytes(r.slice(-MAX_RECORDS))
  // Until a type has minSamples finished runs, start from the provider guide's setting.
  const starting = (type: string, cur: Config): Recommendation | undefined => {
    const d = CLAUDE_DEFAULTS[type]
    const runs = pooled().filter(r => r.type === type && r.outcome !== 'aborted').length
    if (!d || runs >= settings.minSamples || configKey(d) === configKey(cur)) return undefined
    return { type, model: d.model, effort: d.effort, samples: 0, cost: 0, successRate: 0 }
  }
  const issue = (l: Live, i: Issue) => {
    if (l.record.issues.length < MAX_ISSUES) l.record.issues.push(i)
  }

  on('session.start', async ($, e, next) => {
    const stored = await $.store.get(STORE_KEY)
    records = Array.isArray(stored) ? (stored as LedgerRecord[]) : []
    cwd = e.cwd
    machine = machineId((await $.env.get('COMPUTERNAME')) ?? (await $.env.get('HOSTNAME')))
    home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
    if (shareDir) {
      shareFile = `${shareDir}/${machine}/${await $.session.id()}.jsonl`
      shared = await readShared($, shareDir)
    }
    try {
      await $.command.register({
        name: 'token-ledger',
        description: 'Token use by task type, issues, and the cheapest reliable model/effort',
        argumentHint: '[report|last|recommend [type]|rate good|bad|share|export [path]|reset confirm]',
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
    const prev = records[records.length - 1]
    if (prev && !prev.rating && Date.now() - (prev.at + prev.durationMs) < FOLLOW_UP_MS && isComplaint(e.text)) prev.suspect = true
    const type = tag ?? (e.text.trim() === '' ? lastType : classify(e.text))
    lastType = type
    const record: LedgerRecord = {
      v: 1,
      id: e.turnId,
      at: Date.now(),
      cwd,
      machine,
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
    sessionTurns.add(e.turnId)
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
        const rec = recommend(evidence(type), type, current, settings, penalty, l.record.promptChars) ?? starting(type, current)
        if (rec && !suspended.has(configKey(rec))) {
          l.rec = rec
          l.apply = mode === 'auto' && n % EXPLORE_EVERY !== 0
          const saving = rec.savingsPct === undefined ? '' : ` (−${rec.savingsPct}% est.)`
          const from = rec.samples === 0 ? ' (provider guide)' : ''
          $.ui.status(`ledger: ${type} → ${l.apply ? 'using' : 'try'} ${rec.model} / ${rec.effort}${saving}${from}`)
          if (!l.apply && rec.savingsPct !== undefined && !toasted.has(type)) {
            toasted.add(type)
            $.ui.toast(`token-ledger: past ${type} jobs ran cheapest on ${rec.model} / effort ${rec.effort}${saving}, n=${rec.samples}`)
          } else if (!l.apply && rec.samples === 0 && !toasted.has(type)) {
            toasted.add(type)
            $.ui.toast(`token-ledger: no evidence for ${type} yet; the provider guide suggests ${rec.model} / effort ${rec.effort}`)
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
    if (tool === 'Agent' && ran.deny === undefined) {
      const agentId = (ran.result as { agentId?: unknown } | undefined)?.agentId
      const owner = sub ? agentOwner.get(String(e.agentId)) : mainTurn
      if (typeof agentId === 'string' && owner !== undefined) agentOwner.set(agentId, owner)
    }
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
      // Counted against the main turn whose Agent call started it, even after that turn
      // ended. Loops no Agent call started (the engine's own forks) are not counted.
      const owner = agentOwner.get(e.agentId)
      const l = owner === undefined ? undefined : live.get(owner)
      const target = l?.record ?? records.find(r => r.id === owner)
      if (target) {
        const sub = target.subagents
        sub.runs += 1
        if (e.usage) {
          const u = fromApiUsage(e.usage)
          sub.usage = addUsage(sub.usage, u)
          const by = (sub.byModel ??= {})
          by[e.usage.model] = addUsage(by[e.usage.model] ?? emptyUsage(), u)
        }
        if (!l) await persist($, records, logPath)
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
    if (shareFile && ++unshared >= SHARE_EVERY_TURNS) {
      unshared = 0
      await writeShared($, shareFile, mine())
    }
    if (!failed) {
      const total = totalTokens(addUsage(r.usage, r.subagents.usage))
      $.ui.status(`ledger: ${r.type} · ${k(total)} tok · ${r.issues.length} issue${r.issues.length === 1 ? '' : 's'}`)
    }
    return result
  })

  // A fast local write; nothing here waits on the network.
  on('session.end', async ($, e, next) => {
    if (shareFile && unshared > 0) await writeShared($, shareFile, mine())
    return next(e)
  })

  on('command.run', { command: 'token-ledger' }, async ($, e) => {
    const [verb = 'report', ...rest] = e.args.trim().split(/\s+/).filter(Boolean)
    const arg = rest.join(' ')
    const last = records[records.length - 1]
    switch (verb) {
      case 'report':
        return { text: summary(records, settings) }
      case 'share': {
        if (!shareFile) return { text: 'token-ledger: no shared folder set (option shareDir).' }
        unshared = 0
        const failed = await writeShared($, shareFile, mine())
        shared = await readShared($, shareDir)
        const machines = new Set(shared.map(r => r.machine ?? '?')).size
        return { text: failed ? `token-ledger: not shared: ${failed}` : `token-ledger: shared this session; ${shared.length} records from ${machines} machine(s) in ${shareDir}.` }
      }
      case 'last':
        return { text: last ? describe(last, settings) : 'token-ledger: no turns recorded yet.' }
      case 'recommend': {
        const types = arg ? [arg] : [...new Set(records.map(r => r.type))].sort()
        const lines = types.map(t => {
          const r = recommend(evidence(t), t, current, settings)
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
        if (!failed && shareFile && sessionTurns.has(last.id)) await writeShared($, shareFile, mine())
        return { text: failed ? `token-ledger: rating not saved: ${failed}` : `token-ledger: rated the last \`${last.type}\` turn ${arg}.` }
      }
      case 'export': {
        // Default under the user's .claude folder, never inside a project that may be public.
        const path = arg || (home ? `${home}/.claude/token-ledger.jsonl` : 'token-ledger.jsonl')
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
            'Usage: /token-ledger [report|last|recommend [type]|rate good|bad|share|export [path]|reset confirm]',
            'Tag a prompt with a leading type (e.g. `#debug fix the login test`, or `#type:migration ...`).',
          ].join('\n'),
        }
    }
  }).catch(() => ({ text: 'token-ledger: the command failed (see `claude --debug`).' }))
}
