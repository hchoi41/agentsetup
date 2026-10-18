import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'

import { capBytes, classify, commandNames, cost, learnedDefaults, lessons, parseLearned, parseLessons, recommend, redact, splitTag } from '../hooks/ledger.ts'
import type { LedgerRecord, Settings } from '../hooks/ledger.ts'

const SETTINGS: Settings = { minSamples: 3, minSavingsPct: 15, minSuccessRate: 0.8, window: 50, modelWeights: {} }

function rec(type: string, model: string, effort: string, output: number, outcome: LedgerRecord['outcome'] = 'answer'): LedgerRecord {
  return {
    v: 1, id: `${model}-${output}-${Math.random()}`, at: 0, cwd: '/w', type, typeSource: 'auto', promptChars: 10,
    model, effort, applied: false, steps: 1, durationMs: 1, outcome,
    usage: { input: 1000, output, cacheRead: 0, cacheWrite: 0 },
    subagents: { runs: 0, usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } },
    tools: {}, commands: {}, issues: [],
  }
}

const HISTORY = [
  rec('debug', 'claude-opus', 'high', 9000),
  rec('debug', 'claude-opus', 'high', 8000),
  rec('debug', 'claude-opus', 'high', 10000),
  rec('debug', 'claude-sonnet', 'medium', 2000),
  rec('debug', 'claude-sonnet', 'medium', 2500),
  rec('debug', 'claude-sonnet', 'medium', 3000),
]

// The engine beneath the plugin: each event answers as core would, minimally.
function bottom(on: On, seen: { models: string[] }) {
  mock.clock(on, { now: 1_700_000_000_000 })
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('prompt.submit', ($, e) => ({ text: e.text }))
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('turn.step', async function* ($, e) {
    seen.models.push(`${e.model}/${String(e.effort)}`)
    return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn' as const, usage: null }
  })
  on('tool.call', ($, e) =>
    (String(e.tool) === 'Agent'
      ? { result: { agentId: 'sub-1' }, text: 'launched' }
      : { result: '', text: 'exit 1: command not found', isError: true }) as never)
  on('turn.complete', ($, e) => ({ text: e.answer, usage: e.usage }))
}

// A streamed event runs only as it is read: drain it, then take its result.
async function step($: Engine, e: Parameters<Engine['turn']['step']>[0]) {
  const stream = $.turn.step(e)
  for await (const _ of stream);
  return stream.result
}

const RUN = { origin: { kind: 'composer' as const }, presentation: { isFullscreen: false, columns: 120 } }

const USAGE = { input_tokens: 100, output_tokens: 50, cache_read_input_tokens: 1000, cache_creation_input_tokens: 10, model: 'claude-sonnet' }

