# Deployment Plan: Railway (Backend) + Vercel (Frontend)

References: [architecture.md](architecture.md), [implementation-plan.md](implementation-plan.md)

This supersedes Phase 7's "local Docker / self-hosted" deployment target with two managed hosts: the FastAPI backend on **Railway** (built from the existing `Dockerfile`), and the static `frontend/web/` UI on **Vercel**. Streamlit (`frontend/app.py`) is out of scope here — it's a local/demo tool, not meant to be deployed publicly.

Two code changes were needed to make this actually deployable, made alongside this plan rather than left as manual steps to remember:
- **`Dockerfile`**: the `CMD` now binds to `$PORT` when set (`uvicorn ... --port ${PORT:-8000}`), falling back to 8000 locally. Railway assigns its own port at runtime; the old hardcoded `--port 8000` would have silently failed to accept traffic on Railway's actual port.
- **`frontend/web/config.js`** (new, created from `frontend/web/config.example.js`): the only way for a static, build-step-free frontend to know a deployed backend's URL. `app.js` already read `window.SAVORA_API_URL` with a localhost fallback; nothing there needed to change, only new file introduced.

Everything else (`railway.json`, this doc) is new. Verified locally: full test suite (42/42) and a full browser pass against the live backend still succeed with `config.js` absent (the expected local-dev state — see below).

---

## Why the order matters

The two services need to know about each other before either is fully working:
- The frontend needs the backend's URL (`config.js`) before it can call it.
- The backend needs the frontend's URL (`CORS_ALLOW_ORIGINS`) before a browser is allowed to call it.

Both URLs are only assigned *after* each service's first deploy. So this is a four-phase sequence, not a single step — deploy backend, wire frontend to it, deploy frontend, then go back and open the backend's CORS to the frontend's now-known URL.

```
Phase 1: Railway ── deploy backend ──▶ https://<app>.up.railway.app
                                              │
Phase 2:                    write it into frontend/web/config.js, commit, push
                                              │
Phase 3: Vercel ── deploy frontend ──▶ https://<app>.vercel.app
                                              │
Phase 4:          set CORS_ALLOW_ORIGINS on Railway to that URL, redeploy
```

---

## Prerequisites

- A Railway account and a Vercel account (both have free/hobby tiers sufficient for this project).
- This repo pushed to a GitHub repository — both platforms deploy from a connected git repo by default (their CLIs are an alternative; not required).
- Your Groq API key (the same one in your local `.env`).
- Neither platform's CLI is required for this plan; everything below uses each platform's web dashboard. If you prefer their CLIs (`railway` / `vercel`), the equivalent commands are noted inline.

---

## Phase 1 — Deploy the backend to Railway

### 1.1 Push to GitHub

Railway deploys from a git branch. Commit the changes this plan introduced (`Dockerfile`, `railway.json`, `frontend/web/config.example.js`, `frontend/web/index.html`) and push to GitHub if you haven't already.

### 1.2 Create the Railway project

In the Railway dashboard: **New Project → Deploy from GitHub repo** → select this repo. Railway detects `railway.json` and `Dockerfile` at the repo root automatically and builds from them — no other configuration needed for the build itself.

The build runs `RUN python -m src.ingestion.ingest` (per the `Dockerfile`), which downloads the dataset from Hugging Face during the build. This needs the build environment to reach `huggingface.co`, which Railway's standard build environment has by default. Expect the build to take a few minutes longer than a typical Python image build because of this download + the pandas cleaning pass.

### 1.3 Set environment variables

In the service's **Variables** tab:

| Variable | Value | Notes |
|---|---|---|
| `GROQ_API_KEY` | your real key | **Secret.** Same as local `.env`. Never commit this. |
| `CORS_ALLOW_ORIGINS` | *(leave unset for now)* | Comes from `src/config.py`'s default (`http://localhost:3000,http://127.0.0.1:3000`) until Phase 4, when you'll set it to the real Vercel URL. Leaving it unset doesn't break the backend — it just means no browser can call it cross-origin until Phase 4. |
| `GROQ_MODEL` | *(optional)* | Only set if switching off the default `openai/gpt-oss-120b` (e.g. to `qwen/qwen3-32b`, per `src/config.py`). |

`PORT` does **not** need to be set manually — Railway injects it automatically, and the `Dockerfile`'s `CMD` already reads it.

### 1.4 Health check

Already configured declaratively in `railway.json` (`deploy.healthcheckPath: "/health"`), pointing at the `GET /health` endpoint that's existed since Phase 0. Railway uses this to know when a deploy is actually ready, not just started.

### 1.5 Deploy and verify

