# Evaluation Plan: AI-Powered Restaurant Recommendation System

References: [implementation-plan.md](implementation-plan.md), [architecture.md](architecture.md), [edge-case.md](edge-case.md)

This document defines how recommendation *quality* is checked — separate from functional correctness (covered by `tests/test_filters.py`, `tests/test_engine.py`, and the edge cases in `edge-case.md`). Those verify the system behaves correctly; this verifies the LLM's output is actually good, and stays good as prompts/models change.

Scope is intentionally informal for v1, per [Phase 6](implementation-plan.md#phase-6--testing--hardening): a small fixed query set reviewed manually. Formal eval tooling (automated scoring, larger sample sizes, CI-gated regression checks) is a later enhancement, not a Phase 6 requirement.

---

## 1. What This Eval Set Is For

- Catch regressions when `prompts.py` or `engine.py` changes (wording tweaks, schema changes, model/effort changes).
- Catch regressions when the underlying dataset is re-ingested (Phase 1 re-run) — new data can expose prompt weaknesses that didn't show up on the old data.
- Give a repeatable, low-effort sanity check before a deploy (Phase 7), not a comprehensive quality audit.

**Out of scope for this eval set:** load/performance testing, adversarial/red-team prompt injection testing (tracked separately — see [edge-case.md](edge-case.md#phase-4--recommendation-engine-llm-integration)), UI/UX testing.

---

## 2. Query Set

Pick 5–10 queries once real ingested data (Phase 1 output) is available — exact values below are placeholders to replace with real `location`/`cuisine` values confirmed present in `restaurants.parquet`. Each query should exercise a different scenario so the set as a whole covers the system's range, not just the happy path repeated.

| # | Scenario | Example request shape | Why it's in the set |
|---|---|---|---|
| 1 | Common city + common cuisine, generous budget | `location: <major city>, cuisine: [<popular cuisine>], budget: medium, min_rating: 3.5` | Baseline happy path — large candidate set, should produce confident, well-differentiated picks |
| 2 | Narrow filter, small candidate set | `location: <smaller city/area>, cuisine: [<niche cuisine>], min_rating: 4.5` | Tests explanation quality when there are only a few (e.g., 2–4) candidates to choose from |
| 3 | Multiple cuisines requested | `cuisine: [<cuisine A>, <cuisine B>]` | Tests that ranking respects "any-match" semantics without ignoring one cuisine entirely |
| 4 | Free-text preference that maps to a real tag/description signal | `preferences: ["family-friendly"]` | Tests whether explanations meaningfully use `preferences` against `tags`, or just restate rating/cost (see §3, grounding) |
| 5 | Free-text preference with no clear dataset signal | `preferences: ["good for a first date"]` | Tests graceful handling when the preference can't be strongly grounded in available fields — explanation shouldn't fabricate signal that isn't there |
| 6 | Budget-constrained | `budget: low, min_rating: 4.0` | Tests that cost/budget fit is actually reflected in ranking and explanation, not just rating |
| 7 | High `min_rating`, likely small result set | `min_rating: 4.7` | Boundary case near the top of the rating scale — tests behavior when very few restaurants qualify |
| 8 | Zero-match query | `location: <city not well-represented or absent>` | Confirms the short-circuit "no matches" path is taken (no LLM call) — not a quality check on the LLM, but belongs in the same review pass since it's run alongside the others |
| 9 | No cuisine/preference filters, location only | `location: <city>` (cuisine/preferences omitted) | Tests behavior on a maximally broad query — large capped candidate set (top N by rating) |
| 10 | Repeat of #1 on a second run | Same request as #1 | Tests run-to-run consistency (see §4) — not identical output, but comparable quality/grounding |

Keep the set fixed once chosen (don't reshuffle queries between review rounds) so quality comparisons over time are apples-to-apples. Revisit the set only when the dataset changes materially (Phase 1 re-run) or a gap is found during review.

---

## 3. Quality Criteria (what "good" means per query)

For each query's response, check:

1. **Grounding** — every recommended restaurant `name` appears in the actual filtered candidate set for that query (cross-check against `filter_restaurants` output, not just "sounds plausible"). Any name not in the candidate set is a hallucination and an automatic fail, regardless of how good the explanation reads.
2. **Explanation specificity** — explanations reference concrete fields (cuisine, cost, rating, matched tags/preferences) rather than generic filler ("this is a great choice!") that could apply to any restaurant. A reviewer should be able to tell two explanations apart even if the ratings are similar.
3. **Preference usage** — when `preferences` (free-text) are supplied, the explanation should reflect them where the data supports it (query #4), and should not invent support where the data doesn't (query #5) — e.g., claiming "great for a first date" with nothing in `tags`/description backing that up is a fabrication, not a feature.
4. **Ranking sanity** — within the returned top-K, higher-ranked picks shouldn't be obviously worse than lower-ranked ones on the stated criteria (e.g., a 3.6-rated pick ranked above a 4.8-rated pick from the same candidate set, with no explanation for why) unless the explanation justifies the tradeoff (e.g., better budget fit).
5. **Schema conformance** — response validates against the JSON schema in [architecture.md §4.5](architecture.md#45-recommendation-engine-llm-call) without needing the fallback path (except query #8, which is expected to skip the LLM entirely).
6. **Summary line quality** (when present) — accurately reflects the returned set (doesn't reference cuisines/constraints absent from the actual results).
7. **No unrequested invention** — recommendation count matches what was asked for (or is honestly short, per [edge-case.md](edge-case.md#phase-4--recommendation-engine-llm-integration), if the candidate set itself was smaller than top-K) — never padded with restaurants outside the candidate set to hit a target count.

A query **passes** if all of 1, 5, and 7 hold (these are correctness/grounding, non-negotiable) and 2–4, 6 are "reasonable" by the reviewer's judgment (these are quality, subjective). Track failures on 1/5/7 separately from 2/4/6 — the former are bugs, the latter are prompt-tuning opportunities.

---

## 4. Run-to-Run Consistency (query #10)

LLM output is non-deterministic by default — this is expected, not a bug. The consistency check is **not** "identical output twice," it's:

- The *set* of recommended restaurants is reasonably stable (mostly overlapping) between runs of the same query against the same candidate set, not wildly different top-5s each time.
- Grounding and schema-conformance (§3.1, §3.5) hold on every run, not just most runs.
- Explanation quality doesn't degrade on repeat calls (e.g., caching the system prompt shouldn't cause the model to reuse a stale/lower-quality cached completion — it shouldn't, since only the prefix is cached, but this is the check that would catch it if something were misconfigured).

If results vary wildly between identical runs, that's worth flagging even though the system isn't strictly "broken" — it may indicate the prompt is under-constraining the ranking criteria.

---

## 5. Review Process

1. Run all 10 queries against the current deployed prompt/engine code (via the API directly, not through manual UI clicks, so results are easy to record and diff).
2. For each response, score against §3's checklist. Record pass/fail on grounding+schema+count (hard fail) separately from a rough quality judgment (good/acceptable/weak) on the subjective criteria.
3. Run when:
   - `prompts.py` or `engine.py` changes.
   - The model/effort/thinking config changes.
   - `restaurants.parquet` is regenerated from a fresh ingestion run.
   - Before a Phase 7 deploy, as a sanity gate alongside the functional smoke test.
4. A **regression** is: any hard-fail criterion (§3.1/3.5/3.7) newly failing on a query that previously passed, or a clear, reviewer-agreed drop in explanation quality across multiple queries (not one subjective wobble on one query).
5. On a regression: do not ship the change. Revert or fix the prompt/config, re-run the full set (not just the failing query — a prompt fix can improve one query and quietly regress another).

This process is manual and judgment-based by design at this stage — keep review notes (even informal, e.g. a scratch doc per run) so "quality drifted slowly over several small prompt changes" is catchable in retrospect, since no single review would show a big enough drop to flag on its own.

---

## 6. Operational Metrics to Check Alongside Quality

Per [Phase 6](implementation-plan.md#phase-6--testing--hardening), capture these on the same review pass since they use the same set of live calls:

- **Token usage** (`response.usage`) per query — flag if a prompt change causes a significant jump unexplained by candidate-set size.
- **Prompt caching** — confirm `cache_read_input_tokens > 0` on the 2nd+ query in a review run (same system prompt reused). Note: this only shows up starting with the *second* call in a session within the cache TTL — the first call of any review run is expected to show a cache write, not a cache hit; don't misread that as caching being broken.
- **Latency** — each call's response time, checked against the "sub-10s typical" goal in [architecture.md §1](architecture.md#1-goals--non-goals).
- **Fallback rate** — how many of the 10 queries hit the Phase 4 fallback path unexpectedly (excluding query #8, which is expected to). A non-zero unexpected fallback rate during review is itself a finding worth investigating (see [edge-case.md — Silent fallback overuse](edge-case.md#cross-cutting-concerns-span-multiple-phases)).

---

## 7. Future Enhancements (not required for v1 Phase 6)

- Automated scoring (e.g., a second LLM call as judge) to reduce manual review burden as the query set grows.
- Larger, sampled query set instead of a fixed 10 (covers more of the input space, catches narrower regressions).
- CI-gated eval run on every prompt/engine change instead of manual triggering.
- User-facing feedback loop (thumbs up/down on recommendations) to source real queries and quality signal instead of hand-picked ones — note this would be new scope beyond the v1 non-goals in [architecture.md §1](architecture.md#1-goals--non-goals) (no personalization/history persistence), so treat as a post-v1 discussion, not an implicit Phase 6 task.
