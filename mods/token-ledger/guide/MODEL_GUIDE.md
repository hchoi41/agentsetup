# Model usage guide

Starting settings for token-ledger, and a reference for choosing a model and reasoning effort per
task type across providers. Last checked: 2026-10-18. A scheduled task re-checks the official
pages monthly (first of the month) and opens a pull request when something changed.

**How it is used.** token-ledger suggests the Claude column (`hooks/defaults.ts`) for a task type
until it has `minSamples` of your own runs of that type; from then on your measured evidence
decides. The other columns are a reference for the other agents in the Max setup.

**Status of sources.** Anthropic's pages were read in full. OpenAI, Google, xAI and Meta pages
could not be fetched on 2026-10-18 (network); their rows come from search snippets of the
official pages and, for Muse, third-party listings. Verify against the live pages before relying
on them.

## Official guidance

### Claude (Anthropic) — verified
- Models: `claude-fable-5-1` (most capable: long-running agents, deep research), `claude-opus-5-5`
  (complex agentic coding, large refactors, computer use; the suggested starting point for most
  work), `claude-sonnet-5-5` (everyday coding, analysis, content, tool use), `claude-haiku-5-5`
  (lowest latency and price, high volume, subagents).
- Effort: "tuning effort is often a better lever than switching models." Default `high` on Fable
  5.1 and Sonnet 5.5, `medium` on Opus 5.5 and Haiku 5.5; `xhigh` and `max` exist. If Opus 5.5 at
  `xhigh`/`max` falls short, move to Fable 5.1. Pattern: cheap worker plus frontier advisor.
- Sources: https://platform.claude.com/docs/en/about-claude/models/choosing-a-model,
  https://platform.claude.com/docs/en/build-with-claude/effort

### ChatGPT / GPT (OpenAI) — snippets of official docs
- Models: `gpt-5.6-sol` (flagship; `gpt-5.6` alias), `gpt-5.6-terra` (balance), `gpt-5.6-luna`
  (efficient, high volume).
- Effort (`reasoning.effort`): try `low` before `none` when tools or multi-step work matter;
  `none` only for latency-critical work; raise to `high`/`xhigh`/`max` only when evals show a gain;
  too much effort with vague stopping criteria causes overthinking.
- Sources: https://developers.openai.com/api/docs/guides/latest-model/gpt-5.6.md,
  https://developers.openai.com/api/docs/guides/latest-model/gpt-5.5

### Gemini (Google) — snippets of official docs
- Models: `gemini-3.8-flash`, `gemini-3.5-flash-lite` (high throughput, low cost, subagents,
  document parsing), `gemini-3.1-pro-preview` (complex reasoning).
- Thinking (`thinkingLevel`: minimal/low/medium/high): low minimises latency and cost, medium
  for most tasks; `minimal` does not guarantee no thinking; 3.1 Pro has no `minimal`.
- Sources: https://ai.google.dev/gemini-api/docs/models,
  https://ai.google.dev/gemini-api/docs/thinking-mode

### Grok (xAI) — snippets of official docs
- Models: `grok-4.7`, `grok-4.6`, `grok-4.5`, `grok-4.3`.
- Effort (`reasoning.effort`): low/medium/high (default high) on 4.5; 4.6/4.7 add `xhigh`. No
  official per-task guidance or price tiers found; the docs conflict on grok-4.3's effort levels.
- Sources: https://docs.x.ai/developers/model-capabilities/text/reasoning,
  https://docs.x.ai/developers/models/grok-4.3

### Muse (Meta) — mostly third-party, unverified
- Meta Model API (public preview, US developers). `muse-spark-1.3` reported as current;
  OpenAI-compatible Responses API with `reasoning.effort` minimal…xhigh. No official per-task
  guidance found.
- Sources: https://developer.meta.com/ai/products/meta-model-api/ (landing page),
  https://www.llmreference.com/model/muse-spark-1.3/meta-model-api (third party)

## Starting settings by task type

No provider publishes a per-task table; cells are drawn from the guidance above. ✓ = stated in
official guidance, otherwise inferred.

| Task | Claude | GPT | Gemini | Grok | Muse |
|---|---|---|---|---|---|
| debug | sonnet-5-5 / high ✓ | 5.6-terra / medium | 3.8-flash / medium | 4.6 / high | spark-1.3 / high |
| test | sonnet-5-5 / medium | 5.6-terra / low | 3.8-flash / low | 4.5 / low | spark-1.3 / low |
| review | opus-5-5 / medium | 5.6-terra / high | 3.1-pro / high | 4.6 / high | spark-1.3 / high |
| refactor | opus-5-5 / medium ✓ | 5.6-sol / medium | 3.1-pro / medium | 4.6 / medium | spark-1.3 / medium |
| docs | sonnet-5-5 / low | 5.6-luna / low | 3.5-flash-lite / low | 4.3 / low | spark-1.3 / low |
| research | fable-5-1 / high ✓ | 5.6-sol / high | 3.1-pro / high | 4.7 / high | spark-1.3 / high |
| build | opus-5-5 / medium ✓ | 5.6-sol / medium | 3.1-pro / medium | 4.7 / medium | spark-1.3 / medium |
| ops | sonnet-5-5 / low | 5.6-terra / low | 3.8-flash / low | 4.3 / low | spark-1.3 / low |
| question | haiku-5-5 / low | 5.6-luna / none–low | 3.5-flash-lite / minimal | 4.3 / none–low | spark-1.3 / minimal |
| other | your setting | 5.6-terra / medium | 3.8-flash / medium | 4.5 / default | spark-1.3 / medium |

## Open-weight frontier models

Maintained on request only. Set `"includeOpenModels": true` in `guide/config.json` (or ask Claude
to check them) and the next check adds this section.
