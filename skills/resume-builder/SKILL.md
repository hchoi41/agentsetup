---
name: resume-builder
description: "Three-phase resume building: (1) JD frame construction — deep-parse the target JD and triangulate against similar JDs to build the skeleton of what the role really asks for, (2) gap-driven deep interview to extract tacit knowledge from the applicant, (3) multi-filter red-team to validate the resume from every reader's perspective. Use this skill whenever the user mentions 'resume', 'CV', 'job application', 'apply for a job', 'tailor my resume', 'cover letter', 'JD', 'job description', '자소서', '지원동기', '경력기술서', or asks for help positioning their experience for a role. Also trigger when the user uploads a job posting, shares a job link, or says things like 'I want to apply for this', 'how do I position myself for this role', 'build a JD frame', 'red-team my resume', 'pull the JD from Notion', 'search Notion for the JD', or 'the JD is in my Notion tracker'. Use this skill even if the user mentions only one phase — the full workflow is designed to work together."
---

# Resume Builder

A three-phase skill for building resumes that actually land interviews. The core insight: resume writing is a **translation problem** — converting what the applicant knows (tacit knowledge) into what the reader needs to see (JD context). Most AI tools fail in three ways: they parrot the JD back, they rephrase what's already written, or they skip straight to bullet-writing without first understanding what the reader is actually looking for. This skill fixes all three failure modes — first by building a rigorous JD frame, then extracting tacit knowledge against that frame, then stress-testing the draft through every reader's eyes.

## When to Use Each Phase

- **Phase 0 (JD Acquisition)**: The input-routing precondition — get the target JD in hand before anything else. Either it is already in the INPUT folder (Case A, default) or it must be pulled from Notion (Case B). See *Phase 0* below.
- **Phase 1 (JD Frame Construction)**: Always start here. Build a rigorous skeleton of what the role really asks for by deep-parsing the target JD and triangulating against similar JDs. This is the foundation every later step references — don't rush it.
- **Phase 2 (Gap-Driven Deep Interview)**: Once the frame exists, bridge it against the applicant's experience. This is the extraction phase — pulling out evidence the user knows but hasn't said yet.
- **Phase 3 (Multi-Filter Red-Team)**: When a resume draft exists and needs adversarial validation. This is the quality gate — catching what a real recruiter would flag before submission.
- **All three phases together**: The default. Phase 1 sets the frame, Phase 2 fills it with extracted material, Phase 3 stress-tests the result.

The effort is front-loaded. Phase 1 is deliberately the biggest single block of work — a shallow frame compounds into every bullet downstream, so the cost of rushing it is invisible but very real.

## Critical Principles

### 0. The In-Seat Test — the north star the other principles serve

Every editorial decision optimizes for one closing thought in the reader's head: **"이 사람이 우리 회사에서 이 직무로 일하는 모습이 그려진다"** — *I can imagine this person working here, in this role.* Keyword coverage, format compliance, and filter survival are necessary but not sufficient; they get the document read — the in-seat image gets the interview.

The instrument is a **compelling narrative built from true material** — never fabrication. Curate and frame the applicant's real steps so that, looking back from this JD, every little step reads as the step that led here: the applicant reads as the company's next chapter. The mechanism is the same as dating or marriage — the reader isn't scoring a parts list, they're deciding whether the story of "us" is plausible. Two corollaries:

1. **Narrative beats inventory.** Given equally true material, choose the selection and ordering that makes the trajectory converge on this role (the destiny arc), not the one that maximizes bullet count.
2. **The narrative must survive Principle 2.** Destiny framing is a curation-and-emphasis operation on verified claims. The moment it requires a new fact, it has left this skill's territory.

### 1. Trust the User's Self-Selection

The user has already decided this role is worth applying to. They see overlap — maybe not a bull's eye, but they believe they're on the target. Your job is **not** to question whether they should apply. Your job is to surface the bridging evidence that exists in their head but isn't on paper yet.

Frame probes as "help me understand the bridge you see" — never as "are you sure you're qualified?"

### 2. Accuracy Over Positioning Games

The skill exists to surface and sharpen what's true — not to inflate what isn't. Two corollaries:

1. **Don't push verb variety toward solo-achievement when the work was collaborative.** PM work often correctly uses contributory verbs (기여, 지원, 협력). Rewriting "지원" to "주도" for the sake of stronger positioning is inflation, not translation. If the role was genuinely supportive, the honest frame is to show what made that support valuable — the scope, the stakes, the judgment — not to relabel it.
2. **Bridge claims must be honest analogues, not wishful substitutions.** When the JD asks for an experience the user doesn't have, the bridge must name a real analogue and explain the transfer logic (see Phase 2 Step 3). "I can learn fast" is not a bridge. Manufactured analogues get caught at interview and poison everything behind them.

Accuracy builds durable credibility across the full hiring funnel. Inflation may survive ATS but collapses under any careful reader.

### 3. Offer Count Beats Hit Rate — Ship Inside the Window

The objective function is offers received, not per-application polish ("합격률보다 합격 개수"). Two operational consequences:

1. **Ship the 안정권 draft.** A defensible draft submitted inside the window beats a perfect one outside it. Deep audits and further passes can continue after submission — most portals allow update or withdraw-and-reapply far more easily than applying to a closed req.
2. **Triage before scoping.** Posting age, stated deadline, and 우대조건 coverage set the pass budget *before* any framing starts (see Phase 0 → Window & Coverage Triage).

---

## Phase 0: JD Acquisition (Input Routing)

Phases 1–3 assume the target JD is already in hand. Phase 0 is the precondition that gets it there. **Where the JD comes from determines the first move — route by which case matches before doing anything else.**

- **Case A — JD already in the INPUT folder (default).** The JD file already sits in the project's INPUT folder, or the user pasted / uploaded it directly. This is the standard path and behaves exactly as before: read the JD and go straight to Phase 1.
- **Case B — Notion is the source.** The user points at Notion — "search Notion for the JD," "pull the JD from Notion," "it's in my Notion tracker," or otherwise names Notion as where the JD lives. The JD must be searched out of the user's Notion job-application system, extracted, carbon-copied into the workspace, and formalized as a project before Phase 1 begins.

