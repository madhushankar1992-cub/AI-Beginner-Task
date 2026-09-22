# Implementation Plan: AI-Powered Restaurant Recommendation System

References: [problemstatement.md](problemstatement.md), [architecture.md](architecture.md)

Each phase produces a working, demo-able increment. Later phases assume earlier ones are done. Estimates assume one developer working solo; adjust for team size.

---

## Phase 0 — Project Setup

**Goal:** Empty-but-runnable skeleton, no business logic yet.

- Initialize repo structure per [architecture.md §6](architecture.md#6-suggested-project-structure) (`src/ingestion`, `src/api`, `src/recommendation`, `frontend`, `tests`, `data`, `docs`).
- Set up Python environment (`requirements.txt` or `pyproject.toml`): `fastapi`, `uvicorn`, `pydantic`, `pandas`, `datasets`, `groq`, `pytest`.
- Add `.env` / `.env.example` for `GROQ_API_KEY`; confirm secrets are gitignored.
- `git init`, initial commit, `.gitignore` (venv, `.env`, `data/*.parquet`, `node_modules`).
- Stub `src/api/main.py` with a `GET /health` endpoint returning `200 OK`.

**Deliverable:** `uvicorn src.api.main:app` runs; `/health` responds.
**Acceptance:** Fresh clone + install + run works with no manual steps beyond setting `GROQ_API_KEY`.

---

## Phase 1 — Data Ingestion

**Goal:** Clean, queryable restaurant dataset on disk.

- Write `src/ingestion/ingest.py`:
  - Load `ManikaSaini/zomato-restaurant-recommendation` via `datasets.load_dataset(...)`.
  - Inspect raw schema (log column names/types/sample rows) — confirm actual field names before mapping, since the target schema in architecture.md (`location`, `cuisine`, `cost_for_two`, `rating`, `name`, `tags`) is a best guess pending inspection.
  - Normalize into the target schema: `name`, `location`, `cuisine` (list), `cost_for_two`, `rating`, `tags`.
  - Handle missing/malformed values (drop unrated rows or impute; document the rule used).
  - Derive `budget_tier` (low/medium/high) via cost bucketing — pick thresholds from the actual cost distribution, not guessed numbers.
  - Write cleaned output to `data/restaurants.parquet`.
- Add a small ingestion report (row count in/out, dropped-row count, null counts) printed on run — makes data quality issues visible immediately.

**Deliverable:** Running `python -m src.ingestion.ingest` produces `data/restaurants.parquet`.
**Acceptance:** `pd.read_parquet("data/restaurants.parquet")` loads with no nulls in `name`/`location`/`rating`; `budget_tier` has exactly 3 values.

---

## Phase 2 — Data Store + Filtering (Integration Layer, structured half)

**Goal:** Deterministic, LLM-free filtering that turns user preferences into a candidate set.

- `src/api/store.py`: load `restaurants.parquet` into memory once at startup (module-level singleton or FastAPI lifespan hook).
- `src/recommendation/filters.py`:
  - `filter_restaurants(store, location, budget, cuisine, min_rating) -> DataFrame`
  - Match `location` (case-insensitive), `cuisine` (any-match against the list column), `budget_tier`, `rating >= min_rating`.
  - Cap result to top N (e.g., 30) by rating before returning.
- Unit tests (`tests/test_filters.py`) covering: exact match, no match (empty result), partial cuisine match, rating boundary, cap enforcement.

**Deliverable:** Filtering logic fully testable and tested without any network/LLM dependency.
**Acceptance:** `pytest tests/test_filters.py` passes; empty-filter case returns an empty DataFrame, not an error.

---

## Phase 3 — API Layer (Request/Response Contract)

**Goal:** Working `POST /recommendations` endpoint, LLM stubbed out for now.

- `src/api/models.py`: Pydantic `RecommendationRequest` (location, budget, cuisine list, min_rating, preferences list) and `RecommendationResponse` (recommendations list + summary), matching the shapes in [architecture.md §4.3–4.5](architecture.md#43-user-input--api-layer).
- `src/api/main.py`: `POST /recommendations` — validate input, call `filter_restaurants`, and for now return a **stubbed** response (e.g., top-N sorted by rating, no LLM call) so the contract is exercised end-to-end before wiring the LLM.
- Input validation: reject empty `location`, clamp `min_rating` to [0, 5].

**Deliverable:** Endpoint callable via curl/Postman, returns a valid (if not yet AI-ranked) response.
**Acceptance:** Manual request with a known-good location/cuisine returns a non-empty list; unknown location returns an empty/"no matches" response, not a 500.

---

## Phase 4 — Recommendation Engine (LLM Integration)

**Goal:** Replace the stub with real Groq-generated ranking and explanations.

- `src/recommendation/prompts.py`: build the system prompt (role, ranking criteria, "only use the provided list" grounding instruction, output contract) and the per-request user message (serialized candidate set + preferences), per [architecture.md §4.5](architecture.md#45-recommendation-engine-llm-call).
- `src/recommendation/engine.py`:
  - Call `client.chat.completions.create(...)` (Groq's OpenAI-compatible Chat Completions API) with `model="openai/gpt-oss-120b"`, `reasoning_effort="medium"`, `response_format={"type": "json_schema", "json_schema": {...}}` per the JSON schema in architecture.md. Keep the model name in `src/config.py` so switching to `qwen/qwen3-32b` (documented alternative) is a one-line change.
  - Validate the response against the expected schema.
  - On failure (API error, schema mismatch, timeout): fall back to the Phase 3 stub (sorted-by-rating list), and mark the response so the frontend can label it "sorted by rating" instead of "AI-recommended" (per [architecture.md §4.6](architecture.md#46-output-display)).
- Wire `engine.py` into the `/recommendations` endpoint, replacing the Phase 3 stub.
- Short-circuit: if the filtered candidate set is empty, skip the LLM call entirely and return "no matches" directly.

**Deliverable:** End-to-end real recommendations with AI explanations.
**Acceptance:** A real request returns ranked restaurants with non-generic, data-grounded explanations; killing network/API key still returns a usable fallback response instead of an error page.

---

## Phase 5 — Output Display (Frontend)

**Goal:** User-facing UI consuming the API.

- Scaffold frontend: Streamlit (per the [resolved decision in architecture.md §8](architecture.md#8-open-questions--decisions-needed) — pure Python, no Node.js/npm toolchain needed).
- Preference input form: location, budget, cuisine (multi-select), minimum rating, free-text/tag preferences.
- Results view: recommendation cards (Name, Cuisine, Rating, Estimated Cost, AI explanation) + summary line.
- States: loading, empty-results, error/fallback (labeled distinctly per §4.6), happy path.
- Wire to the `POST /recommendations` endpoint from Phase 4.

**Deliverable:** Clickable UI, no manual API calls needed to use the product.
**Acceptance:** A user can fill the form, submit, and see rendered recommendation cards; empty and fallback states are visually distinguishable from normal AI results.

**Addendum (post-Phase-5):** a second frontend, `frontend/web/`, was added later as a static HTML/CSS/JS UI (no build step) implementing a custom design built via a Claude Design canvas. It satisfies the same acceptance criteria independently of Streamlit and calls the same `POST /recommendations` endpoint via browser-side `fetch`, which required adding CORS support (`CORSMiddleware` + `CORS_ALLOW_ORIGINS` in `src/config.py`) to the API — not anticipated in the original Phase 3/4 API design, since Streamlit doesn't need it. Both frontends are independent and either can be run without the other.

---

## Phase 6 — Testing & Hardening

**Goal:** Confidence the system behaves correctly and degrades gracefully.

- `tests/test_engine.py`: mock the Groq client — test prompt construction, schema validation, and fallback-on-failure behavior without making real API calls.
- Small fixed eval set (5–10 representative queries) to sanity-check explanation quality doesn't regress after prompt changes — informal at this stage, formal eval tooling is a later enhancement.
- Basic error-path tests: malformed input, empty dataset match, LLM timeout/error simulation.
- Review cost/latency in practice: confirm candidate-set cap is effective, check actual token usage via `response.usage`, and confirm `reasoning_effort` is actually set to `"medium"` (not silently defaulting higher) on outgoing requests.

**Deliverable:** Test suite covering filters, API contract, and engine fallback paths.
**Acceptance:** `pytest` passes; a manual run with a bad/missing API key still produces a usable (fallback) response end-to-end.

**Addendum:** `tests/test_api.py` (mocked store/engine, FastAPI `TestClient`) covers the API contract directly — input validation, the no-match short-circuit, the AI/fallback response shapes — plus CORS preflight behavior (`frontend/web/`'s origin is allowed; an unlisted origin is not) added alongside Phase 5's addendum. 42 tests pass as of this writing.

---

## Phase 7 — Deployment

**Goal:** Runnable outside the dev machine.

- Dockerize the backend (single container, `Dockerfile` at repo root): ingestion runs at **build time** (bakes a deterministic `data/restaurants.parquet` into the image) rather than at container startup, so the running container never depends on Hugging Face being reachable — the trade-off is that `docker build` itself needs network access.
- Deployment target resolved (per [architecture.md §8](architecture.md#8-open-questions--decisions-needed)): **local Docker / self-hosted container.** Docker isn't installed on the dev machine used to build this project and no cloud account/credentials were available in that environment, so this phase produced and reviewed the `Dockerfile` and `.dockerignore` but did not build-test the image or perform a live deploy — do that (`docker build` / `docker run`, below) once Docker is available, and treat a cloud PaaS as a later option once credentials exist (the image is portable to one).
- `GROQ_API_KEY` is passed at `docker run` time via `-e` (or `--env-file`), never baked into the image — `.dockerignore` excludes `.env`/`.env.example` from the build context.
- Smoke test (once Docker is available): `docker build -t restaurant-recommender .` then `docker run -p 8000:8000 -e GROQ_API_KEY=... restaurant-recommender`, then run the same acceptance checks as Phase 5/6 against `http://localhost:8000`.

**Deliverable:** `Dockerfile` producing a runnable backend image; deployment target decision documented.
**Acceptance:** *Partially met in this environment* — the image is written to satisfy "fresh deploy from a clean environment serves working recommendations without manual post-deploy fixes" (build-time ingestion, env-var-only secrets, no host-specific paths), but this hasn't been verified with an actual `docker build`/`docker run` since Docker isn't installed here. Full acceptance requires that build+run smoke test.

---

## Phase Summary

| Phase | Focus | Depends on | Can demo? |
|---|---|---|---|
| 0 | Project skeleton | — | Health check only |
| 1 | Data ingestion | 0 | No (offline artifact only) |
| 2 | Filtering logic | 1 | No (unit-test level) |
| 3 | API contract (stubbed) | 2 | Yes — via curl/Postman |
| 4 | Real LLM recommendations | 3 | Yes — full backend |
| 5 | Frontend | 4 | Yes — full product |
| 6 | Testing & hardening | 5 | N/A (quality gate) |
| 7 | Deployment | 6 | Yes — live instance |

## Open Decisions Blocking Specific Phases

- ~~Frontend framework choice blocks the start of Phase 5.~~ Resolved: Streamlit.
- Data store choice (pandas vs. SQLite) should be confirmed before Phase 2 if dataset size or filter complexity turns out larger than expected during Phase 1's ingestion report.
- ~~Deployment target blocks Phase 7.~~ Resolved: local Docker / self-hosted container (build-test still pending — see Phase 7).
