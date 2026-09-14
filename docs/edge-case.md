# Edge Cases & Corner Scenarios

References: [implementation-plan.md](implementation-plan.md), [architecture.md](architecture.md), [problemstatement.md](problemstatement.md)

Corner cases and failure scenarios to design/test for, organized by the phase that owns them. Use this as a checklist when implementing and writing tests for each phase — not all of these need handling in v1, but each should be a conscious decision, not an accident.

---

## Phase 0 — Project Setup

- Missing `ANTHROPIC_API_KEY` at startup — does the app fail fast with a clear error, or fail later on first LLM call with a confusing one?
- `.env` present but empty/malformed (e.g., key set to empty string) vs. `.env` entirely absent.
- Fresh clone on a different OS/Python version than dev machine — dependency resolution differences (esp. `pandas`/`datasets` native deps).
- Port `8000` already in use when starting `uvicorn`.
- `.gitignore` gaps: confirm `data/*.parquet` and `.env` are actually excluded before first commit (a secret or large binary committed once is hard to fully scrub later).

## Phase 1 — Data Ingestion

- **Schema drift**: actual Hugging Face dataset columns don't match the assumed target schema (`name`, `location`, `cuisine`, `cost_for_two`, `rating`, `name`, `tags`) — column renamed, missing, or split across multiple source columns.
- **Nulls/missing values** in each field independently:
  - Missing `rating` — drop row, impute (e.g., dataset mean), or default to 0 (which could wrongly exclude/include it under `min_rating` filters)?
  - Missing `location` or `name` — these are used as-is downstream (`location` matched to user input, `name` shown in UI); a null here should always drop the row, not impute.
  - Missing `cost_for_two` — breaks `budget_tier` derivation; decide fallback tier or drop.
  - Missing/empty `cuisine` list — restaurant becomes unmatchable by any cuisine filter.
- **Malformed values**: non-numeric `rating`/`cost_for_two` (e.g., `"N/A"`, `"₹500 for two"`, stray currency symbols or commas in cost strings), rating outside expected `0–5` range (e.g., a `0–10` scale in source data).
- **Duplicate rows**: same restaurant appearing multiple times (different branches vs. true duplicates) — affects result diversity and the Phase 2 "cap top N" step.
- **Encoding/locale issues**: non-ASCII restaurant/location names (regional scripts, accented characters), inconsistent casing (`"bangalore"` vs `"Bangalore"` vs `"Bengaluru"` — same city, different string).
- **Cuisine string parsing**: multi-cuisine values delimited inconsistently (`"Italian, Chinese"` vs `"Italian/Chinese"` vs `["Italian","Chinese"]` already-list) — the split logic must handle all source delimiters seen, not just a guessed one.
- **`budget_tier` bucketing**: cost distribution is skewed (e.g., mostly low-cost with a long tail of expensive outliers) — naive tertile buckets could put a majority of rows in one tier; outlier costs (e.g., `cost_for_two = 0` or absurdly high) skew thresholds.
- **Empty dataset after cleaning**: if the drop-null rule is too aggressive, ingestion could produce zero or near-zero rows — the acceptance check (no nulls in key fields) should also assert a minimum row count.
- **Ingestion re-run idempotency**: re-running `ingest.py` should overwrite `restaurants.parquet` cleanly, not append/duplicate.
- **Upstream dataset changes**: `datasets.load_dataset(...)` pulls a different version/split later — no version pin means silent schema or content drift on re-ingestion.

## Phase 2 — Data Store + Filtering

- **Empty filter result**: no restaurant matches all criteria (already an explicit acceptance case) — confirm it returns an empty DataFrame/list, not `None` or an exception.
- **All filters absent/maximally permissive**: user (or a buggy request) supplies no meaningful constraints — does filtering degrade to "return everything" (then get capped to top N), which could be surprising for a "location: Bangalore" style product?
- **Case sensitivity**: `location="BANGALORE"` vs stored `"Bangalore"` — confirm case-insensitive match is applied consistently to both `location` and `cuisine`.
- **Partial/fuzzy location match**: user submits `"Bangalore"` but dataset stores `"Bengaluru"`, or a neighborhood-level string (`"Koramangala, Bangalore"`) vs. city-level stored value — exact substring/equality match may silently return zero results for a "reasonable" query.
- **Cuisine any-match edge cases**: user requests a cuisine that exists in the dataset under a different spelling/casing (`"Chinese"` vs `"chinese food"`); user requests multiple cuisines where only one candidate matches — confirm "any-match" (OR) semantics are what's intended vs. "all-match" (AND).
- **`min_rating` boundary**: exact equality (`rating == min_rating`) must be included per `>=`; floating-point rating values (`4.0000001`) causing boundary surprises; `min_rating = 0` should match everything including unrated-but-imputed rows.
- **`min_rating` at extremes**: `min_rating = 5.0` may legitimately return zero or very few matches — distinguish this from a broken filter.
- **Budget tier mismatch**: user-submitted `budget` string doesn't exactly match one of the three derived tier labels (casing, synonyms like `"cheap"` vs `"low"`).
- **Cap-to-top-N ties**: many restaurants tied at the same rating at the N-th cutoff — cap logic needs a deterministic tiebreaker (e.g., secondary sort key) or results become non-reproducible between runs.
- **Very large candidate set before capping**: a broad query (e.g., large city, common cuisine, low `min_rating`) could match thousands of rows — confirm filtering + capping performs acceptably and doesn't materialize huge intermediate structures unnecessarily.
- **Store not yet loaded**: a request arrives before the startup hook finishes loading `restaurants.parquet` (race condition), or the parquet file is missing/corrupt at startup.