If no JD is present and the source is not specified, ask where it is (INPUT folder, paste, a link, or Notion) rather than guessing.

### Case A — JD in INPUT (default)

1. Locate the JD in the project's INPUT folder (or use the pasted / uploaded text).
2. Read it in full.
3. Proceed to Phase 1. No carbon-copy or project scaffold is required unless the user asks for one.

### Case B — Pull the JD from Notion

Trigger: the user mentions Notion in connection with the JD. Run these steps in order.

**B1 — Search Notion.** Search the user's Notion workspace for the target role's JD and its surrounding context. Search these three databases by name first — they are the user's canonical job-application system:

- **2026-06 Job Application KPI Tracker**
- **Job Application Tracker**
- **Job Description Analysis**

Then run additional free-text Notion searches to catch anything outside those databases — by company name, role title, requisition ID, and any identifier the user supplied. Use `notion-search` for discovery, `notion-query-data-sources` to filter within a named database, and `notion-fetch` to pull full page content. Only use page IDs returned by search — never invent them. If several entries match, list them and confirm the right one with the user before extracting.

**B2 — Extract.** From the matched Notion pages, pull:

- The **full JD text, verbatim** — this becomes the reference document for Phase 1 and for the acceptance criteria.
- **Relevant surrounding information**: application status / stage, deadlines, requisition ID, company and role metadata, recruiter contacts, prior notes, any existing JD analysis, KPI-tracker fields, and links — anything in those three databases (and related pages) that bears on the application.

Record each item's Notion page URL / ID so the carbon copy is traceable back to the original.

**B3 — Start the project.** Activate the **`/start-project`** skill to formalize this application as a tracked project. That creates the paired `11.OUTPUT_ROUGH/{project}/` + `03.OUTPUT_FINAL/{project}/` folders, registers the project, and creates the initial RAM. Name the project after the company + role. The extracted JD is the natural "reference document" for `start-project`'s acceptance-criteria step.

**B4 — Carbon-copy into OUTPUT_ROUGH.** Write the extracted JD and context as files **inside the project folder `start-project` just created** (`11.OUTPUT_ROUGH/{project}/`). These are carbon copies — faithful local reproductions of the Notion source, not summaries. Follow the workspace naming convention `project_content-type_vN_YYYYMMDD`:

- the JD itself — e.g. `{slug}_jd_v1_{YYYYMMDD}.md`
- the extracted context — e.g. `{slug}_notion-extract_v1_{YYYYMMDD}.md` (status, deadlines, req ID, contacts, prior analysis, source URLs)

**B5 — Hand off to Phase 1.** With the JD carbon-copied into the project's OUTPUT_ROUGH folder, proceed to Phase 1 using the **local copy as the source of record**. If the Notion extract already contains prior JD analysis, fold it into the frame — but re-verify it against the JD text; do not trust stale analysis blindly.

### Window & Coverage Triage (run before Phase 1, either case)

With the JD in hand, set the pass budget before building the frame:

1. **Window risk.** Record posting date/age and deadline. Fresh (<14 days) or deadline-dated → full three-phase treatment. **30+ days old with no stated deadline → treat as expiring: compress to one tailoring pass, submit, then keep improving while waiting.** Re-check liveness immediately before any additional polish pass.
2. **우대조건 coverage.** Count the 우대/가산점 items the user can honestly claim. **Below ~35% coverage → flag it and recommend a conservative approach** (compressed effort, or redirecting the hours to a better-covered target). This is a flag, not an auto-kill — the user's self-selection still rules (Principle 1).
3. **Output one line:** window verdict + 우대 coverage % + agreed pass budget (full / compressed / submit-now-improve-later).

---

## Phase 1: JD Frame Construction

### Purpose

Before you write anything, you must understand what you're writing toward. Most resume failures trace back to **working from a shallow reading of the JD**. This phase builds a rigorous skeleton — the "JD frame" — that becomes the reference standard for every downstream decision. The bulk of Phase 1 effort (~80%) goes into deep-parsing the target JD and constructing the frame; the remaining ~20% goes to similar-JD triangulation for calibration.

### Step 1: Deep Parse of the Target JD

Read the JD slowly, more than once. Extract:

1. **Role archetype** — Abstract away the specific company: what is this role at its core? ("Mid-level corporate planning PM in a Korean mid-to-large IT firm" is more useful than "경영기획 담당자 at Company X.")
2. **Core function prongs** — The 3–6 fundamental responsibility areas, ranked by apparent centrality. Centrality is judged by position in the JD (top = central), space allocated, and language intensity.
3. **Hard filters** — Binary pass/fail criteria: years of experience, degree level, major, certifications, citizenship/visa, language scores. These are ATS/HR kill conditions.
4. **Preferred filters (우대조건)** — Softer asks but still scoring. Each 우대 item you leave silent is a point drop in the Korean HR scoring rubric. Treat these as hard asks for the purposes of building bridges, even when the surface language is soft.
5. **Tool/system vocabulary** — Every named tool, platform, system, methodology. List them verbatim (ATS matches on exact tokens).
6. **Domain-specific vocabulary** — Industry and internal-sounding nouns (자회사 관리, 경영회의체, 손익분기점 분석, A/B 테스트). These reveal the world the team actually lives in.
7. **Signal intensity map** — For each requirement, note the language: "필수," "이상," "우대," "가산점," "있으면 좋은," "관련 경험." Different intensities = different scoring weights.
8. **Implicit requirements** — What the JD implies but doesn't state: seniority tier, decision scope, stakeholder altitude, autonomy expected. Read between the lines.
9. **Culture signals** — Vibe words: "주도적," "데이터 기반," "꼼꼼한," "협업 중심," "빠른 실행." These hint at the team's self-image.

