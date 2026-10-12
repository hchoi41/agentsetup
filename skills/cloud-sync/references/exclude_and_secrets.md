# What Is Not Copied

`10.INPUT` and `30.OUTPUT` are synced in full. This file lists the only exceptions. Every one is named in the dry run and in `SYNC_REPORT.md`; none is dropped silently.

**Nothing is left out for being large, numerous, old, or regenerable.** There is no size threshold, no pointer file, no latest-version-only rule and no job-type include list. Working bulk is kept out of the cloud by where it lives — `20.PROCESS` is never copied — not by guessing which files matter.

---

## A. Needs a decision — the run ends `INCOMPLETE` until it is settled

### 1. Possible secrets — held back until a human clears the file

Secrets must never reach the cloud. The script matches by **name** and does a best-effort **content scan** before any copy.

**Name patterns:**
```
.env  .env.*  *.env
*.pem  *.key  *.pfx  *.p12  *.keystore  *.jks
id_rsa*  id_ed25519*  *.ppk  known_hosts
.aws/  .ssh/  .gnupg/  .netrc  _netrc  .Renviron
credentials*  *.credentials  client_secret*  service-account*.json  *serviceaccount*.json
*secret*  *token*  *apikey*  *api_key*  *password*
cookies.txt  *cookies*.json  *.session  auth.json
```

**Content scan** (text and config files under 1 MB, the project's RAM files, and `RECIPE.md`): strings that look like live credentials — AWS access key ids, `sk-…` API keys, Slack `xox…` tokens, GitHub personal access tokens (classic and fine-grained), private-key blocks, and `password=` / `pwd=` / `Server=...;Password=` assignments. The exact patterns are in `scripts/sync_project.ps1`.

The patterns are broad on purpose, so they also catch harmless files (`tokenomics.pdf`, a script with `pwd = os.getcwd()`). Each hit is settled by a human, never ignored and never cleared by the agent on its own:

- **A real secret:** remove it from the file, or move the file out of INPUT/OUTPUT. Re-run.
- **A false alarm:** re-run with `-Allow <path relative to the project folder>`.

A clearance is written to `20.PROCESS/SYNC_ALLOW.json` in the hub (it never goes to the cloud) together with the file's SHA-256. Later syncs honour it for as long as the file's content is unchanged; a changed file is held again.

This scan is a safety net, not a guarantee.

Project-input permission: agents may create and edit files and subfolders under `01.PROJECT/<project>/10.INPUT/`, preserving provenance and prior versions when revising evidence. Deleting an input file or folder, including removing an input link, requires Max's explicit permission. Operation inputs remain read-only except for separately authorized writes; outputs retain their existing access rules. A secret finding or sync blocker is not deletion permission. The engine reports blocked content and does not delete it. Its own unfinished `.cs.tmp` copy files are transaction cleanup, not input content.

### 2. Names OneDrive refuses

OneDrive does not accept a file or folder named `.lock`, a name containing `_vti_`, or a name that starts or ends with a space. Such a file would sit in the OneDrive folder and never upload, so it is not copied. Rename it in the hub and re-run.

### 3. The cloud copy is newer

When a file differs and the copy in the destination is the newer one, someone changed it there. It is not overwritten. Decide which wins: bring the cloud version back into the hub by hand, or re-run with `-OverwriteNewer <path>`.

## B. Not content — listed, and the run can still be `OK`

```
~$*            (MS Office lock)
.~lock.*#      (LibreOffice lock)
Thumbs.db  ehthumbs.db  desktop.ini  .DS_Store
*.tmp          (OneDrive does not upload temporary files)
.git/          (a git working copy inside OneDrive is corrupted by concurrent sync)
```

If the git history itself is a deliverable, export it (`git bundle`) into `30.OUTPUT`.

---

## Blockers — the run stops before anything is written

| Blocker | Fix |
| --- | --- |
| A link or junction inside INPUT or OUTPUT (OneDrive does not sync links) | Put the real files there, or remove the link |
| Destination path longer than 400 characters (OneDrive limit) | Shorten the project folder name or the subfolder names |
| A file larger than 250 GB (OneDrive limit) | Split it |
| A folder in the destination where a file must go, or the other way round | Rename one of them |
| Not enough free space on the destination drive | Free space, or "Free up space" on folders already synced |
| A folder that cannot be read | Fix its permissions, or close what holds it |

Paths between 260 and 400 characters sync, with a warning: some Windows apps cannot open them.

## Warnings

- **Runtime folders inside INPUT or OUTPUT** — `node_modules/`, `.venv/`, `venv/`, `env/`, `__pycache__/`, test/type/lint/notebook caches, `renv/`, `ms-playwright/`, `dist/`, `build/`, `.next/`, `target/`, `out/`, `.Rproj.user/`, `packrat/`, and `.gradle/` retain the local runtime classification. They belong in `20.PROCESS` or the configured local runtime area. If one sits in INPUT or OUTPUT it is copied like everything else; the dry run warns so its role and any authorized move can be settled first. These names may also hold intentional deliverables, so classification is a review cue, not permission to delete or silently skip them.
- **Large batches** — OneDrive recommends no more than 300,000 synced items in total. The dry run warns when one sync adds 50,000 or more.
- **Items outside the three folders** — anything beside `10.INPUT`, `20.PROCESS` and `30.OUTPUT` in the project folder is not synced, and is pointed out.

---

## Quick precedence (apply top-down)

1. In `20.PROCESS`? → not copied (manifest and recipe go up instead).
2. Lock, OS or `.tmp` file, or `.git` internals? → not copied, listed.
3. Possible secret and not cleared? → held back; the run is `INCOMPLETE`.
4. A name OneDrive refuses? → not copied; the run is `INCOMPLETE`.
5. Blocker? → the run stops; nothing is written.
6. Newer in the cloud? → not overwritten; the run is `INCOMPLETE`.
7. Otherwise → copied.
