---
name: contract
description: "Invoked by the user only as /rule-of-thumb-model:contract <model folder> [--table T]. Rule-of-thumb-model command: propose the destination columns of each raw table and record the user's agreement before any SQL is written."
disable-model-invocation: true
argument-hint: "<model folder> [--table T]"
---

# contract: agree the raw columns before any SQL (P1)

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too. Then `<SKILL_DIR>/references/column-contract.md` (what a contract holds, the agreement record, what the SQL may do).

## Steps

1. No model folder yet: `ROTM init MODEL --name <name>` (writes the marker `rotm_model.json`).
2. Learn the source's columns: ask the user for a sample export (CSV) of each raw table, or its schema. A discovery
   query goes outside the model folder (inside it the contract gate refuses every `.sql` and `.m` file).
3. Propose, per raw table:
   `ROTM contract propose MODEL --table <t> --csv <sample.csv> --key <a,b> --grain "one row per ..." --rows <MIN:MAX>`
   - more flags: `--purpose`, `--connector power_query|connected_sheets|csv|other`, `--system`, `--source`,
     `--where`, `--unit COL=UNIT`, `--type COL=TYPE`, `--null COL=never|allowed`, `--total COL=VALUE[:TOL]` (a
     source-side control total), `--capacity N`, `--allow-empty`; without a sample `--columns name:type,...`;
   - known exceptions, when the raw data breaks the contract and the rows are kept: `--key-repeats N` (at most N
     rows repeat an earlier key), `--known-nulls COL=N` (a never-null column is empty in at most N rows). Propose
     writes them from the sample when the key you gave repeats, or a column is empty in at most 1% of 100 or more
     rows, and lists them under "Points to confirm". Never invent a row-number key or set `--null COL=allowed` to
     get such rows landed: the checks would then report nothing;
   - a contract that exists is refused without `--force`; `--force` on an agreed contract sets it back to proposed
     and gives it the next version number;
   - choose a short table name: it is part of the sheet names `raw_<t>` and `h_<t>` and of every formula that
     reads them, and counts toward the 100-character formula length limit. A name longer than 12 characters gets
     the note N-NAME. To use a shorter name, propose the table under that name and move the first name's files away
     with `ROTM contract remove MODEL --table <t>` (step 9);
   - a column named `row`, `in_raw`, `dup_key` or `dup`, or `<c>_null` where `<c>` is another column that gets a
     null flag (a number, integer, date or datetime column that may be empty), has the name of a helper column
     build adds. Propose prints the note N-HELPER-NAME with the suggested destination name `<name>_src` and lists
     it under "Points to confirm"; build refuses the name (exit 2). Rename the destination column in the JSON, keep
     its source expression, run `ROTM contract show MODEL --table <t>` and show the user the contract;
   - each source expression is the sample's header: bare when it is a plain identifier (`amount`), in backticks
     when it is a GoogleSQL reserved keyword or not a plain identifier (`` `order` ``, `` `Order Date` ``). The SQL
     then reads `` `order` AS `order` ``; the SQL inside a database Power Query gives such a name the connector's
     quotes (`[order] AS [order]` for SQL Server). A source expression you write is used as written.
