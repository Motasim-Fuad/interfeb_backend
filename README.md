# Interfab QC Backend

FastAPI + PostgreSQL + Redis backend for Interfab Fabric Inspection. Feature-based structure
mirroring the Flutter app's `features/` layout — every module is self-contained (`models.py`,
`schemas.py`, `routes.py`), so adding Sewing/Cutting/Production later is copy-the-pattern, not
a rewrite.

Tested end-to-end already (admin login → PLM → Fabric Inspection roll+defect+calculation →
GSM before/after-wash upsert → master inspection_report linking) — this is working code, not
just scaffolding.

---

## 1. Do you need Docker on your Mac? — Yes, one-time install

Your Mac already has Python set up — that's enough for editing/running the code directly if you
ever want to, but **Postgres and Redis need to run somewhere**, and Docker is what gives you both
without installing/configuring them natively on macOS (which is messier and harder to reproduce
on a server later). So:

**You need to install Docker Desktop once.** Everything else (Python, the API server, migrations)
runs *inside* containers that Docker manages — you won't need to separately install PostgreSQL or
Redis on your Mac at all.

### Installing Docker Desktop on Mac
1. Go to https://www.docker.com/products/docker-desktop/
2. Download the version for your chip — click the Apple logo → About This Mac to check if you have
   **Apple Silicon (M1/M2/M3/M4)** or **Intel**, then download the matching `.dmg`.
3. Open the `.dmg`, drag Docker to Applications, launch it once from Applications.
4. Wait for the whale icon in the menu bar to say "Docker Desktop is running" (first launch takes
   a minute or two).
5. Verify from Terminal:
   ```bash
   docker --version
   docker compose version
   ```
   Both should print a version number — if so, you're ready.

That's the only new thing to install. Your existing Python setup is untouched and not required
for running this project (though it's there if you ever want to run `uvicorn` directly without
Docker for quick debugging).

---

## 2. Project setup (one-time)

```bash
cd interfab-backend
cp .env.example .env
```

Open `.env` and change at least `JWT_SECRET_KEY` to any random string. Leave the rest as-is for
local development — the defaults already match `docker-compose.yml`.

---

## 3. Run it

From the `interfab-backend` folder:

```bash
docker compose up --build
```

First run will:
1. Pull/build the Postgres, Redis, and API images
2. Wait for Postgres to be healthy
3. Run `alembic upgrade head` (applies migrations — but see step 4, you need to generate the
   first migration once)
4. Start the API with hot-reload at **http://localhost:8000**

Leave this terminal running. Open a **second terminal** for the next steps.

---

## 4. First-time database migration (only needed once, before first `up`)

Alembic is wired up, but you need to generate the *first* migration from the models before
`docker compose up` can apply it. With the containers running (from step 3):

```bash
docker compose exec api alembic revision --autogenerate -m "initial schema"
docker compose exec api alembic upgrade head
```

After this, every time you add a new column/table to any module's `models.py`, repeat just these
two commands to create and apply a new migration — this is how the schema evolves safely for the
next 10 years without ever hand-writing SQL.

---

## 5. Check it's alive

Open in your browser:
- **http://localhost:8000/health** → `{"status": "ok", ...}`
- **http://localhost:8000/docs** → full interactive Swagger UI — every endpoint, try-it-out included

---

## 6. Create your first admin (dashboard login)

`.env` has `ADMIN_SETUP_SECRET` - registering ANY admin (first one or later ones) requires
this secret, so change it from the default before you start.

```bash
curl -X POST http://localhost:8000/auth/admin/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@interfab.com", "password": "yourpassword", "full_name": "Your Name", "setup_secret": "<your ADMIN_SETUP_SECRET from .env>"}'
```

Then log in:
```bash
curl -X POST http://localhost:8000/auth/admin/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@interfab.com", "password": "yourpassword"}'
```
Copy the `access_token` from the response — use it as `Authorization: Bearer <token>` for every
admin-only endpoint (creating defect types, standards, access codes, etc).

Need a second admin later (e.g. for a manager/partner)? Same endpoint, same secret — no code
change needed.

---

## 7. Generate an app login code (for the Flutter app)

Using the admin token from step 6:
```bash
curl -X POST http://localhost:8000/auth/access-codes \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"role": "inspection", "label": "QC Floor Code"}'
```
Response includes `plain_code` (e.g. `IN-A1B2C3`) — **shown only once**. This is what the Flutter
app sends to `/auth/app/login` to get its own token.

---

## 8. Everyday commands

```bash
docker compose up              # start everything (no rebuild)
docker compose up --build      # rebuild image after changing requirements.txt
docker compose down             # stop everything
docker compose down -v          # stop + wipe the database (careful - dev reset only)
docker compose logs -f api      # tail API logs
docker compose exec api bash    # shell inside the API container
docker compose exec db psql -U interfab -d interfab   # psql shell into the database
```

---

## 9. Project structure

```
app/
  core/                  # config, database, security (JWT), redis, storage abstraction
  features/
    auth/                # admin login + role-based access codes
    admin_config/        # ALL dashboard-editable lookup tables (defect types, standards, etc)
    plm/                 # the central parent - buyer/supplier/style/unit + colors/rolls/schedule
    attachments/         # generic photo upload (works for every module)
    roll_lock/           # Redis-based "don't let 2 people edit the same roll" lock
    fabric_inspection/   # 4-point system (flagship module - read this one first)
    gsm/                 # + moisture, skew_bowing, shrinkage, color_continuity (before/after-wash pattern)
    relaxation/          # + shade_check, correlation, factory_report (single-inspection modules)
    inspection_report/   # per-roll master aggregator across all modules
    analytics/           # defect ranking, pass/fail summary (query-based, always live)
  main.py                # wires every router together
  models_registry.py     # imports every model - Alembic reads THIS to see the full schema
alembic/                 # migrations
docker-compose.yml       # api + postgres + redis
Dockerfile
requirements.txt
.env.example
```

## 10. Adding a new feature/module later (Sewing, Cutting, Production...)

1. `mkdir app/features/sewing` with `models.py` / `schemas.py` / `routes.py` (copy an existing
   module like `relaxation` as a template — same shape).
2. Add `from app.features.sewing import models as sewing_models` to `models_registry.py`.
3. Add `app.include_router(sewing_router)` in `main.py`.
4. `docker compose exec api alembic revision --autogenerate -m "add sewing module"` then
   `alembic upgrade head`.

Nothing else changes — existing modules, PLM, auth, storage all stay exactly as they are.

## 11. Switching image storage to AWS S3 later

Edit `.env`:
```
STORAGE_BACKEND=s3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-south-1
AWS_S3_BUCKET=your-bucket-name
```
Restart (`docker compose restart api`). No code changes anywhere — every module already calls
the storage abstraction (`app/core/storage.py`), not the filesystem directly.
