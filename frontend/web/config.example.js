// Copy this file to config.js and set the deployed backend's URL before
// deploying frontend/web/ to Vercel (or any static host other than your own
// machine) — see docs/deployment-plan.md.
//
// config.js IS meant to be committed once it has a real value: Vercel's
// GitHub integration deploys whatever's in the repo, so a gitignored file
// would simply be missing from the deployed site. This is fine — the
// backend's URL isn't a secret (the browser calls it directly, so it's
// visible in every visitor's Network tab regardless); only GROQ_API_KEY
// needs to stay out of git, and that lives on Railway, never in this file.
//
// Local dev doesn't need this file at all: app.js falls back to
// http://127.0.0.1:8000 when window.SAVORA_API_URL is unset, so
// index.html's <script src="./config.js"> 404-ing locally (before you've
// created config.js) is expected and harmless.
window.SAVORA_API_URL = "https://<your-railway-app>.up.railway.app";
