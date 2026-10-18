import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'

import { classify, commandNames, recommend, splitTag } from '../hooks/ledger.ts'
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
  on('tool.call', () => ({ result: '', text: 'exit 1: command not found', isError: true }) as never)
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
})

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

  test('auto mode switches the turn to the cheapest reliable setting', { options: { mode: 'auto' } }, async ($, on) => {
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

  test('suggest mode leaves the model alone', async ($, on) => {
    mock.store(on, { records: HISTORY })
    const seen = { models: [] as string[] }
    bottom(on, seen)
    await $.session.start({ cwd: '/work', surface: null, isInteractive: false })
    await $.turn.start({ text: 'fix the crash on save', turnId: 't4' })
    await step($, { turnId: 't4', index: 0, model: 'claude-opus', effort: 'high', messageCount: 1 })
    expect(seen.models).toEqual(['claude-opus/high'])
  })
})