Output this as a structured **target-JD parse** before proceeding to similar-JD triangulation.

### Step 1b: Company-State Research — the why-this-role-why-now layer

Text-level parsing (Step 1) tells you what the company wrote. This step tells you **why they wrote it now**. A JD is an artifact of a moment in the company's life — a hire, a project, a pivot, a fire. Reading that moment converts generic requirements into specific intent, and it is where the in-seat narrative (Principle 0) gets its anchor.

1. **Sweep the company's current state**: recent leadership hires (especially foreign or cross-industry executives), megaprojects and expansions, strategic pivots (사업 전환·신사업), global partnerships and conference appearances, regulatory or reputational events. Sources: news search, IR/공시, the company's own PR. Budget: 15–30 minutes on a compressed pass; more only if the frame stays ambiguous.
2. **Form a hypothesis** for each notable JD requirement: "영어가능자" + a newly hired foreign president = the role likely interfaces with that office. A hypothesis sharpens emphasis and 자소서 framing — it is never asserted as fact in any asset.
3. **Verify before use.** Any company fact that will appear in a 자소서, cover letter, or interview answer must be independently verified (two sources, or one primary). Findings delivered by secondary tools (NotebookLM-style syntheses, aggregator summaries) are leads, not facts, until confirmed.
4. **Flag landmines.** Research surfaces what to avoid as reliably as what to use: live controversies, disputed projects, sensitive litigation. List them with an explicit DO-NOT-REFERENCE tag — an applicant praising a company's controversial venture reads as unprepared, not enthusiastic.
5. **Respect entity scope.** Group-level projects and subsidiary-level postings are different seats. Use group state as *context* for what the organization now values, and claim seat-level relevance only where the posting itself supports it — presuming access to the flagship project from a subsidiary staff role reads as naive.

Output: a short **company-state brief** appended to the JD frame — verified facts (with sources), hypotheses (labeled), landmines, and a one-line "why this role, why now" reading. Route it into the 자소서 지원동기/왜 blocks, the Phase 2 destiny-arc framing, and interview-prep seeds.

### Step 2: Pull Similar JDs for Triangulation

A single JD is a sample size of one. It may be idiosyncratic — written by a specific hiring manager with personal preferences, or copy-pasted from a template that doesn't reflect what the team actually wants. Triangulation reveals the **archetype-level signal** underneath the employer-specific noise.

1. Gather 3–5 similar JDs from comparable companies and comparable role levels. Sources: the user's saved listings, search, or the target company's other postings.
2. For each similar JD, run the same parse from Step 1, abbreviated — you're mining for overlap, not producing a full frame per listing.
3. Compare.
4. **Add the time axis: 3 years of posting history.** Where available, pull the target company/role's own postings from the past 3 years (and competitors'). History separates the stable core (asked every year → true requirement) from drift (new this year → current priority or a new hiring manager), and completes the 필수/우대 inventory that the coverage triage and 가산점 planning depend on.

### Step 3: Construct the Consolidated JD Frame

From the target JD and the similar JDs, build the consolidated frame:

- **Archetype-universal requirements**: appear in ≥3 of the JDs (including the target). These are the role's true core. A gap here is a structural problem.
- **Employer-specific requirements**: appear only in the target JD. Honor them because they reveal this hiring manager's priorities, but don't overweight them as "industry standard."
- **Language variation map**: same underlying requirement, different wording across JDs. The resume should use the **target JD's exact tokens** for ATS while carrying the archetype's meaning.
- **Silent consensus**: what do ALL the similar JDs assume without stating? (E.g., Excel proficiency, business email English.) These are invisible until they're missing.

### Step 4: Produce the Frame Output

The frame document should include, in this order:

1. **Role archetype one-liner** — the compressed definition of this role in a single sentence
2. **Core function prongs ranked by centrality** — with a note on which are archetype-universal vs. employer-specific
3. **Hard filter checklist** — each item marked as met / partial / missing from the user's current materials
4. **Preferred filter checklist** — same marking, with a bold flag on any silent item (each silent 우대 is a tier-drop risk)
5. **Vocabulary map** — verbatim tokens to use, grouped by function area
6. **Signal intensity notes** — which requirements carry the most scoring weight
7. **Implicit / culture signals** — the "between the lines" reading
8. **우대 coverage line** — the triage coverage % restated against the now-complete 우대 inventory (re-run the ~35% call if the inventory changed it)
9. **Adjacency map (optional, on request)** — works-with 직무, similar-function 직무, competitor/중견 firms, and 전후방 industries where this same frame + evidence base can be reused. Supports portfolio fan-out (offer count > hit rate) at near-zero marginal framing cost.

Present the frame to the user for sanity check before advancing to Phase 2. Their domain knowledge will catch things you missed, and they may reorder what you ranked as central.

---

## Phase 2: Gap-Driven Deep Interview

### Purpose

Extract the tacit knowledge that bridges the gap between what the resume currently says and what the JD frame requires. The user knows things about their experience that are relevant but unarticulated — your job is to pull it out.

### Step 1: Load the JD Frame

Do not re-parse the JD from scratch — use the frame built in Phase 1. Every requirement probed in Phase 2 should trace back to a frame entry. If a new gap surfaces during the interview that isn't in the frame, pause and update the frame first; don't let Phase 2 drift off-frame.

### Step 2: Build the Gap Map

Compare the JD frame against the user's current resume, career history, or whatever information is available. Classify each requirement into:

| Category | Meaning | Action |
|----------|---------|--------|
| **Direct match** | User's experience clearly covers this | Light polish — make the language sharper |
| **Bridgeable gap** | Not obvious from current materials, but could exist | **This is where the interview focuses** |
| **Large gap** | Weak or no visible connection | Probe deeply — the user chose to apply, so there may be a bridge you can't see yet |

Sort by gap severity. The largest, least-obvious gaps come first in the interview — that's where the most valuable tacit knowledge is hiding.

### Step 3: The Deep Interview

