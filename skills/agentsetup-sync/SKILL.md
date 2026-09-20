---
name: agentsetup-sync
description: Publish the local agent setup to GitHub, install the latest agent setup onto a machine, or ingest changes contributed directly to the GitHub repo. Use when the user says "update the agent setup", "push the agent setup", "publish my skills", "I want the latest agent setup on this device/folder", "install the agent setup on a new computer", or adds a new skill/workflow to a hub and wants it propagated. Also use when the publisher stops with "DRIFT GUARD" or a pull request was merged on GitHub.
---

# agentsetup-sync

Moves the agent setup along the promotion pipeline — publish up (A), install down (B), ingest back (C).

```
TIER 1  local hub            <local hub root(s)>  — short local paths OUTSIDE OneDrive
          │                  new skills / workflows are born here
          ▼  PROMOTE
TIER 2  agents_setup         $env:OneDrive\980.agents_setup
          │                  curated central setup, OneDrive-synced
          ▼  PUBLISH
TIER 3  working copy         <cache repo>  ← git lives here, OUTSIDE OneDrive (Bootstrap.ps1's -CacheRoot default)
          │
          ▼  PUSH
        GitHub               https://github.com/hchoi41/agentsetup   (PUBLIC)
          │
          ▼  INSTALL
        any machine          a fully wired hub
```

**Never skip a tier.** Editing the repo working copy directly gets overwritten by the next
publish. Editing `agents_setup` without promoting from a hub loses the hub's version.

---

## Direction A — UPLOAD ("update the agent setup")

Trigger: the user added a skill, workflow, script, or governance change to a local hub and
wants it to become the published state.

1. **Identify what changed.** Ask which hub, or diff the hub's `skills/` and `scripts/`
   against `agents_setup`. Name the specific files — do not guess.
2. **Promote Tier 1 → Tier 2.** Copy the changed canonical sources into `agents_setup`.
   Skills go to `agents_setup/<skill-name>/SKILL.md` (unpacked source is canonical — never
   edit a `.skill` package or an installed `.claude/skills/` copy).
3. **Update the manifest if the shape changed.** A brand-new skill or script needs an entry in
   `payload_manifest.json`. Nothing publishes unless the manifest names it.
4. **Publish.** From the repo working copy:
   ```powershell
   .\Publish-AgentSetup.ps1 -Message "feat(skills): add <name>" -DryRun
   .\Publish-AgentSetup.ps1 -Message "feat(skills): add <name>"
   ```
5. **Report** the commit SHA and what shipped.
6. **Tag governance generations.** When the payload carries a governance version bump (v4, v5, …),
   tag the published commit from the cache repo — `git tag -a v<N> <sha> -m "<one-line note>"` then
   `git push origin v<N>` — so installed machines can tell which generation they run
   (`git describe --tags`).

### The scan gate

`Publish-AgentSetup.ps1` runs a secret/PII scan before every commit and **refuses to proceed on
any hit**. The repo is public — this is the reason it stays safe. If the gate fires:

- Read the reported file:line. Do not reflexively add `-SkipScan`.
- A real hit means the manifest is pulling in something it shouldn't → fix the manifest.
- Only use `-SkipScan` when a human has eyeballed every hit and confirmed it is a false positive.

### Commit messages

`{type}({scope}): {summary}` — types: `feat` `fix` `docs` `chore` `refactor` `plan` `research`.
Scope is the folder: `skills`, `scripts`, `orchestrate`, `governance`, `template`.
Describe what changed for a reader who was not in the session.

---

## Direction B — DOWNLOAD ("get the latest agent setup here")

Trigger: a new machine, a new folder, or a hub that has fallen behind.

```powershell
# step zero on a brand-new machine — Bootstrap.ps1 checks git/pwsh, clones (or syncs) the
# cache repo to its -CacheRoot default outside OneDrive, then delegates to Get-AgentSetup.ps1:
irm https://raw.githubusercontent.com/hchoi41/agentsetup/main/Bootstrap.ps1 -OutFile "$env:TEMP\Bootstrap.ps1"
& "$env:TEMP\Bootstrap.ps1" -Destination <target> -DryRun
& "$env:TEMP\Bootstrap.ps1" -Destination <target>
```

Already have the cache repo on this machine? Run `.\Get-AgentSetup.ps1 -Destination <target>`
from it directly — Bootstrap is only the network-facing step zero.

Then confirm with the user:

- `.claude/`, `.agents/`, `.opencode/` exist at the target — these are **junctions rebuilt
  locally**, never cloned. If they are missing, `scripts/sync_agent_adapters.ps1` did not run
  and no agent can see the skills.
- `AGENT_HUB_ROOT` is set to the target (user scope).
- `AGENTS.md` and `00.ABOUT/CLAUDE.md` were **merged, not overwritten**, if the target already
  had local sections. `Get-AgentSetup.ps1` preserves them unless `-Force` is passed.

---

## Direction C — INGEST ("someone changed the repo directly")

Trigger: the publisher stops with **`DRIFT GUARD:`** — origin/main holds commits this pipeline
did not push (a merged pull request, a hotfix made on another machine). Publishing over them
would erase that work; this direction brings it home instead.

1. **See what came in.** From the cache repo: `git fetch origin`, then
   `git log <last-pushed-sha>..origin/main --stat` — read every foreign commit. The last-pushed
   SHA is in the staging folder's `publish_state.json`.
2. **Review like a maintainer.** The scan gate checks what goes OUT, not what comes IN — read the
   diff for secrets, personal data, machine paths, and licence problems before adopting anything.
3. **Apply accepted changes to the SOURCES** (the curated store the manifest reads from) — never
   only to the mirror, which is rebuilt from sources on every publish. A brand-new file also
   needs a `payload_manifest.json` include entry. For rejected changes, say why on the PR.
4. **Republish with `-AcceptRemote`.** The pipeline commit lands ON TOP of the foreign commits,
   so the contributor's history is preserved; `publish_state.json` records the new tip and the
   guard is satisfied.

---

## Hard rules

1. **Never push `agents_setup`'s own `.git`.** It has separate history whose old commits were
   never scanned. The published repo is its own disposable working copy at Bootstrap.ps1's
   `-CacheRoot` default, outside OneDrive.
2. **Never put the git working copy inside OneDrive.** Concurrent sync corrupts `.git`.
3. **Personal data never publishes.** `career_db/` (in both `scripts/` and `tests/`), all
   `12–15` memory folders, `70.carbon_copy/`, and the registries are permanently excluded.
   `tests/career_db/fixtures/notion_export.json` in particular holds real career data.
4. **The manifest is the allowlist.** If it isn't named in `payload_manifest.json`, it does not
   ship — that is the design, not a bug.
5. **Junctions are not artifacts.** They are rebuilt on every install. Never try to commit them.
