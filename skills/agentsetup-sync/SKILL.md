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

Promote canonical sources first, then publish a reviewed, allowlisted payload through a pull
request. Use the publication authorization already in force; a clean check alone is not
authorization to publish.

1. **Identify and promote the exact source changes.** Skills live unpacked at
   `agents_setup/<skill-name>/SKILL.md`; packages and generated adapters are outputs. Preserve
   originals and local clauses. Do not fix only the public checkout: the next source rebuild
   would replace that edit.
2. **Resolve every manifest input.** The `AGENTS_SETUP`, `HUB`, `PLAYGROUND` and `STAGED`
   prefixes are symbolic roots. `STAGED` is the curated publishing project's `20.PROCESS`
   folder containing `repo_README.md`, `repo_gitignore.txt`, the publisher and manifest; it is
   not the public checkout. Resolve it from the current project mapping. Require every
   include source to exist, every destination to stay inside the release root, and no
   duplicate destination. Missing or unknown sources must stop preparation.
3. **Materialize a separate release checkout from those sources.** Use a dedicated
   `codex/<release-name>` branch based on the current reviewed remote main. Copy only the
   manifest allowlist with its exclusions; preserve unrelated work in other checkouts.
   Review the complete delta, including removals. Require the exact payload file inventory
   and hashes to agree with the intended source materialization. Do not include private
   project/operation content, memory, registries, transcripts, backups or test fixtures.
