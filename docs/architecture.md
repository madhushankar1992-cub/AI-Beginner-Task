# Architecture: AI-Powered Restaurant Recommendation System

Reference: [problemstatement.md](problemstatement.md)

This document defines the technical architecture for the Zomato-inspired restaurant recommendation service. Where a technology choice isn't dictated by the problem statement, a default is proposed and called out — treat these as a starting point to confirm, not a locked-in decision.

---

## 1. Goals & Non-Goals

**Goals**
- Combine a structured restaurant dataset with an LLM to produce ranked, explained recommendations.
- Keep the LLM's job bounded to reasoning/ranking/explaining over data we've already filtered — never let it invent restaurants or facts not present in the dataset.
- Fast enough for interactive use (single request/response cycle, sub-10s typical).

**Non-Goals (v1)**
- Real-time booking/ordering integration.
- User accounts, auth, or personalization history persistence.
- Live/streaming restaurant data (dataset is static, refreshed periodically offline).

---

## 2. High-Level Architecture

```
┌─────────────┐      ┌──────────────────┐      ┌───────────────────────┐      ┌────────────────────┐
│   Frontend   │─────▶│   API Layer       │─────▶│   Integration Layer    │─────▶│  LLM (Groq)         │
│ (Web UI)     │◀─────│ (FastAPI backend) │◀─────│ (filter + prompt build)│◀─────│  Chat Completions   │
└─────────────┘      └──────────────────┘      └───────────┬───────────┘      └────────────────────┘
                                                             │
                                                             ▼
                                                  ┌───────────────────────┐
                                                  │  Restaurant Data Store │
                                                  │ (preprocessed dataset) │
                                                  └───────────────────────┘
                                                             ▲
                                                             │
                                                  ┌───────────────────────┐
                                                  │   Data Ingestion Job   │
                                                  │ (Hugging Face dataset) │
                                                  └───────────────────────┘
```

**Request flow:**
1. User submits preferences via the frontend.
2. API layer validates input and calls the integration layer.
3. Integration layer filters the dataset down to a candidate set (structured, deterministic — no LLM involved yet).
4. Integration layer builds a prompt containing only the candidate set + user preferences, sends it to the LLM (Groq).
5. The LLM ranks, explains, and optionally summarizes.
6. API layer parses the structured LLM response and returns it to the frontend.
7. Frontend renders the recommendation cards.

---

## 3. Technology Stack (proposed)

| Layer | Choice | Why |
|---|---|---|
| Data ingestion | Python + `datasets` (Hugging Face) + `pandas` | Native loader for the target dataset; pandas for cleaning/filtering |
| Data store | Parquet/CSV file (or SQLite for query convenience) loaded into memory at startup | Dataset is static and small enough (~thousands of rows) that a full DB server is unnecessary |
| Backend API | Python + FastAPI | Async-friendly, typed request/response models via Pydantic |
| LLM provider | Groq via the official `groq` Python SDK (OpenAI-compatible Chat Completions API) | Fast open-weight inference; `openai/gpt-oss-120b` (primary) and `qwen/qwen3-32b` (alternative) both support structured JSON output and a `reasoning_effort` control |
| Frontend | Streamlit | Pure Python — no separate Node.js/npm toolchain needed; fastest path to a working demo UI (§8) |
| Deployment | Containerized (Docker), single service for MVP | Keeps ingestion, API, and prompt logic in one deployable unit initially |

If the team already has a preferred stack (Node backend, different frontend framework, existing DB), swap the equivalent component — the layer boundaries below don't depend on this specific stack.

---

## 4. Component Details

### 4.1 Data Ingestion Layer

