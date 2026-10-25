---
name: write-analytical-style
description: Write or rewrite feedback, reviews, coaching pages, and report findings in an analytic, numbers-first style. Each claim is stated plainly and backed by a number and its exact place in the source. No honorific praise narration, empty adjectives, or metaphors. Use when the user says "분석적으로", "수치로 말해", "형용사 말고 숫자", "말투 고쳐", "'하셨습니다' 말투 말고", "write analytically", or rejects a phrase with "이게 무슨 말이야?". This skill rewrites text; for an audit-only report of metaphors in an existing document, use de-mannerism.
---

# Write in analytic style

Feedback and findings are read by someone who has to check them and act on them. Every sentence should point to something the reader can verify in the source.

Origin: a coaching review (Codeit Excel course, 2026-10) where the coach rejected sentence after sentence that said nothing checkable ("올바른 질문을 스스로 세우셨습니다", "분석의 뼈대를 갖추셨습니다", "정확히 보려고"), asking: "가급적 형용사 대신에 수치로 얘기하면 안돼? 우리 데이터분석가잖아." The pages he approved after that review are the reference for this style (see "Reference pages").

## When to use
- Writing or rewriting coaching pages, review comments, score comments, audit findings, report summaries.
- The user calls a sentence unclear, asks for an analytic tone, or asks for numbers instead of adjectives.
- Not for marketing copy, personal letters, or fiction.

## Rules

### 1. Evaluative claims carry a number and a location
Each evaluative claim has three parts: what is true, the number that shows it, and where it is in the source (file, sheet, cell, column, report section).
- ✗ "매출 증가 원인을 잘 분석하셨습니다."
- ✓ "매출 증가가 주문 건수 증가에서 비롯되었다는 점을 수치로 확인했습니다. 보고서 3장의 표에서 주문당 매출은 6개월간 20,092\~20,186원으로 큰 변화가 없고, 2월 → 3월 매출 +41.4%, 주문 수 +41.3%입니다."

If no number exists, use a count or a concrete observable ("결과 시트 6개에 수식, 피벗, 차트가 하나도 없습니다"). Plain statements of absence need no number ("5장의 전략 제안에는 방향만 있고, 구체적인 실행 방안이 없습니다").

### 2. Plain declarative endings, no honorific praise narration
Describe what was done or what is in the file with plain endings (~했습니다, ~있습니다, ~없습니다; ~없음, 재현 불가 in table cells). The person may be the implied subject. Do not narrate or judge their work with the honorific -시- ("~하셨습니다", "~해 주셨습니다").
- ✗ "결측치 처리를 꼼꼼히 진행하셨습니다."
- ✓ "결측치를 사례마다 근거에 따라 대체했고, 대체한 값은 모두 3주차 정제 데이터와 일치합니다."

Honorifics stay in direct requests and in references to the reader's own things: "~해 보세요", "보완해 주세요", "제출하신 파일", "이유가 있다면 궁금해요".

### 3. Facts instead of judgement words and metaphors
A word whose only job is to judge (탄탄, 올바른, 정확히, 신중한, 꼼꼼히, 돋보입니다, 깔끔합니다, 적절합니다, solid, careful) gets replaced by the fact that made you want to judge. A metaphor that needs decoding (뼈대, 선을 긋다, 성장 엔진, earns its keep) gets replaced by the literal statement.

A judgement word after the concrete fact is acceptable ("+0.98건과 −0.02건의 변화를 한 표에 제시한 점이 좋습니다"). A judgement word instead of the fact is not ("비교 설계가 좋습니다").

Phrases the user rejected, and the rewrites that replaced them:

| Rejected | Why it failed | Rewrite |
| --- | --- | --- |
| 멤버십 효과를 확인하는 올바른 질문을 스스로 세우셨습니다 | "질문" is not what was built; "올바른" is empty | 가입 고객의 주문 수 변화(B5: 가입 직전 주 → 가입한 주, +0.98건)와 계속 미가입 고객의 변화(C5: 이웃한 두 미가입 주, −0.02건)를 한 표에 제시했습니다. '모든 고객의 주문이 늘었는가, 가입 고객만 늘었는가'를 비교하려는 접근입니다. |
| 성장 여부를 판단하고 원인을 쪼개 보는 분석의 뼈대를 갖추셨습니다 | metaphor; the reader cannot tell what was done | Rule 1 ✓ example |
| 가입 효과를 정확히 보려고 비교 방법을 직접 설계하셨습니다 | "정확히" has no measurable meaning; honorific praise | delete "정확히 보려고"; state the comparison and its two bases |
| 판단마다 근거를 찾고, 데이터가 말해 주지 않는 것을 먼저 구분하는 분석 태도가 탄탄합니다 | two claims in one headline; "탄탄" is empty; the example under it supported only the first claim | split into two bullets: the 결측치 bullet (Rule 2 ✓), and "보고서에 '방문 고객만 기록됨', '멤버십의 인과 효과가 아님'이라는 두 가지 데이터 한계를 명시했습니다" |
| 데이터로 말할 수 없는 것은 먼저 선을 그으셨습니다 / 결론을 어디까지 믿어도 되는지가 분명합니다 | metaphor; vague | same as the second half of the row above |