For each bridgeable or large gap, conduct a focused interview. This is not a surface-level "tell me about a time" exercise. You are trying to uncover:

- **Why the user believes this is bridgeable** — their mental model of the connection
- **Specific experiences** that map to this requirement, even if from a different domain
- **Transferable judgment** — decisions they made that demonstrate the competency the JD is looking for
- **The nuance** — not just "I did X" but "I did X because Y, which required understanding Z, and the result was W"

Interview protocol:

1. State the specific gap you're probing: "The JD asks for [X]. Your current materials show [Y]. Help me understand the bridge."
2. Ask open-ended first: "What in your experience connects to this?"
3. Then drill deeper: "Can you give me a specific situation?", "What was the decision you had to make?", "Why did you choose that approach over alternatives?"
4. Probe for transferability: "How does that judgment apply to what this role needs?"
5. Look for reframing opportunities: sometimes the experience IS there but framed in the wrong language or domain. Help the user see the translation.

#### Evidence Inventory Probes (지식 / Skill / 경험)

Sweep every gap across the three evidence classes — applicants under-report all three:

- **지식** — coursework, 법규/도메인 knowledge, the company's value-chain understanding, 자격증. Probe the learning path, the method, and the resulting insight: "which piece of this knowledge would competitors find hardest?"
- **Skill** — tools (Excel/SQL/ERP/BI), languages, presentation. Probe for skill-*applied-to-experience* moments, not possession.
- **경험** — any setting where the behavior matches the JD prong (인턴/공모전/알바/동아리/학생회 count: the material need not be 직무 경험 if the behavior transfers).

For every 경험, split it: what was the **standard process**, and what was **the user's own method**? The delta between the two is the resume material.

#### The Bridge-Claim Triad (for no-direct-experience prongs)

For any JD prong where the user has no direct experience, **silence is the worst possible answer** — it reads to the screener as "can't do it," not "didn't mention it." But the bridge must be honest (see Critical Principle 2). Every bridge claim must contain three parts:

1. **Acknowledge** — Use the JD's exact noun so ATS/HR pattern-matches. ("The role asks for SAP experience.")
2. **Analogue** — The closest honest experience you have, framed in terms of shared capability, not surface identity. ("I have not used SAP directly, but I have extracted and modeled financial data from enterprise DBs at scale using SQL and Python.")
3. **Transfer logic** — Specific reasoning for why the ramp-up from analogue to target is short. Not "I learn fast" (empty) but "because [shared structural characteristic] is the core skill, and the SAP-specific layer is [tool-level, not judgment-level]."

If any of the three parts is missing, the bridge collapses — two parts reads as excuse-making, one part is just reframing. If no honest analogue exists at all, note the gap as genuine and move on rather than manufacturing one. A genuine gap written down honestly loses fewer points than a manufactured bridge caught at interview.

**Keep going on each gap until one of these happens:**
- The user says "there's no more I can give"
- You have enough concrete material to write a compelling resume bullet
- It becomes clear this is genuinely a gap (not a hidden bridge) — in which case, note it honestly and move on

### Step 4: Synthesize into Resume Language

After the interview, take the extracted knowledge and:

1. Draft resume bullets that translate the tacit knowledge into the JD's language
2. Frame experiences using the target role's terminology and priorities (pull from the Phase 1 vocabulary map)
3. Quantify where possible — numbers, scope, impact
4. Ensure each bullet answers the implicit recruiter question: "So what? Why does this matter for THIS role?"
5. **Minimize 상황/역할 description** — spend the characters on judgment, action, and consequence, not context. Context is what interviews are for.

Every drafted bullet must then pass two checks before it's considered done:

#### The Impact-Chain Check

Every bullet must expose the full chain, not just the activity:

> **Activity → Result → Measurable Impact → Business consequence**

The most common failure is stopping at Activity or Result. "분석을 수행했다" is Activity. "리포트를 제출했다" is Result. Neither scores. **Measurable Impact** ("리포트 작성 시간 주 10시간 단축") and **Business consequence** ("경영진 의사결정 주기 단축") are what the reader actually evaluates.

Quantified impact is recommended, **not required**. The evidence hierarchy: ① 정량 (numbers) → ② 정성 (a named expert's/manager's *specific* positive feedback, peer recognition) → ③ 깨달음 (insight gained — weakest, last resort). When the number genuinely doesn't exist:
- Anchor a defensible proxy (participants count, frequency, project scale, dollar scope of the decision supported) rather than leave the link blank.
- Or use 정성 evidence as scoring evidence in its own right — stated plainly, not as an apology for a missing number.
- Or mark the bullet yellow and make a deliberate call to leave it — don't leave it missing by accident.

A weak chain marked yellow is a conscious tradeoff. A weak chain left accidentally is an unforced loss.

#### The Cliché Review

Certain phrases score **negative**, not neutral. They flag the writer as generic and actively suppress perceived competence. Before finalizing each bullet, check against the domain cliché registry:

- **PM / 경영기획 clichés**: "크로스 펑셔널 협업 조율," "이해관계자 관리," "프로세스 개선," "유관부서 커뮤니케이션," "전략적 사고," "데이터 기반 의사결정"
- **General resume clichés**: "results-oriented," "team player," "proactive," "passionate about," "dynamic environment"

If a bullet relies on cliché phrasing, replace it with the **underlying mechanism**: the specific decision made, the specific conflict resolved, the specific tradeoff quantified. Mechanism is un-clichéable because it's specific to your actual work.

#### The In-Seat Read-Through (final synthesis check)

After bullets pass the Impact-Chain Check and the Cliché Review, read the whole document once as the hiring manager, not as the editor. One question: does the career read as a **converging arc toward this seat** (Principle 0)?

- Does the top third set up a story the rest of the document keeps confirming?
- Is there a step that reads as a detour? Reframe it honestly — what it added that this role uses — or neutralize it. An unexplained detour breaks the destiny read.
- Does the 자소서/summary connect the company's current moment (Step 1b brief) to the applicant's trajectory in one thought — "this phase of the company needs exactly this arc"?
- Would this reader finish with the in-seat image? If not, the fix is selection and ordering, not adjectives.