4. Fix in `MODEL/contracts/<t>.contract.json` what a sample cannot know: units, null rules, allowed values, source
   expressions (one SQL expression each; prose goes in `description`), the control total from the source
   system's own report. Every guess the proposal made is listed under "Points to confirm" in the `.md`.
   - After every edit of the JSON run `ROTM contract show MODEL --table <t>`. It writes
     `MODEL/contracts/<t>.contract.md` again from the JSON and records `agreement.shown_sha256` (the content hash
     of the JSON the `.md` shows) in the JSON.
   - Display formats: with a sample, propose writes `format` (a key of the style dictionary's `number_formats`)
     for each integer and number column: whole numbers get the type's default (`count` for integer, `amount`
     for number); values with decimals get `amount2` (2 decimals); values mostly below 1 in size get the
     number format with the fewest decimals that shows them (`factor`, 4 decimals), or `general` when no key
     shows that many. The `.md` shows it in the column table (`Format`; `default` means the type's format) and
     under "Points to confirm". Change it to another key when the user wants more or fewer decimals.
5. Show the user what `ROTM contract show MODEL --table <t>` writes (it prints the text of
   `MODEL/contracts/<t>.contract.md`). Never show an `.md` written before the last edit of the JSON. The text ends
   with "Reply 'agreed' or say what to change." Stop and wait for the reply.
6. Changes asked: make them in the JSON, run `ROTM contract show MODEL --table <t>`, show the user what it writes,
   and wait again.
7. Agreed: `ROTM contract agree MODEL --table <t> --reply "<the user's words, verbatim>" --by <name>`.
   - `agree` refuses (exit 2, nothing recorded) with "the contract changed after it was last shown" when
     `contracts/<t>.contract.md` is missing, when it differs from the text rendered from the current JSON (its last
     line, which says whether and by whom the contract is agreed, is not compared), or when
     `agreement.shown_sha256` differs from the current content hash (an edit of a part the `.md` does not show).
     Then run `ROTM contract show MODEL --table <t>`, show the user the `.md` again and record their new reply. A
     contract proposed before v3 has no `agreement.shown_sha256`; its `.md` text alone is compared.
   - A reply that reads as a refusal is refused (exit 2), by `agree` and by the contract gate hook alike: its
     first word is negative (no, not, don't, 아니요, ...), or it holds a negated agreement anywhere (not agreed,
     have not agreed, don't agree, cannot agree, disagree, 동의하지 않, 동의 안). A reply that starts "No changes" or
     "No need to" is not a refusal by its first word.
   - A reply that holds a word that refuses is refused the same way, on every channel: reject, decline and refuse
     (also rejected, rejection, declined, refused, refusal); a negation before run, proceed, start, land, continue
     or go ahead ("Do not run the query yet."); and the Korean forms 거절, 반려, 거부 and 하지 마. The message ends
     "no agreement was given; wait for the user's reply". "Please reject the proposed sales contract." is refused.
   - A reply that postpones the decision is refused the same way, on every channel: will review, review it later
     or tomorrow, get back to you, need to check or look first, not yet, let me check, hold off and hold on, please
     wait, wait until and wait for (not when negated: no need to wait is not a stop), and the Korean forms 나중에,
     내일, 검토 후, 확인해 볼게, 아직, 보류 and 기다려. The reason starts "postpones the decision"; the message says
     "no agreement was given; wait for the user's reply". Record nothing: wait for the user's reply to
     `contracts/<t>.contract.md`, then record it. "Reviewed, agreed." and "No need to wait, go ahead." are
     recorded; "Please hold off on running the query." is refused.
   - A reply that asks for a change is refused the same way (the reason starts "asks for a change"): change,
     rename, remove, drop, replace, switch, fix, correct, revise or modify as a request ("please change", "can you
     rename", "should be changed", "needs to change") or as an imperative at the start of a sentence or clause
     ("Replace the key ...", "Looks good, but drop region"), and the Korean forms 바꿔, 변경해, 수정해 and 고쳐. A
     negated change is not a request: "no changes", "nothing to change", "change nothing", "without changes". Make
     the change, run `ROTM contract show MODEL --table <t>`, show the user the contract again and record their
     reply to the new version.
   - A reply that agrees some tables and asks for a change in another is recorded only for the tables it agrees
     without change, with `--reply` set to the user's sentence or sentences about those tables, copied exactly (the
     hook accepts a reply that is part of a user message). Example: the user wrote "Sales is good. Please change the
     unit in target.": run `ROTM contract agree MODEL --table sales --reply "Sales is good." --by <name>`; change
     `target`, show it again and wait.
   - A reply that is only a question is not an agreement: every sentence ends with a question mark (or a Korean
     question ending) and no sentence is a statement, for example "What does the amount column include?". It is
     refused (exit 2, nothing recorded), by `agree` and by the contract gate hook, on every channel; the reason
     starts "is a question". Answer the question and wait for the user's reply. A reply with a statement and a
     question ("Looks good, can you go ahead?") is recorded.
   - When the reply is part of a longer user message, the hook reads the sentences that hold it and the whole
     message for a refusal in the same way. A refusing sentence that names another table than the one given
     with `--table` does not count against this table: for the user message "Sales is agreed. Please change the
     unit in target.", the reply "Sales is agreed" is recorded for `sales` and refused for `target`. The reply
     "agreed" taken from the user message "I have not agreed. Please leave it proposed." is refused.
   - No list of words is required in an agreement, and the reply need not name the table: the user's own approval
     in any words is recorded ("agreed", "looks good, go ahead", "Reviewed, agreed.").
   - In a session, the hook accepts the reply only when the user typed it after the latest `contract propose`
     of that table, and after the latest edit of `contracts/<t>.contract.json`, in this session. One reply given
     after several proposals counts for each of those tables. Proposing and agreeing in one command is refused.
   - `agree` records the table and the contract's sha256 with the reply.
   - Headless, with the agreement written in the task file: `--channel task-file --source-file <task file>`; the
     reply must occur in that file, and no sentence of the task file that holds it may read as a refusal, a
     change request or a postponement ("This proposal is not agreed." holds the reply "agreed" and is refused). When a short reply
     also occurs in such a sentence, pass the sentence that gives the agreement, copied exactly.
   - No agreement anywhere: do not run `agree`. Report the contract as proposed.
   - A changed contract (version 2 or later): the `.md` lists "Changes since version N"; the user's reply must be
     given for this version. When `agree` says the words are the same as an earlier version's, say in the report
     which version was agreed with which words, and list the changes.
8. `ROTM contract status MODEL` lists the state of every contract: missing, invalid, proposed, agreed or stale
   (agreed, then edited: counts as not agreed).
9. A table proposed or landed under a name that is not wanted: `ROTM contract remove MODEL --table <t>`.
   - It refuses (exit 2, nothing moved) while `model.spec.json` has a sheet with `"table": "<t>"`: change the
     specification first.
   - Otherwise it moves each existing file of `contracts/<t>.contract.json`, `contracts/<t>.contract.md`,
     `queries/<t>.*` (with their `.bak_` copies), `raw/<t>.csv` and `raw/<t>.controls.json` into
     `MODEL/removed/<t>_<YYYYMMDD_HHMMSS>/`, under the same relative paths, and lists what it moved. Nothing is
     deleted.
   - The hooks let it through (it is a `rotm.py` command).

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | done; with `--strict`, no warning either | continue |
| 1 | findings: an error, or with `--strict` any warning | fix each finding: every line names the sheet, the cell and the fix |
| 2 | refused: bad input, nothing written | change what the message names |
| 3 | not available or not verified (for example Excel is missing) | say "not recalculated" (or "not rendered") in the report |
| 4 | internal error: one line; the traceback is in the state folder's `last_error.txt` | report it to the user; do not work around it by editing files |
| 5 | Excel busy: another job holds the Excel lock | wait and run again |

## What to tell the user

- The contract of each table as the `.md` shows it, and that nothing is landed until they agree.
- After `agree`: the table, the contract version, who agreed, when, and their words as recorded.
- After a reply that agreed some tables and asked for a change in another: which tables were recorded, with which
  words, and which wait for the change.
- Each known exception: what the raw data holds (for example "T00400 delivered twice"), that the rows are kept, and
  which check counts them.
- Never say a contract is agreed when `contract status` does not say `agreed`.