### 4. One claim per bullet, and the support must match it
Each bullet opens with one bold sentence that is the claim, followed by 1–3 supporting sentences or a short sub-list. Every supporting sentence or sub-item is evidence for the bold sentence directly above it. If it supports a different claim, move it under that claim or cut it. Never pack two strengths or two problems into one sentence.

### 5. State comparison bases exactly
When comparing numbers, say how each was computed and over what unit (customers, customer-weeks, pairs). If two numbers were computed differently, say so. Never write "같은 시기", "same period", or "same group" unless it is true.
- ✓ "+0.98건(가입한 주 − 직전 주)과 −0.02건(이웃한 두 미가입 주의 차이)은 산출 기준이 달라 직접 비교할 수 없습니다."

### 6. Label where each number came from, and compute it correctly
- Numbers from the source: cite the location.
- Numbers you recalculated: label them ("코치 재계산 기준", "recalculated").
- Statistical results: give the test, n, the effect in the reader's unit, and the p-value reading ("p값이 0.05보다 훨씬 작아 유의수준 5%에서 유의", "p값 약 0.12로 유의하지 않음"). Quote an exact p only when you computed it with the same method as the reader's tool (paired t-test → t-distribution, not a normal approximation) and recomputed it once.
- Keep three things apart: change within a group, difference between groups, and cause. One significant group and one non-significant group do not show that the groups differ significantly, and neither shows a cause.

### 7. Verify negative and counting claims too
Statements about what was not done ("일괄 0으로 채우지 않고", "t검정이 없어") and counts ("결측치 3건") are claims. Confirm them in the file before writing. If the file documents cases or categories rather than individual cells, say "사례" or "종류", not "건".

### 8. Point to the reader's own file
Instructions that touch a file name the file, sheet, and columns, and say why those columns ("제출하신 `delivery_app_data.xlsx`의 `Data` 시트에서, 마지막 열인 R열 오른쪽의 빈 S열과 T열 2행에 머리글을 입력합니다"). Each formula gets one sentence on what it returns.

### 9. Quotes are verbatim
Quote only text you have read in the source, character for character. When shortening, keep the words in order and do not merge two quotes. When quoting the user, keep hedges such as "가급적".

### 10. Keep the author's vocabulary and punctuation
When editing someone else's draft, keep their term choices (e.g., 평균 주문 금액(AOV), 활성 고객, 대응표본 t검정(paired t-test)) unless they are wrong. Unifying terminology is a proposal to the user, not a silent change. The same applies to their existing punctuation.

### 11. Terms the reader can follow
Explain a term the reader may not know in plain words in the same sentence, or drop it. A common pattern: the Korean term with the English term or abbreviation in parentheses on first use. Excel menu paths go in code formatting as the reader's Excel shows them (`데이터 > 데이터 분석 > t-검정: 쌍체비교`).

### 12. Punctuation and markup
- In Korean text you write, avoid the 가운뎃점 (·) where possible; list with commas ("수식, 피벗, 차트"). Do not sweep existing 가운뎃점 out of someone else's text (Rule 10).
- In rich-text targets (Notion, Docs), use the target's bold markup. Literal `**` or escaped `\*\*` will show as asterisks.
- In markdown files, escape a lone tilde in ranges (`20,092\~20,186원`).

## Process
1. **Read the source first.** Collect the numbers, cell locations, and quotes you will use. Do not write a claim you cannot point to.
2. **Draft** using the rules.
3. **Self-check each sentence.** Can the reader verify it at a named location? Is a judgement word standing in for a fact? Is there a metaphor? Is it one claim, with matching support? Are comparison bases stated? Is each number labelled as source or recalculated?
4. **Fact-check before handing over.** Re-open the source and confirm each number, count, and quote. Report anything you could not verify instead of keeping it.
5. **If the user rejects a phrase,** explain in one or two plain sentences what it was trying to say, then give the numbers-first rewrite. Do not defend the phrase.

## Reference pages
Before drafting, open one page the user has already approved in this style and match its section order, bullet shape, and table columns. For Codeit coaching pages, these are the delivered pages under the user's Notion page "Coaching Excel (Codeit 코드잇)"; use the most recent one written after this skill existed. If you cannot find one, ask the user which page to follow.

## Coaching pages
- **Requirements first, in the analysis.** Decide the requirements check before writing anything else (필수 vs 가이드라인, ○/△/× or ✅/△/❌). It decides which strengths, gaps, and coaching items you write. On the page, keep the template's section order. In the approved page the 📋 요구사항 이행 여부 section comes after 🎯 핵심 코칭 and opens with a bold 요약 (how many 필수 items need work, then 가이드라인 gaps), followed by one table per week (구분 | 항목 | 이행 | 확인 내용).
- **✅ and ⚠️ stay overarching.** In the approved page this was two bullets per week in each section. The detailed how-to goes in 🎯 핵심 코칭.
- **🎯 핵심 코칭 shape.** Each item is a numbered list. Each step opens with a bold action sentence, then gives the menu path in code formatting, the formulas with one sentence on what each returns, and the setting values. Close with the expected numbers (a results table) and, where the student must write it up, a quote block showing one report sentence.
- **Score comments.** State the fact first. If there is a gap, add it after "다만".
- **Gaps.** State the gap as a fact, then ask whether there was a reason ("혹시 t검정을 생략하거나 결과를 값으로만 붙여 넣으신 이유가 있다면 궁금해요", "혹시 누락된 파일이 있을까요?"). Do not label the gap a mistake.
