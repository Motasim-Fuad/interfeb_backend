# Deploying on a VPS (with the local AI)

Everything — API, PostgreSQL, Redis and the local AI (Ollama) — runs from the same
`docker compose` files you use on your laptop, so what works locally works on the server.

## 1. Server size

| What | Minimum | Recommended |
|---|---|---|
| RAM | 8 GB (use the `3b` models) | **16 GB** (use the `7b` models) |
| CPU | 4 vCPU | 6–8 vCPU |
| Disk | 40 GB | 80 GB (models ≈ 5–12 GB, plus photos and database) |
| OS | Ubuntu 22.04 / 24.04 | |

No GPU is needed. With a Gemini key a mill-report photo is read in seconds; without it (or when
Gemini is down / over its free limit) the local model reads it in 1–8 minutes on CPU, in the
background, so the Dashboard never freezes.

## 2. One-time setup

```bash
# Docker
curl -fsSL https://get.docker.com | sh
# Code
git clone <your repo> interfab-backend && cd interfab-backend
cp .env.example .env
```

Edit `.env` (at least these):

```
ENV=production
JWT_SECRET_KEY=<random 40+ characters>
ADMIN_SETUP_SECRET=<random 20+ characters>
ALLOWED_ORIGINS=https://dashboard.your-domain.com
MEDIA_BASE_URL=https://api.your-domain.com/media
# Mill report photos: Gemini (fast). Key from https://aistudio.google.com
VISION_LLM_API_KEY=<your Gemini key>
# Local fallback when Gemini is unavailable; 16 GB server reads handwriting better with 7b
VISION_FALLBACK_MODEL=qwen2.5vl:7b
```

## 3. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose run --rm ollama-pull        # downloads the AI models once (several GB)
```

Check: `curl http://localhost:8000/health` → `"status":"ok"`.

Put Nginx / Caddy in front for HTTPS (`api.your-domain.com` → `localhost:8000`).
Ollama only listens on `127.0.0.1`, so it is never reachable from the internet.

## 4. Updating

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Changing a model name in `.env`? Run `docker compose run --rm ollama-pull` again, then
`docker compose restart api`.

## 5. Switching AI later

Both readers use any OpenAI-compatible endpoint, so no code change is needed:

* Photos (main): `VISION_LLM_BASE_URL`, `VISION_LLM_MODEL`, `VISION_LLM_API_KEY` (Gemini by default)
* Photos (fallback): `VISION_FALLBACK_*` (local Ollama by default, used automatically when the main one fails)
* FabriQ chat: `ASSISTANT_LLM_*` — empty by default: the built-in engine answers instantly and exactly.
Excel / CSV mill reports never need the AI.