4. **Validate the prepared payload.** Run the relevant tests and a secret/PII/path scan on
   the complete materialized payload, including text diagrams and decoded JSON values.
   Retain results and explicit limits. The publisher's optional offline scan is only a
   supplementary check; it can report missing sources and continue, so it is not the
   completeness gate. To run that supplementary check, bind all roots explicitly:

   ```powershell
   $stagedDir = '<absolute curated publishing project 20.PROCESS folder>'
   $agentsSetup = Join-Path $env:OneDrive '980.agents_setup'
   $sourceHub = '<absolute local source hub>'
   $playground = '<absolute local playground source hub>'
   $disposableMirror = '<verified disposable publisher mirror outside source hubs and OneDrive>'
   & (Join-Path $stagedDir 'Publish-AgentSetup.ps1') `
     -Manifest (Join-Path $stagedDir 'payload_manifest.json') -StagedDir $stagedDir `
     -AgentsSetup $agentsSetup -Hub $sourceHub -Playground $playground `
     -RepoRoot $disposableMirror -Message 'feat(governance): prepare reviewed release' -DryRun
   ```

   Do not invoke the destructive publisher on the prepared release checkout. A real run
   resets and wipes its target before committing and pushing main. `-NoPush` still resets,
   wipes and commits; it is not a preview. Keep the scan enabled. Do not use `-SkipScan` to
   get around a finding.
5. **Commit and open the PR.** Recheck remote main for drift, then stage only the reviewed
   file delta in the release checkout. Verify staged paths and bytes against the final
   inventory before committing. Push its named branch and open a PR with a body file that
   states the resulting behavior, validation and limitations. Do not push the private hub's
   Git history or use the consumer's installation cache as a release checkout. Attach the
   created PR to the current task when the app supports it.
6. **Merge and record the result.** Review the PR and available checks under the authorized
   release scope. Verify the merged commit against remote main, then record that reviewed
   SHA in the curated `publish_state.json`, preserving its prior bytes. Its existing schema
   uses `last_pushed_sha`, `recorded_at_utc` and `message`; describe the PR route in the
   message. This records the reviewed source/remote baseline for the next release. Do not
   run the destructive publisher after merging just to update its state file.
7. **Tag the chosen generation.** When the payload carries the agreed fleet/governance
   generation, create an annotated tag at the verified merged SHA. Recheck that the tag is
   unused; never move an existing release tag. If a GitHub release is wanted, use that tag
   with `gh release create --verify-tag` and reviewed notes. GitHub's source ZIP contains the
   tagged repository. Report the commit, PR/release link, exact payload and any test limits.

### Scan findings

Read each reported file and line. A real hit means the source or allowlist needs correction
before publication. Preserve the failing result and rerun the gate after the correction.
The scan is bounded evidence, not a guarantee of universal secret detection. Do not put
private scan output or local source inventories into the public payload.

### Commit messages

`{type}({scope}): {summary}` — types: `feat` `fix` `docs` `chore` `refactor` `plan` `research`.
Describe the final behavior for a reader who has not seen the working conversation.

---

## Direction B — DOWNLOAD ("get the latest agent setup here")

Trigger: a new machine, a new folder, or a hub that has fallen behind.

### Quick reference: clean install with no existing Max-system folders

Use the downloaded release ZIP for a new hub. Extract it, open PowerShell 7 in the folder
containing `Install-MaxAgentSystem.ps1`, and run:

```powershell
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1
```

- Default destination: `%LOCALAPPDATA%\AgentSetup\hub`. All missing parents are created.
- Bundled source is selected automatically; no Git, personal-PC copy, or migration is needed.
- Native skill adapters are copies by default. `-UseJunctions` opts into junctions.
- `-Destination '<local path>'` chooses another hub. `-DryRun` previews without creating it.
- Only a missing or completely empty destination is accepted, including no hidden files.
  An occupied destination must use the existing-hub upgrade route below.
- Start by opening the installed hub in the agent, reading `AGENTS.md` and `00.ABOUT`, and
  filling the `AGENTS.md` identity placeholders. The project registry starts empty; no
  example projects, private history, account login, or scheduled work is imported.

See the repository-root `INSTALL.md` for the standalone first-use guide. PowerShell 7 is the
installation prerequisite; optional workflow software and AI accounts are installed or
configured separately through the machine's approved channels. Do not bypass host policy.

### Network and existing-hub routes

```powershell
# network route — Bootstrap.ps1 checks git/pwsh, clones (or fast-forwards) a clean
# cache repo to its -CacheRoot default outside OneDrive, then delegates to Get-AgentSetup.ps1:
irm https://raw.githubusercontent.com/hchoi41/agentsetup/main/Bootstrap.ps1 -OutFile "$env:TEMP\Bootstrap.ps1"
& "$env:TEMP\Bootstrap.ps1" -Destination <target> -DryRun
& "$env:TEMP\Bootstrap.ps1" -Destination <target>
```

Already have the cache repo on this machine? Run `.\Get-AgentSetup.ps1 -Destination <target>`
from it directly — Bootstrap is only the network-facing step zero.

Then confirm with the user:

- `.claude/`, `.agents/`, `.opencode/` exist at the target — their skill surfaces are **rebuilt
  locally** as junctions (or copies with `-CopyAdapters`), never cloned. If they are missing, `scripts/sync_agent_adapters.ps1` did not run
  and no agent can see the skills.
- `AGENT_HUB_ROOT` changes in user scope only if `-SetUserEnvironment` was explicitly requested; otherwise pass the hub path directly.
- Unmodified ledger-owned stock, including `AGENTS.md` and `00.ABOUT/CLAUDE.md`, updates. Locally edited or created payload/source files are kept and reported; no automatic text merge is performed. Generated adapter documents and copied skill surfaces are regenerated on each run, including edits made directly to those generated documents or copied skill surfaces. Customize the canonical `skills/` and `AGENTS.md` sources instead. `-Force` replaces payload conflicts only after saving original bytes; local project controls remain untouched.
- All five project-first core skills resolve through the adapters and the operations dashboard exists. A legacy workspace requires its governed migration first.
- For an extracted repository ZIP, use `Get-AgentSetup.ps1 -SourceRoot <extracted-root> -Destination <target>`; no Git/network is needed. `-CopyAdapters` supports installations without junctions.

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
4. **Publish the reconciled sources through Direction A.** Base the reviewed release branch
   on the accepted remote history, validate the complete materialized payload, and use a PR.
   After merging, record the verified remote SHA and PR provenance in `publish_state.json`.
   Do not use the destructive publisher's `-AcceptRemote` flag as a substitute for review.

---

## Hard rules

1. **Never push `agents_setup`'s own `.git`.** It has separate history whose old commits were
   never scanned. Use a separate reviewed release checkout outside OneDrive. The consumer's
   Bootstrap `-CacheRoot` installation cache is not a publishing workspace.
2. **Never put the git working copy inside OneDrive.** Concurrent sync corrupts `.git`.
3. **Personal data never publishes.** `career_db/` (in both `scripts/` and `tests/`), all
   `12–15` memory folders, `70.carbon_copy/`, and the registries are permanently excluded.
   `tests/career_db/fixtures/notion_export.json` in particular holds real career data.
4. **The manifest is the allowlist.** If it isn't named in `payload_manifest.json`, it does not
   ship — that is the design, not a bug.
5. **Junctions are not artifacts.** They are rebuilt on every install. Never try to commit them.