#### KR 자소서 Assembly (when the application includes 자기소개서 / 지원동기 / 문항)

Korean 자소서 items are scored keyword-first — evaluator rubric ≈ **키워드 선정 40 / 키워드 증명 40 / 키워드 각인 20**. Draft in that order:

1. **Name the keyword before writing a sentence.** Identify what the question is designed to evaluate (평가의도) and pick the keyword that serves it — reflected against *this* company/직무, ideally carrying the user's own philosophy. **직무역량 is the highest-value keyword family**; a thin 경력 is survivable when a 직무 비전 + accurate 역량 keywords carry the answer.
2. **Prove it with 생각→행동→성과.** Pick the 소재 whose behavior concretizes the keyword's 평가지표 (필수); target-field relevance and self-initiated/공익 purpose are tie-breakers (선택). The 소재 need not be 직무 경험 — **behavior beats material**. Apply the same evidence hierarchy as the Impact-Chain Check.
3. **Imprint it.** Topic sentences the reader wants to read, support sentences that are easy to read. Keep a **500자 cut** of every answer for length-capped portals.

**지원동기/포부 assembly template** (drop-in):
> "(방식/아이디어)으로 (성과지표)를 높이는 것이 (이 회사의 사업 목표) 고려 시 중요합니다. 이를 위해 (역량 A/B/C)를 쌓았습니다. 특히 A는 (경로: 자격증/교육/책 등)로 강점화했고, (경험)에서 (자기만의 노하우)로 발휘해 (성과 — 수치 우선, 없으면 구체적 긍정 피드백)를 얻었습니다. 입사 후 (직무)에서 … 기여하겠습니다."

Build the master (원형) answers once, to 안정권, against the target's 3-yr 문항 + 인재상 — then tailor per company. Same reuse economics as the consolidated JD frame. The 직무 비전 assembly also doubles as the spoken 1분 자기소개 skeleton for interview prep.

Present drafts to the user for review. They know their experience best — iterate until the language feels both accurate and compelling.

---

## Phase 3: Multi-Filter Red-Team

### Pre-Red-Team: Kill / Protect / Build Sculpt Pass

Before running the reader filters, sort every resume element into three actions:

| Action | When to apply | What to do |
|--------|--------------|------------|
| **Kill** | Bullet doesn't map to any JD frame prong for this role, or actively signals a mismatch (e.g., marketing emphasis on a finance role) | Compress to a single line, reframe toward a relevant prong, or cut entirely. The goal is to remove noise that drains weight from signal. |
| **Protect** | Bullet directly evidences a core JD frame prong, already passes the Impact-Chain Check, and is cliché-free | Leave alone. Don't over-polish. |
| **Build** | Bullet has strong underlying material but weak presentation — missing impact chain, cliché phrasing, or wrong framing for this JD | Amplify the quant, reframe toward JD vocabulary, excise clichés |

**Critical move: reframe is a third option beyond keep/drop.** The same fact can serve different roles when framed differently. "38개국 글로벌 론칭 리드" framed as 마케팅/현지화 is irrelevant for a 경영기획 role — but reframed as "38개국 론칭 시 예산 기획 및 수익률 분석" it maps to a core 경영기획 prong. Before killing a prestige bullet, try reframing against the Phase 1 frame.

**Noise is a zero-sum cost on a page.** Keeping a prestige bullet that doesn't map doesn't score neutral — it actively de-emphasizes the bullets that do. Curation > accumulation.

Once Kill / Protect / Build is complete, run the sculpted draft through the filter pipeline below.

### Purpose

A resume doesn't face one reader — it passes through a pipeline of filters, each with different eyes, different priorities, and different kill criteria. This phase simulates each filter stage so the resume survives the full gauntlet. The model is based on Korean hiring practices (경력직 중심) with global ATS principles where applicable.

### The Filter Pipeline

The hiring process is a sequential funnel. Failing at any stage means the resume never reaches the next reader. Each filter has a different cognitive model:

| Filter | Who | What they care about | Kill speed |
|--------|-----|---------------------|------------|
| **F1: Machine** | ATS / 채용플랫폼 시스템 | Keyword match, hard criteria compliance | Instant |
| **F2: Testing** | (varies, sometimes skipped) | Technical competency proof | N/A for resume |
| **F3: HR Reader** | 인사팀 채용담당자 | Organizational fit, criteria compliance, red flags | 5-10초 initial, 평균 12분 deep |
| **F4: Team Reader** | 현업 실무진 (팀장, 대리~과장급) | "이 사람이 바로 일할 수 있나?" | Careful read |
| **F5: Executive** | 임원 | Cultural fit, long-term value, narrative coherence | Interview-linked |

Run the red-team in this order. The biggest volume filters (F1, F3) come first — if the resume dies there, optimizing for F4/F5 is wasted effort.

---

### Filter 1: Machine Screening (ATS / 시스템 필터링)

**Context:** 한국 기업의 69.2%가 어떤 형태로든 서류 필터링을 실시 (사람인 조사). 대기업/공기업은 거의 100%. 경력직은 신입보다 시스템 필터링 비중이 낮지만, 대규모 채용이나 플랫폼 지원 시에는 여전히 적용됨.

**What the machine checks:**

1. **Hard criteria match** — 필터링 항목 우선순위:
   - 경력 연수 (59%) — JD가 "3년 이상"이면 2년 11개월은 자동 탈락 가능
   - 전공 (54.2%) — 관련 전공 키워드가 학력란에 있는지
   - 나이 (50%) — 한국 특유. 공개적으로 안 쓰지만 내부적으로 필터링하는 기업 다수
   - 학력 (35.5%) — 학교명 또는 학위 수준
   - 외국어 점수 (22.9%), 자격증 (21.1%), 학점 (15.7%)