describe('ledger logic', () => {
  test('classifies prompts and reads tags', () => {
    expect(classify('fix the failing login test')).toBe('debug')
    expect(classify('implement a CSV export')).toBe('build')
    expect(classify('what does this function return?')).toBe('question')
    expect(splitTag('#research compare two libs')).toEqual({ tag: 'research', rest: 'compare two libs' })
    expect(splitTag('# Heading').tag).toBeUndefined()
    expect(commandNames('FOO=1 npm test && git status | head -5')).toEqual(['npm', 'git', 'head'])
    expect(commandNames('for f in *; do python $f; done')).toEqual(['python'])
    expect(splitTag('#include <stdio.h>').tag).toBeUndefined()
    expect(splitTag('#type:migration move the db').tag).toBe('migration')
    expect(redact('curl -H "Authorization: Bearer abc.def" API_KEY=xyz')).toBe('curl -H "Authorization: Bearer ***" API_KEY=***')
  })

  test('recommends the cheapest reliable setting once evidence is enough', () => {
    const r = recommend(HISTORY, 'debug', { model: 'claude-opus', effort: 'high' }, SETTINGS)
    expect(r?.model).toBe('claude-sonnet')
    expect(r?.effort).toBe('medium')
    expect(r?.savingsPct).toBeGreaterThan(50)
    expect(recommend(HISTORY.slice(0, 5), 'debug', { model: 'claude-opus', effort: 'high' }, SETTINGS)).toBeUndefined()
    expect(recommend(HISTORY, 'debug', { model: 'claude-sonnet', effort: 'medium' }, SETTINGS)).toBeUndefined()
  })

  test('does not recommend a setting that fails too often', () => {
    const flaky = [...HISTORY, rec('debug', 'claude-sonnet', 'medium', 1, 'error'), rec('debug', 'claude-sonnet', 'medium', 1, 'error')]
    expect(recommend(flaky, 'debug', { model: 'claude-opus', effort: 'high' }, SETTINGS)).toBeUndefined()
  })

  test('prices subagent tokens at their own model weight', () => {
    const r = rec('build', 'claude-opus', 'high', 0)
    r.usage = { input: 1000, output: 0, cacheRead: 0, cacheWrite: 0 }
    r.subagents = { runs: 1, usage: { input: 1000, output: 0, cacheRead: 0, cacheWrite: 0 }, byModel: { 'claude-haiku': { input: 1000, output: 0, cacheRead: 0, cacheWrite: 0 } } }
    expect(cost(r, { opus: 5, haiku: 1 })).toBe(6000)
  })

  test('keeps the store under its byte cap, newest first', () => {
    const many = Array.from({ length: 50 }, (_, i) => rec('build', 'm', 'e', i))
    const kept = capBytes(many, 3000)
    expect(kept.length).toBeLessThan(50)
    expect(kept[kept.length - 1]).toBe(many[49])
  })
})

describe('learned settings and evidence', () => {
  test('learned settings carry only the choice per type', () => {
    const out = learnedDefaults(HISTORY, SETTINGS, new Date('2026-10-18'))
    expect(out).toEqual({ generated: '2026-10-18', types: { debug: { model: 'claude-sonnet', effort: 'medium', samples: 3, successRate: 1 } } })
    expect(JSON.stringify(out)).not.toContain('/w')
  })

  test('a lessons note is short, readable, safe and reads back in', () => {
    const r = rec('debug', 'claude-opus', 'high', 9000)
    r.cwd = 'C:/Users/max/secret'
    r.commands = { 'deploy-secret': 1 }
    r.issues = [{ kind: 'tool-error', tool: 'Bash', detail: 'token abc failed on host x' }]
    const flaky = Array.from({ length: 3 }, () => rec('build', 'claude-haiku', 'low', 10, 'error'))
    const note = lessons([...HISTORY, r, ...flaky], SETTINGS, new Date('2026-10-18'))
    expect(note).toContain('1. debug: use claude-sonnet / medium, ')
    expect(note).toContain('cheaper than claude-opus / high (n=3, 100% success)')
    expect(note).toContain('Avoid: build on claude-haiku / low (3 of 3 failed)')
    expect(note).toContain('Common problems: tool-error (Bash) ×1')
    for (const secret of ['secret', 'deploy', 'host x', 'abc']) expect(note).not.toContain(secret)
    expect(note.split('\n').length).toBeLessThanOrEqual(12)
    expect(parseLessons(note)).toEqual({ debug: { model: 'claude-sonnet', effort: 'medium', samples: 3, successRate: 1 } })
  })

  test('reads a learned file and ignores malformed ones', () => {
    expect(parseLearned('{"generated":"x","types":{"debug":{"model":"m","effort":"low","samples":9}}}')?.types.debug?.model).toBe('m')
    expect(parseLearned('{"types":{"debug":{"model":1}}}')?.types).toEqual({})
    expect(parseLearned('not json')).toBeUndefined()
  })

  test('old runs age out of the window', () => {
    const old = Array.from({ length: 3 }, () => rec('debug', 'claude-haiku', 'low', 100))
    const recent = Array.from({ length: 50 }, () => rec('debug', 'claude-opus', 'high', 9000))
    const s = { ...SETTINGS, window: 50 }
    expect(recommend([...old, ...recent], 'debug', undefined, s)?.model).toBe('claude-opus')
    expect(recommend([...recent, ...old], 'debug', undefined, s)?.model).toBe('claude-haiku')
  })
})

const AUTO = { options: { mode: 'auto', minSamples: 3 } }

