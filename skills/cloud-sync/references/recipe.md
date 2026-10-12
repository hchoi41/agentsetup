# The Recipe — how the work was done and why

`20.PROCESS/RECIPE.md` is the part of the working folder that reaches the cloud. It records how the work was done and why, written as instructions someone else could follow. `start-project` hands it to the next project of the same kind; `operationalize` turns it into an operation's standing procedure.

**The test:** could someone holding only `10.INPUT`, `30.OUTPUT` and this file do the work again — and skip the dead ends?

---

## 1. Write it from evidence (forward pass)

Work through these sources in order and stop when you have enough:

1. The project's RAM versions and the acceptance criteria in `10.INPUT` — decisions, changes of direction, what was rejected and why.
2. The shape of `20.PROCESS` — `PROCESS_MANIFEST.json` (the first `-Apply`, or `-RecipeOnly -Apply`, writes it): what was made, which artifacts went through the most versions, what was touched last.
3. The working files themselves — read the ones that mattered (most versions, last modified), not all of them.
4. The conversation, if this session did the work.

Write what happened, not what should have happened. Where the evidence does not show *why* something was done, write "reason not recorded". Do not invent one.

## 2. Format

```markdown
# Recipe — {folder name}

- status: provisional | final
- outputs_fingerprint: {16 characters, printed by the script}
- written: {YYYY-MM-DD}
- checked: {YYYY-MM-DD}
- type1: {type1}
- type2: {type2}

## What was asked and what was delivered
{Two or three sentences: the brief (from `10.INPUT`) and the deliverables (in `30.OUTPUT`).}

## Steps
1. **{The instruction, in the imperative.}**
   - Why: {the reason, or the decision and what it replaced}
   - Uses: `10.INPUT/{file}`, {tool}
   - Makes: `30.OUTPUT/{file}`, or the intermediate it produces
   - Lane: D0 (code) / D1 / D2 (judgment)

## Dead ends
- {What was tried and dropped} — {why}

## Reverse-engineering check
| Output | Made by step | From | Verdict |
| --- | --- | --- | --- |
| `30.OUTPUT/{file}` | 3, 5 | `10.INPUT/{file}` | explained |
| `30.OUTPUT/{folder}/` | 6 | — | explained |

Unused inputs: {list, or "none"}
Open gaps: {list, or "none"}

## Next time
- {What to do differently; what to turn into a template or a script}
```

Rules the script and the other skills rely on:

- Write every output path in backticks, starting with `30.OUTPUT/`.
- One row per file in `30.OUTPUT`. A folder of same-kind files may be one row: write the folder path with a trailing `/`.
- `status` is `provisional` while the project is active, and `final` once it is completed or operationalized. A `final` recipe must account for every output and carry the current stamp; a `provisional` one only has to exist.
- Keep `type1` and `type2` on their own lines, exactly as shown: `start-project` finds earlier recipes of the same kind by those two lines. For a folder named before the naming convention, choose the verb and the theme keyword that describe the work.

## 3. Cross-check by reverse engineering (last)

The forward pass tells the story the way it was remembered. The reverse pass tests that story against what exists.

1. **Reconstruct from the outputs alone.** Take a fresh listing of `30.OUTPUT` and `10.INPUT`.
   - With subagents available: give a fresh one only `10.INPUT` and `30.OUTPUT` — not the recipe, not `20.PROCESS`, not the RAM — and ask it to work out, file by file, what must have been done to produce each output from those inputs.
   - Without: do the same yourself, from the listing, before re-reading your Steps.
2. **Fill the table** in "Reverse-engineering check" from that reconstruction.
3. **Compare it with the Steps and fix the recipe:**
   - an output no step makes → a step is missing
   - a reconstructed step the recipe lacks → it was done but not written down
   - a step that left no trace in any output → move it to Dead ends, or say what it was for
   - an input no step uses → list it under "Unused inputs"
   - a step that needs a file found only in `20.PROCESS` → put its content in the recipe, or ask the user to move the file to `30.OUTPUT`
4. **Stamp it.** Copy the `outputs_fingerprint` the script printed into the header. It is built from the name, size and modified time of every file in `30.OUTPUT`, so it ties this check to the exact outputs. When an output is added, removed or saved again, the script reports the recipe as `stale` and the check is redone.

## 4. What the script verifies, and what it cannot

`sync_project.ps1` verifies what code can: `RECIPE.md` exists, every file in `30.OUTPUT` is named in it, the fingerprint matches the current outputs, and the text contains nothing that looks like a credential. `-RecipeOnly` runs just these checks, with no destination.

Whether the steps are *right* is the judgment the reverse pass exists for. A recipe can pass every check and still be wrong.