2. **Keyword density** — JD에 명시된 스킬, 도구, 방법론이 이력서 본문에 존재하는지. 최신 ATS는 NLP 기반 의미 매칭도 하지만, 안전하게 가려면 JD의 정확한 용어를 사용하는 것이 최선.

3. **Format compliance** — 사람인/잡코리아/원티드 등 플랫폼 양식을 쓰는 경우 자동 파싱됨. 자유 양식 제출 시 표, 이미지, 특수 레이아웃이 파싱을 깨뜨릴 수 있음.

**Red-team checklist for F1:**
- [ ] JD의 모든 hard requirement 키워드가 이력서에 1회 이상 등장하는가?
- [ ] 경력 연수가 명시적으로 계산 가능한가? (입사일~퇴사일 명기)
- [ ] 전공명이 학력란에 정확히 기재되어 있는가?
- [ ] 자격증/어학점수가 JD 기준을 충족하는가?
- [ ] 파일 포맷이 ATS 친화적인가? (PDF > Word, 표/이미지 최소화)
- [ ] 블라인드 채용 대상이면 편견 유발 정보가 제거되었는가?

**Verdict:** PASS (다음 필터로) / FAIL (자동 탈락 사유 명시)

---

### Filter 2: Testing (기술 테스트)

이 단계는 resume skill의 범위 밖. 다만 이력서에서 "테스트에서 증명할 수 있는 역량"을 암시하는 것은 유리할 수 있음 — 예: "Excel 모델링으로 [X] 분석 수행" 같은 구체적 도구 사용 경험은 코딩테스트/실무테스트 전에 신뢰감을 줌.

---

### Filter 3: HR Reader (인사팀 서류심사)

**Context:** 이 단계가 가장 많은 지원자를 탈락시키는 관문. 인사담당자는 조직 적합성과 기본 자격 요건을 봄. 현업의 전문성은 판단하지 못함 — 대신 "이 사람을 현업 면접에 올려도 되는가?"를 결정.

**한국 경력직 서류심사의 특성:**
- 이력서(정량 평가)가 전체의 50~70% 가중치. HR이 주로 평가.
- 자기소개서/경력기술서(정성 평가)가 30~50%. 부서별 전문가(대리~과장급)가 평가하는 경우가 많음.
- 경력직은 **경력기술 항목이 가장 중요한 평가 요소** (60.4%, 1위).
- 핵심 판단 기준: **"즉시 실무 투입 가능한 인재인가?"**

**What HR scans for (5~10초 초기 스캔):**
1. 직무 적합성 — 현재 직무와 지원 직무의 연결이 한눈에 보이는가?
2. 경력의 연속성 — 공백기가 있는가? 잦은 이직이 있는가? (한국에서 특히 민감)
3. 이직 사유 추론 — 이력서에 직접 안 써도 경력 흐름에서 HR이 추론함
4. 성장 궤적 — 직급/역할이 시간에 따라 성장하고 있는가?
5. 정보의 정확성/신뢰성 — 허위기재 의심 요소 (과장된 직급, 모호한 기간)

**What HR evaluates (12분 심층 검토, 경력직 평균):**
1. JD 요구사항과의 매칭도 — requirement별 evidence 유무
2. 경력기술서의 구체성 — 프로젝트명, 본인의 역할, 개선 결과, 수치
3. 조직 적합성 신호 — 기업문화에 맞는 커뮤니케이션 톤
4. 자소서와 이력서의 톤/내용 일치 — 불일치 시 리스크 큼

**Red-team checklist for F3:**
- [ ] 5초 스캔: 이력서 상단 1/3에서 "이 사람은 [직무]에 적합하다"가 읽히는가?
- [ ] 경력 흐름에 설명 안 되는 공백이 있는가?
- [ ] 이직이 잦아 보이는가? (한국 기준: 2년 미만 반복은 red flag)
- [ ] 경력기술서가 구체적인가? (프로젝트명 / 역할 / 결과 / 수치)
- [ ] "즉시 투입 가능" 느낌이 드는가? 아니면 교육이 필요해 보이는가?
- [ ] 자소서와 이력서 사이에 톤이나 내용 모순이 있는가?
- [ ] (자소서 포함 시) 문항별 rubric 점검: 평가의도에 맞는 키워드인가 → 생각/행동/성과로 증명되는가 → 중심문장이 간결하게 각인되는가? (선정 40 / 증명 40 / 각인 20)
- [ ] 직급 progression이 자연스러운가?
- [ ] "50개 이력서 중 이 사람을 면접에 올리겠는가?" — 솔직하게 답변

**Verdict:** PASS / MAYBE (보류) / FAIL (사유 명시)

---

### Filter 4: Team Reader (현업부서 실무진)

**Context:** HR을 통과한 이력서가 실제로 함께 일할 팀에게 전달되는 단계. 현업은 HR과 전혀 다른 눈으로 봄 — "이 사람이 내 옆자리에 앉으면 바로 일할 수 있는가?"가 핵심. 팀장급과 대리~과장급 실무자가 함께 검토하는 경우가 많음.

**What the team reads for:**

1. **실무 적합성 (Can they do the work?)**
   - 구체적인 프로젝트 경험이 우리 팀의 업무와 겹치는가?
   - 사용한 도구/방법론이 우리 팀의 스택과 일치하거나 근접한가?
   - 업무의 depth가 우리가 원하는 수준인가? (관리만 했나, 직접 hands-on 했나?)

2. **현업 이해도 (Do they get our world?)**
   - 업계 용어를 자연스럽게 쓰고 있는가?
   - 프로젝트 설명에서 현실적인 이해가 보이는가? (교과서적 vs. 실전)
   - "아, 이 사람 현업에서 부딪혀봤구나"라는 느낌이 있는가?

3. **포트폴리오/상세경력의 구체성**
   - 숫자로 검증 가능한 성과가 있는가?
   - 본인의 기여분이 명확한가? (팀 성과 vs. 개인 역할)
   - 난이도나 complexity가 느껴지는가?