describe('token-ledger hooks', () => {
  test('records a turn: type, tokens, commands and issues', async ($, on) => {
    mock.store(on)
    const seen = { models: [] as string[] }
    bottom(on, seen)
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'fix the failing login test', turnId: 't1' })
    await step($, { turnId: 't1', index: 0, model: 'claude-sonnet', effort: 'medium', messageCount: 1 })
    await $.tool.call({ tool: 'Bash', command: 'npm test', tool_use_id: 'u1' })
    await $.tool.call({ tool: 'Bash', command: 'npm test', tool_use_id: 'u2' })
    await $.turn.complete({ answer: 'done', durationMs: 1200, isAborted: false, turnId: 't1', reason: 'answer', usage: USAGE })

    const last = await $.command.run({ command: 'token-ledger', args: 'last', ...RUN })
    expect(last.text).toContain('type `debug` (auto), claude-sonnet / effort medium')
    expect(last.text).toContain('npm×2')
    expect(last.text).toContain('tool-error (Bash)')
    expect(last.text).toContain('retry (Bash)')
    const report = await $.command.run({ command: 'token-ledger', args: '', ...RUN })
    expect(report.text).toContain('| debug | 1 | 100% |')
  })

  test('a #tag sets the type and is removed from the prompt', async ($, on) => {
    mock.store(on)
    bottom(on, { models: [] })
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    const sent = await $.prompt.submit({ text: '#research compare two libraries', wait: false, origin: RUN.origin })
    expect(sent.text).toBe('compare two libraries')
    await $.turn.start({ text: 'compare two libraries', turnId: 't2' })
    await $.turn.complete({ answer: 'ok', durationMs: 10, isAborted: false, turnId: 't2', reason: 'answer', usage: USAGE })
    const last = await $.command.run({ command: 'token-ledger', args: 'last', ...RUN })
    expect(last.text).toContain('type `research` (tag)')
  })

  test('auto mode switches the turn to the cheapest reliable setting', AUTO, async ($, on) => {
    mock.store(on, { records: HISTORY })
    const seen = { models: [] as string[] }
    bottom(on, seen)
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'fix the crash on save', turnId: 't3' })
    await step($, { turnId: 't3', index: 0, model: 'claude-opus', effort: 'high', messageCount: 1 })
    await step($, { turnId: 't3', index: 1, model: 'claude-opus', effort: 'high', messageCount: 3 })
    expect(seen.models).toEqual(['claude-sonnet/medium', 'claude-sonnet/medium'])
    await $.turn.complete({ answer: 'ok', durationMs: 10, isAborted: false, turnId: 't3', reason: 'answer', usage: USAGE })
    const last = await $.command.run({ command: 'token-ledger', args: 'last', ...RUN })
    expect(last.text).toContain('(applied by token-ledger)')
  })

  test('suggest mode leaves the model alone', { options: { minSamples: 3 } }, async ($, on) => {
    mock.store(on, { records: HISTORY })
    const seen = { models: [] as string[] }
    bottom(on, seen)
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'fix the crash on save', turnId: 't4' })
    await step($, { turnId: 't4', index: 0, model: 'claude-opus', effort: 'high', messageCount: 1 })
    expect(seen.models).toEqual(['claude-opus/high'])
  })

  test('auto mode does nothing when the session already runs the best setting', AUTO, async ($, on) => {
    mock.store(on, { records: HISTORY })
    const seen = { models: [] as string[] }
    bottom(on, seen)
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'fix the crash on save', turnId: 't5' })
    await step($, { turnId: 't5', index: 0, model: 'claude-sonnet', effort: 'medium', messageCount: 1 })
    expect(seen.models).toEqual(['claude-sonnet/medium'])
  })

  test('an applied setting that errors is not applied again', AUTO, async ($, on) => {
    mock.store(on, { records: HISTORY })
    const seen = { models: [] as string[] }
    bottom(on, seen)
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'fix the crash on save', turnId: 't6' })
    await step($, { turnId: 't6', index: 0, model: 'claude-opus', effort: 'high', messageCount: 1 })
    await $.turn.complete({ answer: '', durationMs: 10, isAborted: false, turnId: 't6', reason: 'error' })
    await $.turn.start({ text: 'fix the crash on save', turnId: 't7' })
    await step($, { turnId: 't7', index: 0, model: 'claude-opus', effort: 'high', messageCount: 1 })
    expect(seen.models).toEqual(['claude-sonnet/medium', 'claude-opus/high'])
  })

  test('starts from the provider guide while a type has no evidence', { options: { mode: 'auto' } }, async ($, on) => {
    mock.store(on)
    const seen = { models: [] as string[] }
    bottom(on, seen)
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'what does this flag do?', turnId: 't8' })
    await step($, { turnId: 't8', index: 0, model: 'claude-opus-5-5', effort: 'high', messageCount: 1 })
    expect(seen.models).toEqual(['claude-haiku-5-5/low'])
  })

  test('a subagent is counted against the turn that started it, even after it ended', async ($, on) => {
    mock.store(on)
    bottom(on, { models: [] })
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'research two libraries', turnId: 'm1' })
    await $.tool.call({ tool: 'Agent', description: 'look', prompt: 'look', tool_use_id: 'u9' } as never)
    await $.turn.complete({ answer: 'ok', durationMs: 10, isAborted: false, turnId: 'm1', reason: 'answer', usage: USAGE })
    await $.turn.start({ text: 'write the docs', turnId: 'm2' })
    await $.turn.complete({ answer: 'r', durationMs: 5, isAborted: false, turnId: 'x', agentId: 'sub-1', reason: 'answer', usage: { ...USAGE, model: 'claude-haiku' } })
    await $.turn.complete({ answer: 'r', durationMs: 5, isAborted: false, turnId: 'y', agentId: 'fork-9', reason: 'answer', usage: USAGE })
    await $.turn.complete({ answer: 'ok', durationMs: 10, isAborted: false, turnId: 'm2', reason: 'answer', usage: USAGE })
    const report = await $.command.run({ command: 'token-ledger', args: '', ...RUN })
    expect(report.text).toContain('| research | 1 |')
    const last = await $.command.run({ command: 'token-ledger', args: 'last', ...RUN })
    expect(last.text).toContain('subagents: 0 runs')
  })

  test('a complaint right after an answer marks it as a likely failure', async ($, on) => {
    mock.store(on)
    bottom(on, { models: [] })
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'fix the parser', turnId: 'c1' })
    await $.turn.complete({ answer: 'done', durationMs: 10, isAborted: false, turnId: 'c1', reason: 'answer', usage: USAGE })
    await $.turn.start({ text: "that's wrong, it still fails", turnId: 'c2' })
    await $.turn.complete({ answer: 'ok', durationMs: 10, isAborted: false, turnId: 'c2', reason: 'answer', usage: USAGE })
    const report = await $.command.run({ command: 'token-ledger', args: '', ...RUN })
    expect(report.text).toContain('| debug | 2 | 50% |')
  })

  test('rate, reset and unknown commands answer', async ($, on) => {
    mock.store(on)
    bottom(on, { models: [] })
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    expect((await $.command.run({ command: 'token-ledger', args: 'rate bad', ...RUN })).text).toContain('no turn to rate')
    await $.turn.start({ text: 'build a page', turnId: 'r1' })
    await $.turn.complete({ answer: 'ok', durationMs: 10, isAborted: false, turnId: 'r1', reason: 'answer', usage: USAGE })
    expect((await $.command.run({ command: 'token-ledger', args: 'rate bad', ...RUN })).text).toContain('rated the last `build` turn bad')
    expect((await $.command.run({ command: 'token-ledger', args: 'reset', ...RUN })).text).toContain('reset confirm')
    expect((await $.command.run({ command: 'token-ledger', args: 'reset confirm', ...RUN })).text).toContain('deleted')
    expect((await $.command.run({ command: 'token-ledger', args: 'last', ...RUN })).text).toContain('no turns recorded')
    expect((await $.command.run({ command: 'token-ledger', args: 'nope', ...RUN })).text).toContain('Usage:')
  })
})