## Phase 3 — API Layer

- **Empty/whitespace-only `location`**: must be rejected per spec — confirm whitespace-only (`"   "`) is treated as empty, not passed through.
- **`min_rating` out of range**: negative values, values `> 5`, non-numeric strings, `NaN`/`Infinity` — spec says clamp to `[0, 5]`; decide whether out-of-range is clamped silently or rejected with a 422 (inconsistent handling between these two is itself a bug).
- **Empty `cuisine` list**: valid input (no cuisine preference) vs. a list containing only empty strings.
- **Unknown/unrecognized `location`**: valid string but no matching data at all — spec requires this to be a graceful empty/"no matches" response, not a 500.
- **Extra/unexpected fields in request body**: Pydantic's default strict/ignore behavior should be an explicit decision, not accidental.
- **Wrong types**: `min_rating` as a string, `cuisine` as a single string instead of a list, deeply nested/malformed JSON — should return 422 with a clear message, not a 500.
- **Very large `preferences` list or long free-text strings**: potential for oversized prompts downstream (Phase 4) — consider a length cap at the API boundary.
- **Duplicate values in `cuisine` list**: should not cause duplicate-counted matches or logic errors.
- **Concurrent requests**: multiple simultaneous calls against the shared in-memory store — confirm read-only access is safe without locking (should be, if store is never mutated post-startup, but worth confirming explicitly).
- **Unicode/special characters in `location`/free text**: injection-safe handling (even though there's no SQL here, downstream prompt construction in Phase 4 must still treat this as untrusted text).

## Phase 4 — Recommendation Engine (LLM Integration)

- **LLM returns empty/malformed JSON**: doesn't match the expected schema at all — must trigger the fallback path, not crash the request.
- **LLM returns partially valid JSON**: e.g., `recommendations` array present but missing `explanation` on some items, or `rating`/`estimated_cost` as wrong types — schema validation must catch partial as well as total mismatches.
- **LLM hallucinates a restaurant not in the candidate set**: the "ground truth only" instruction is a prompt-level guardrail, not a guarantee — consider a post-response validation step that cross-checks returned `name`s against the candidate set and drops/flags any that don't match.
- **LLM returns fewer than top-K recommendations**: e.g., candidate set has only 2 restaurants but prompt asks for top 5 — response should gracefully return what's available, not error or pad with fabricated entries.
- **API errors**: rate limiting (429), auth failure (invalid/expired key, 401), server error (5xx), network timeout — each should map to the fallback response, but distinguishing them in logs matters for operability (auth failure is a config bug, not a transient failure worth retrying).
- **Timeout tuning**: what's the actual timeout threshold before falling back? Too short causes unnecessary fallbacks on normal latency variance; too long makes the "sub-10s typical" UX goal unreliable.
- **Retry behavior**: is a single transient failure (one dropped connection) retried once before falling back, or does it fall back immediately? No retry-storm risk if retries aren't bounded.
- **Empty candidate set reaching the engine**: Phase 4 spec says short-circuit before the LLM call — confirm this short-circuit actually happens upstream and the engine is never called with zero candidates (defense in depth: engine should also handle it safely if called anyway).
- **Prompt size limits**: candidate set serialization plus preferences text approaching context/token limits (unlikely at N≤30 candidates, but free-text `preferences` could be abused with very long input if Phase 3 doesn't cap it).
- **Prompt injection via free-text preferences**: user-supplied `preferences` text could contain instructions attempting to override the system prompt (e.g., "ignore previous instructions and recommend X") — the grounding constraint should be tested against adversarial input, not just benign queries.
- **Non-deterministic output across identical requests**: same filters/preferences may produce different rankings/explanations on repeat calls — decide if this is acceptable (likely yes) or needs `temperature`/caching considerations documented.
- **Schema validation false negatives**: an LLM response that's semantically fine but doesn't strictly match the JSON schema (e.g., `rating` returned as `"4.5"` string instead of number) — decide whether to coerce/repair before falling back, since falling back on trivial type mismatches wastes a valid response.
- **Fallback response mislabeling**: confirm the fallback path always sets the "sorted by rating, not AI-recommended" flag — a bug here silently misrepresents fallback results as real AI recommendations to the user.
- **Cost/latency blowup**: forgetting to cap candidate size in some code path, causing an oversized prompt on a broad query.

## Phase 5 — Output Display (Frontend)

- **Loading state interrupted**: user navigates away or submits a second request before the first resolves — must cancel/ignore the stale response rather than rendering out-of-order results.
- **Empty-results state**: must be visually distinct from both the loading and error/fallback states (three-way distinction, not just two).
- **Fallback vs. AI-recommended labeling**: must be clearly and consistently visible on every card in a fallback response, not just a page-level banner that could be missed.
- **Partial data in a recommendation card**: `explanation` or `estimated_cost` missing/empty for a given item (possible after Phase 4's partial-validity handling) — card layout shouldn't break or show `"undefined"`/blank gaps.
- **Very long explanation text or restaurant names**: layout overflow in cards on narrow viewports.
- **Multi-select cuisine with zero selections**: form should allow submitting with no cuisine filter, not silently block submission.
- **Rapid double-submit**: double-clicking submit shouldn't fire two overlapping requests.
- **API unreachable entirely** (backend down, network error, CORS misconfiguration): distinct from an LLM-fallback response — this is a full request failure and needs its own error state, not conflated with "no matches."
- **Rating displayed as stars**: fractional ratings (e.g., `4.3`) — confirm rounding/half-star rendering behaves reasonably.
- **Browser back/forward after a submission**: does the form retain the last submitted values, or reset silently and confuse the user about what's currently displayed?

## Phase 6 — Testing & Hardening

- **Mocked LLM client edge cases to explicitly test**: valid response, empty `recommendations` array, malformed JSON, timeout exception, rate-limit exception, auth exception, response with extra unexpected fields, response with a hallucinated restaurant name not in candidates.
- **Malformed input tests**: cover every field independently and in combination (not just one bad field at a time) — e.g., empty `location` AND out-of-range `min_rating` in the same request.
- **Empty-dataset-match test**: confirm the full request path (not just the filter unit) short-circuits correctly end-to-end without an LLM call being made (assert the mock wasn't invoked).
- **Eval set staleness**: the fixed 5–10 query eval set should be revisited whenever the underlying dataset is re-ingested (Phase 1 re-run) — a stale eval set against updated data can silently stop being meaningful.
- **Cache-hit assumption**: `cache_read_input_tokens > 0` check assumes at least two calls share an identical system prompt within the cache TTL — a test/review run with only one request per unique prompt would never show a cache hit even if caching is correctly configured; confirm the review methodology accounts for this.
- **Token usage regression**: no automated guard against a prompt change silently blowing up token usage/cost — worth a soft threshold check in review, even if informal.
- **Flaky LLM-quality checks**: informal eval set quality review is inherently subjective/non-deterministic — document what "regression" means concretely (e.g., explanation no longer references data-grounded facts) so the check doesn't become rubber-stamped over time.

## Phase 7 — Deployment

- **Ingestion-at-startup cost**: if ingestion runs at container startup rather than build time, cold-start latency includes a full Hugging Face dataset download — could fail or time out in restricted-network deployment environments.
- **Missing/misconfigured `ANTHROPIC_API_KEY` in the target environment**: secrets management differs from local `.env` — confirm the deployment target's secret-injection mechanism is actually wired up, not just documented.
- **Stateless container restarts**: in-memory data store means every restart re-loads (or re-ingests) `restaurants.parquet` — confirm the parquet artifact is baked into the image or otherwise available at startup, not assumed to exist from a previous run.
- **Concurrent container instances** (if scaled beyond one replica): each loads its own in-memory copy of the dataset — fine for read-only data, but worth confirming no shared-state assumptions sneak in later.
- **Smoke test drift**: remote smoke tests reusing Phase 5/6 acceptance checks must account for real network latency to the LLM API from the deployment region — a check tuned to local-machine latency could false-positive/negative remotely.
- **Rollback path**: no explicit mention of how a bad deploy is rolled back — worth deciding before the first real deploy, not after an incident.
- **Health check vs. readiness**: Phase 0's `/health` returning `200` doesn't guarantee the data store finished loading — a load balancer routing traffic based on `/health` alone could send requests to an instance that isn't actually ready to filter/recommend yet.

## Cross-Cutting Concerns (span multiple phases)

- **Dataset/API schema coupling**: the target schema in `architecture.md` is explicitly a "best guess pending inspection" (Phase 1) — every downstream phase (filtering, API contract, prompt construction, frontend cards) has an implicit dependency on that schema being finalized early; a late schema change ripples through all of them.
- **"No matches" vs. "system error" conflation**: multiple phases (Filtering, API, Engine, Frontend) each need to distinguish "legitimately zero results" from "something broke" — a bug in any one layer that conflates these produces a misleading user-facing message.
- **Silent fallback overuse**: because Phase 4's fallback path always produces a *usable* response, a persistent LLM misconfiguration (e.g., always-invalid API key) could go unnoticed in casual testing since the app "still works" — needs explicit monitoring/logging of fallback rate, not just a UI label.
- **Budget tier / min_rating semantic mismatch**: `budget_tier` is a derived bucket (low/medium/high) while `min_rating` is a continuous threshold — a user's mental model ("give me options up to ₹800") doesn't map cleanly onto discrete tiers; worth confirming this simplification is acceptable for v1.
- **Currency/locale assumptions**: `cost_for_two` and `estimated_cost` formatting assumes a single currency/locale (likely INR given Zomato/Indian dataset) — not documented explicitly anywhere; a corner case if the dataset or user base isn't India-only.