4. **적응 속도 예측**
   - 우리 팀에 온보딩하면 몇 주 만에 독립적으로 일할 수 있을 것 같은가?
   - learning curve가 높아 보이는 영역이 있는가?

5. **비즈니스 매너 / 소통 능력 신호**
   - 경력기술서의 서술 방식에서 논리적 사고가 보이는가?
   - 이해관계자와의 협업 경험이 있는가?
   - 갈등 상황을 어떻게 다뤘는지 힌트가 있는가?

**Red-team checklist for F4:**
- [ ] 실무진이 읽었을 때 "이 사람 우리 일 해봤구나" 느낌이 드는가?
- [ ] 사용 도구/방법론이 팀 스택과 매칭되거나 근접한가?
- [ ] 프로젝트 설명이 교과서적이 아니라 실전에서 부딪힌 느낌인가?
- [ ] 본인 기여분이 팀 성과와 구분되어 있는가?
- [ ] "이 사람 오면 2주 안에 일 시킬 수 있겠다" vs. "3개월은 가르쳐야 하겠다" — 어느 쪽?
- [ ] 현업에서 쓰는 용어/프레임워크가 자연스럽게 녹아 있는가?
- [ ] hands-on 경험과 관리 경험의 비율이 지원 포지션에 적합한가?

**Verdict:** PASS (면접 추천) / WEAK (면접은 하되 우려사항 있음) / FAIL (사유 명시)

---

### Filter 5: Executive (임원 — 면접 연계)

**Context:** 임원면접은 이력서만으로 판단하는 것이 아니라 면접과 밀접하게 연결됨. 하지만 이력서가 면접에서의 narrative를 세팅하는 역할을 함 — 임원이 이력서를 보고 "이 사람한테 물어볼 것"을 정함. 따라서 이력서가 면접에서 유리한 질문을 유도하도록 설계되어야 함.

**What executives evaluate:**

1. **조직/문화 적합성 (Culture fit)**
   - 회사의 인재상과 일치하는가?
   - 한국 특유: "경력사원 중 회사 문화와 안 맞으면 1년 못 채우고 퇴사"가 현실. 임원은 이걸 가장 두려워함.
   - 이력서에서 협업, 리더십, 가치관을 읽을 수 있는 단서가 있는가?

2. **장기 가치 / 성장 잠재력**
   - 이 사람이 3~5년 후에도 회사에 있을 것 같은가?
   - 커리어 trajectory가 이 회사에서 성장하는 방향과 일치하는가?
   - 단순 이직이 아니라 "이 회사여야 하는 이유"가 암시되는가?

3. **리더십 vs. 팔로워십 (역할에 따라)**
   - 팀장급 이상: 리더십 경험과 성과가 보이는가?
   - 팀원급: 팀 내 협업과 자기주도적 업무 수행 경험이 보이는가?

4. **면접 질문 유도**
   - 임원은 이력서를 바탕으로 면접 질문을 만듦
   - 이력서에 "물어볼 수밖에 없는" 흥미로운 포인트가 있는가?
   - 반대로 — "건드리면 위험한" 약점이 노출되어 있지는 않은가?

**Red-team checklist for F5:**
- [ ] 이력서의 커리어 스토리가 "이 회사가 다음 단계"라는 흐름으로 읽히는가?
- [ ] 조직 문화 적합성의 단서가 있는가? (협업, 커뮤니케이션, 가치관)
- [ ] 이력서가 면접에서 유리한 질문을 유도하는가?
- [ ] 면접에서 곤란할 수 있는 약점이 이력서에 불필요하게 노출되어 있는가?
- [ ] "이 사람은 3년 후에도 여기 있겠다" 느낌이 드는가?
- [ ] 리더십/팔로워십 경험이 지원 포지션 레벨에 맞는가?

**Verdict:** INTERVIEW-READY (면접에서 유리한 위치) / RISKY (면접에서 방어해야 할 포인트 다수) / FAIL

---

### Phase 3 Summary Report

모든 필터를 통과한 후, 종합 보고서를 작성:

1. **Filter-by-filter verdict** — 각 단계의 PASS/FAIL과 사유
2. **Critical fixes** — 어느 한 필터에서라도 FAIL이면, 그 필터의 fix가 최우선
3. **Optimization opportunities** — PASS했지만 MAYBE/WEAK인 영역의 개선안
4. **Interview preparation seeds** — F5에서 도출된 "면접에서 물어볼 것"과 대비 방향. 제출 즉시 면접 드릴 시작 (대기 기간 = 연습 기간); 자료는 입으로 소리 내어 연습; 직무 비전 조립문 = 1분 자기소개 뼈대; "~은 부족하지만" 화법 금지 — 근거 제시형으로 대체
5. **Submit-or-iterate call** — Phase 0 triage의 window verdict 대비 판단. Red-team이 clean-enough면 제출 권고: 안정권 제출 후 개선이 100점 미제출보다 낫다 (Principle 3)
6. **Overall assessment** — "이 이력서가 최종 면접까지 갈 확률"에 대한 솔직한 판단

---

## Workflow Integration

When running all three phases together:

0. **Phase 0 — Acquire the JD, then triage.** Route by source: read it from the INPUT folder (Case A, default), or pull it from Notion and carbon-copy it into the project's OUTPUT_ROUGH folder via `/start-project` (Case B). Run the **Window & Coverage Triage** to set the pass budget. Only then start the frame.
1. **Phase 1 — Build the JD frame, including the Step 1b company-state brief.** Don't skip or rush — text-level parsing alone reproduces the generic-tailoring failure mode. If the user provides only the target JD, either pull 2–3 similar JDs yourself (if search is available) or ask the user to supply comparable listings. The frame output gets explicit sign-off from the user before advancing.
2. **Phase 2 — Gap-driven interview against the frame.** Synthesize into bullets that pass both the Impact-Chain Check and the Cliché Review.
3. **Sculpt pass — Kill / Protect / Build** across the draft before the red-team runs.
4. **Phase 3 — Multi-filter red-team** on the sculpted draft.
5. **Loop back.** If Phase 3 reveals new gaps, update the frame (Phase 1) first, then re-run targeted Phase 2 questions. Don't patch in place — a frame edit propagates cleanly, a local patch drifts.
6. Repeat until the red-team pass is clean **or the window says ship** — iteration is window-bounded (Principle 3). A clean-enough draft inside the window beats a perfect one outside it; post-submission time is for deep audits and interview drills.