Trigger the deploy (Railway does this automatically on push once the project is connected). Once it's live, get the service's public URL from the Railway dashboard (**Settings → Networking → Generate Domain** if one isn't assigned yet) — it'll look like `https://<something>.up.railway.app`.

Verify with the same checks used throughout local development:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<your-app>.up.railway.app/health
# expect: 200

curl -s -X POST https://<your-app>.up.railway.app/recommendations \
  -H "Content-Type: application/json" \
  -d '{"location":"Whitefield","cuisine":["north indian"],"min_rating":4}'
# expect: a 200 JSON response (source "ai" or "fallback" — see architecture.md §4.5-4.6
# for why a fallback here is expected behavior, not a bug, especially on repeated calls)
```

If `/recommendations` returns 200 with real data, the backend deploy is done. Cross-origin browser calls will still fail until Phase 4 — that's expected, not a bug to chase yet.

---

## Phase 2 — Point the frontend at the deployed backend

Locally:

```bash
cp frontend/web/config.example.js frontend/web/config.js
```

Edit `frontend/web/config.js` and replace the placeholder with the real Railway URL from Phase 1:

```js
window.SAVORA_API_URL = "https://<your-app>.up.railway.app";
```

Commit and push it. Unlike `.env`, this file **is** meant to be committed — see the comment in `config.example.js` for why (the backend URL isn't a secret; it's called directly from every visitor's browser).

---

## Phase 3 — Deploy the frontend to Vercel

### 3.1 Project settings

In the Vercel dashboard: **Add New → Project → Import** this GitHub repo. Since `frontend/web/` is a subdirectory (not the repo root) and has no build step, set:

- **Root Directory:** `frontend/web`
- **Framework Preset:** Other
- **Build Command:** *(leave empty)*
- **Output Directory:** *(leave as default — `.` / the root directory itself)*

No `vercel.json` is needed for this — it's a handful of static files (`index.html`, `styles.css`, `app.js`, `options.js`, `config.js`) with no routing beyond the single page, and Vercel's CDN already handles cache invalidation per-deployment without extra config.

### 3.2 Deploy and verify

Deploy. Vercel assigns a URL like `https://<something>.vercel.app`. Open it — the page will load (HTML/CSS/fonts don't need the backend), but submitting the form will fail with the frontend's own "Could not reach the recommendation service" error banner, because Railway doesn't yet allow this origin. That's the expected state at the end of this phase — confirmed visually, not just assumed, is the point of Phase 4's smoke test.

---

## Phase 4 — Close the loop: allow the frontend's origin on Railway

### 4.1 Set CORS_ALLOW_ORIGINS

Back in Railway's **Variables** tab, set:

```
CORS_ALLOW_ORIGINS=https://<your-app>.vercel.app
```

Multiple origins are comma-separated (matching `src/config.py`'s parsing) — useful if you later add a custom domain on Vercel: `https://<your-app>.vercel.app,https://your-custom-domain.com`.

Note this only covers the **production** Vercel URL. Vercel also creates a unique preview URL for every branch/PR deploy; those won't be able to call the API unless added too. For a project this size, treat preview deploys as frontend-only visual checks and do full end-to-end testing against production, rather than trying to keep a dynamic preview-URL allowlist in sync.

### 4.2 Redeploy

Railway redeploys automatically when a variable changes (or trigger it manually from the dashboard). This is a variable-only change — it does not rebuild the Docker image, so it's fast.

### 4.3 End-to-end smoke test

Open the real Vercel URL in a browser and submit the form (matching the acceptance check used throughout local development in this project):

- A known-good location (e.g. "Whitefield") returns rendered recommendation cards, not an error.
- An unrecognized location shows the empty-state banner, not a crash.
- Check the browser console — no CORS errors.

This is the same shape of check as `docs/eval.md`'s methodology and the Playwright-based browser tests used throughout local development (see `tests/test_api.py`'s CORS preflight tests for the equivalent automated check against `localhost:3000`) — just run once, by hand, against the real deployed URLs.

---

## Ongoing operations

**Redeploying after code changes:** both platforms redeploy automatically on push to the connected branch. A backend change rebuilds the Docker image (re-running ingestion); a frontend-only change (anything under `frontend/web/`) only affects Vercel's build.

**Rotating `GROQ_API_KEY`:** update it in Railway's Variables tab and redeploy. Same as the local `.env` rotation process, just in Railway instead of on disk.

**Groq rate limits under real traffic:** `src/recommendation/rate_limiter.py` already guards against exceeding Groq's published limits for `openai/gpt-oss-120b` (8K tokens/minute is the binding constraint — see `src/config.py`'s comment). On a live, multi-visitor deployment this means concurrent users will see the sorted-by-rating fallback more often than in solo local testing, since the whole deployment shares one Groq account's rate budget. This is working as designed (a graceful degradation, not an outage) but is worth knowing before treating a burst of "AI ranking unavailable" banners as a bug report.

**Rollback:** both Railway and Vercel keep prior deploys and support one-click rollback to a previous deployment from their dashboards — faster than reverting and re-pushing a git commit.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Frontend loads but every search shows "Could not reach the recommendation service" | Phase 4 not done yet, or `CORS_ALLOW_ORIGINS` doesn't exactly match the Vercel URL (scheme + host must match exactly; no trailing slash) |
| Browser console shows a CORS error naming a specific origin | That origin isn't in `CORS_ALLOW_ORIGINS` — likely a preview-deploy URL (see 4.1) or a custom domain not yet added |
| `config.js` 404s in the deployed frontend (visible in Network tab) | Phase 2 was skipped, or `config.js` wasn't committed — remember it must be a tracked file, not gitignored (this project doesn't ignore it, see `.gitignore`) |
| Railway build fails at the ingestion step | Transient Hugging Face availability issue during build, or the build environment lacks network egress — retry the build; this is the documented trade-off of build-time ingestion (see `Dockerfile`'s top comment) |
| `/health` returns 200 but `/recommendations` always returns the fallback | `GROQ_API_KEY` missing/invalid on Railway, or the account's Groq rate limit is currently exhausted — check Railway's deploy logs for the `"Falling back to sorted-by-rating response: ..."` line `src/api/main.py` logs on every fallback, which names the actual cause |

---

## Out of scope for this plan

- **Custom domains** — both platforms support adding one under their respective dashboards; not covered here since none is currently owned for this project.
- **Streamlit (`frontend/app.py`)** — remains a local-only tool per this plan's opening note; deploying it (e.g., to Streamlit Community Cloud) would be a separate, later decision.
- **CI/CD beyond each platform's built-in git-push deploys** — no separate pipeline is set up; this is sufficient for the project's current scale.