**Responsibility:** Load the Zomato dataset from Hugging Face ([`ManikaSaini/zomato-restaurant-recommendation`](https://huggingface.co/datasets/ManikaSaini/zomato-restaurant-recommendation)), clean it, and persist a normalized version for the app to query.

- Run as an offline/startup script (`ingest.py`), not on the request path.
- Steps:
  1. Load via `datasets.load_dataset(...)`.
  2. Normalize field names (e.g., `location`, `cuisine`, `cost_for_two`, `rating`, `name`, `tags`).
  3. Handle missing values (drop or impute rating/cost where absent).
  4. Derive a `budget_tier` field (low/medium/high) by bucketing cost.
  5. Normalize cuisine strings (lowercase, split multi-cuisine lists into a list field).
  6. Write the cleaned table to disk (Parquet) as the artifact the API loads at startup.
- Re-run manually or on a schedule if the upstream dataset changes — not part of the request-time system.

### 4.2 Data Store

- In-memory pandas DataFrame (or SQLite table) loaded once at API startup.
- Indexed/filterable on `location`, `cuisine`, `budget_tier`, `rating`.
- No live writes — read-only for the application.

### 4.3 User Input / API Layer

**Responsibility:** Expose an HTTP endpoint that accepts preferences and returns recommendations.

`POST /recommendations`

```json
{
  "location": "Bangalore",
  "budget": "medium",
  "cuisine": ["Italian", "Chinese"],
  "min_rating": 4.0,
  "preferences": ["family-friendly", "quick service"]
}
```

- Pydantic model validates shape and types.
- Basic guardrails: reject empty location, clamp `min_rating` to [0, 5].

### 4.4 Integration Layer

**Responsibility:** Bridge structured data and the LLM. This is the layer that keeps the LLM grounded.

1. **Filter:** Query the data store for rows matching `location`, `cuisine` (any-match), `budget_tier`, and `rating >= min_rating`.
2. **Cap candidate size:** Take the top N (e.g., 20–30) candidates by rating to keep the prompt small and cheap — don't send the entire filtered set if it's large.
3. **Prompt construction:** Serialize the candidate set as compact structured text/JSON and build a prompt that:
   - States the user's preferences explicitly.
   - Provides the candidate list as the *only* source of truth ("only recommend restaurants from this list; do not invent restaurants or facts not present here").
   - Asks for a ranked top-K (e.g., top 5) with a short explanation per pick, plus an optional one-line overall summary.
   - Requests a structured JSON response (see §4.5) rather than free text, so the API layer can parse it reliably.
4. If the filtered set is empty, short-circuit and return a "no matches, try relaxing filters" response without calling the LLM at all — saves cost and avoids the model guessing.

### 4.5 Recommendation Engine (LLM Call)

**Model:** `openai/gpt-oss-120b` via Groq's OpenAI-compatible Chat Completions API (`client.chat.completions.create`), with `qwen/qwen3-32b` documented as a drop-in alternative model id if cost/quality/availability trade-offs favor it.

- **Structured output:** Use `response_format={"type": "json_schema", "json_schema": {...}}` with a schema like:

```json
{
  "recommendations": [
    {
      "name": "string",
      "cuisine": "string",
      "rating": "number",
      "estimated_cost": "string",
      "explanation": "string"
    }
  ],
  "summary": "string"
}
```

  This removes the need for fragile free-text parsing and guarantees the API layer gets a shape it can render directly.

- **Prompt design principles:**
  - System prompt: fixed instructions (role, ranking criteria, "ground truth only" constraint, output contract). This is the stable prefix and should stay identical across requests.
  - User message: candidate restaurant list (structured) + user preferences (the volatile part).
  - Explicitly instruct the model to weigh rating, budget fit, cuisine match, and any free-text preferences (e.g., "family-friendly") using the `tags`/description fields in the data.
- **Reasoning effort:** Both `openai/gpt-oss-120b` and `qwen/qwen3-32b` support a `reasoning_effort` parameter (`"low"` / `"medium"` / `"high"`). Default to `"medium"` — this is a ranking/summarization task, not deep multi-step reasoning, so don't raise it without evidence it's needed.
- **Cost/latency:** Groq's inference is fast enough that prompt-level caching isn't the primary lever here (unlike providers with explicit cache-read pricing); the main cost control is keeping the candidate-set cap small (§4.4) and not over-provisioning `reasoning_effort`.
- **Error handling:** If the LLM call fails (rate limit, timeout, auth error) or returns output that doesn't validate against the schema, fall back to a plain sorted-by-rating list from the filtered candidates so the user always gets a usable result.

### 4.6 Output Display

- Frontend renders each recommendation as a card: Name, Cuisine, Rating (stars), Estimated Cost, AI explanation.
- Show the optional `summary` above the list.
- Empty-state and error-state messaging (no matches / LLM unavailable → fallback list, clearly labeled as "sorted by rating" rather than "AI-recommended").

---

## 5. Data Flow Summary

```
Hugging Face dataset ──(ingest.py, offline)──▶ cleaned Parquet ──(startup load)──▶ in-memory store
                                                                                        │
User preferences ──▶ API layer ──▶ filter (pandas query) ──▶ candidate set (≤30 rows) ──┘
                                                                    │
                                                                    ▼
                                                     prompt (system + candidates + prefs)
                                                                    │
                                                                    ▼
                                                Groq (openai/gpt-oss-120b, structured output)
                                                                    │
                                                                    ▼
                                                     validated JSON ──▶ API response ──▶ UI cards
```

---

## 6. Suggested Project Structure

```
NXT LEAP/
├── docs/
│   ├── problemstatement.md
│   └── architecture.md
├── data/
│   └── restaurants.parquet          # output of ingestion, not the raw HF dataset
├── src/
│   ├── ingestion/
│   │   └── ingest.py                # loads + cleans HF dataset
│   ├── api/
│   │   ├── main.py                  # FastAPI app
│   │   ├── models.py                # Pydantic request/response schemas
│   │   └── store.py                 # loads/queries the data store
│   ├── recommendation/
│   │   ├── filters.py               # structured filtering logic
│   │   ├── prompts.py               # prompt templates
│   │   └── engine.py                # Groq API call + response parsing
│   └── config.py                    # model name, API key handling, thresholds
├── frontend/
│   └── app.py                       # Streamlit app (calls POST /recommendations)
└── tests/
    ├── test_filters.py
    └── test_engine.py               # mock the LLM call
```

---

## 7. Non-Functional Considerations

- **Cost control:** Cap candidate-set size sent to the LLM; use `reasoning_effort: "medium"` by default and measure before raising it.
- **Latency:** Single non-streaming Groq call per request is acceptable for this use case (interactive, not agentic/multi-turn); Groq's inference speed gives headroom here even without streaming. Switch to streaming only if response times become noticeable in the UI.
- **Reliability:** Deterministic filtering is the source of truth for *which restaurants exist* in the result; the LLM only ranks/explains within that set — this bounds hallucination risk to explanations, not fabricated restaurants.
- **Testability:** Filtering logic is pure and unit-testable without hitting the LLM. LLM-dependent tests should mock the Groq client or use a small fixed eval set to check explanation quality doesn't regress.
- **Config/secrets:** `GROQ_API_KEY` via environment variable, never hardcoded.

---

## 8. Open Questions / Decisions Needed

- ~~Frontend framework: React (production-grade) vs. Streamlit (fastest to demo)?~~ **Resolved (Phase 5): Streamlit** — pure Python, no Node.js/npm toolchain needed on top of the existing backend stack.
- Data store: flat file/pandas vs. SQLite vs. a real DB — depends on expected dataset size and whether filters need to get more complex later.
- ~~Deployment target (local only, cloud VM, containers on a PaaS)?~~ **Resolved (Phase 7): local Docker / self-hosted container** — see `Dockerfile` at the repo root. Docker isn't installed on the dev machine used for this project and no cloud account/credentials were available, so the image was written and reviewed but not build-tested in this environment; a cloud PaaS target remains a reasonable later choice (the container is portable to one) once credentials are available.
- Whether "additional preferences" (family-friendly, quick service) map to actual dataset fields/tags or are purely inferred by the LLM from restaurant descriptions — affects how much the integration layer vs. the LLM does the matching.