## Output Expectations

- **Acquired JD (source of record)** — the JD read from INPUT (Case A), or carbon-copied from Notion into `11.OUTPUT_ROUGH/{project}/` alongside a Notion-extract file (status, deadlines, req ID, contacts, source URLs) when Case B applies
- **JD frame** — structured skeleton of what the role really asks for (archetype, prongs, hard/preferred filters, vocabulary map, signal intensity, implicit signals)
- Gap map (structured table) referencing the frame
- Interview notes / extracted knowledge (for user's reference)
- Resume bullets or full resume draft, each passing Impact-Chain Check + Cliché Review
- Kill / Protect / Build sculpt record — what was cut, kept, reframed
- Red-team assessment with filter-by-filter verdicts
- Prioritized fix list

### Rendering & QA Discipline (added 2026-07-23)

When the deliverable includes DOCX/PDF renders with page targets (e.g., KR 3p / EN 2p), QA is routed — the main session NEVER opens, renders, pages through, or visually reads a PDF (current Fable 5 near-infinite-loops on PDF reads; visual inspection is a Moravec task — trivial for humans, expensive for LLMs):

1. **Converge by construction**: fixed table widths, hard page breaks, autofit disabled. Exact page counts are a build property, not an inspection target — never iterate layout by render-inspect.
2. **Deterministic in-session checks only** (`D0`): `python scripts/qa_pdf_check.py <file.pdf> --pages N` (page count + text-layer/ATS probe), or a pypdf/pdfinfo one-liner where the script is absent.
3. **Visual QA** (clipping, overflow, table drift, fonts): one-off render with the user present → the user's 10-second eyeball (default). Batch or unattended → a Sonnet-class subagent, single pass over the render, returning STATUS + pinpointed issues only; page images never enter the main context; 2-round cap, then hand to the user.

Canonical rationale: fleet hub `14.LESSONS_LEARNED/lesson_visual-qa-task-routing_20260723_v1.md`; governance "Rendered-Output QA Protocol" (v2.1).

## Edge Cases

- **JD source is Notion**: Run Phase 0 Case B — search the three named databases (2026-06 Job Application KPI Tracker, Job Application Tracker, Job Description Analysis) plus free-text Notion search, confirm the entry if several match, carbon-copy the JD and context into the project's OUTPUT_ROUGH folder via `/start-project`, then start Phase 1 from the local copy.
- **JD not found in Notion**: If the search returns no clear match, show the user what you found and ask them to point to the exact Notion page or paste the JD — do not proceed on a guess.
- **User has no existing resume**: Start Phase 1 as usual, then Phase 2 as a full career interview, then build from scratch.
- **User only wants Phase 3**: Skip the interview, go straight to red-team. But still build a JD frame first (Phase 1) — the red-team needs the frame to evaluate relevance. Flag any gaps that Phase 2 could have filled.
- **User only provides the target JD, no similar JDs available**: Reduce Phase 1 to a single-JD frame, but note the risk — you may be calibrating to idiosyncratic language. Flag anything that feels unusual in the target JD as "unknown whether archetype or employer-specific."
- **Company-state research contradicts the JD's surface read**: trust the verified company state for emphasis decisions, but never contradict the JD's explicit asks. Note the tension in the frame and probe it at interview.
- **User supplies secondary-source research (NotebookLM-style syntheses, aggregator output)**: treat as leads, not facts — verify independently (Step 1b.3) before any of it enters an asset, and check for landmines the synthesis missed.
- **User resists Phase 1 as too slow**: Explicitly reject the shortcut. A shallow frame compounds into every bullet downstream — the cost of a weak frame is invisible but very real. The time saved here is paid back many times over in Phase 2 rework.
- **Multiple target JDs (applying to several similar roles)**: Build one consolidated frame that covers the shared core, then produce per-role variants that swap the employer-specific prongs and vocabulary.
- **Career pivot**: Pay extra attention to transferability framing in Phase 2. The bridge is likely through judgment and meta-skills, not direct technical overlap — which makes the Bridge-Claim Triad load-bearing.
- **User gets frustrated during interview**: They may feel like you're questioning their qualifications. Reaffirm: "I'm not doubting your fit — I'm trying to find the best way to show it on paper."

## Source Notes

2026-07-15 update: the Window & Coverage Triage, 3-yr posting-history axis, 우대 coverage rule (~35%), adjacency map, Evidence Inventory Probes, evidence hierarchy (정량→정성→깨달음), 상황/역할 minimization, KR 자소서 Assembly (40/40/20 rubric + 지원동기 template), and window-bounded iteration were folded in from `14.LESSONS_LEARNED/lesson_recruit-trend-2026-{research-strategy,application-prep,interview-prep}_20260715_v1.md` (강민혁, 2026 강남구 행복 일자리 박람회 deck) and `lesson_posting-closed-during-prep_20260714_v1.md`.

2026-07-23 update: Rendering & QA Discipline added per `lesson_visual-qa-task-routing_20260723_v1.md` — no model-side PDF inspection; deterministic checks + routed visual QA.

2026-07-27 update: Principle 0 (the In-Seat Test / destiny narrative), Phase 1 Step 1b (Company-State Research layer), the In-Seat Read-Through, and two edge cases added per Max's directive during the 삼표산업 전략기획 application (`apply_sampyo_strategic_planning`). Founding example: web verification of a foreign-president hire and a developer-transformation pivot re-read the posting's "영어가능자" requirement as a specific organizational need, not a generic language filter.
