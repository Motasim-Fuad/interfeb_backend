"""Mill report -> roll rows, run as a background job (a local model may take a minute per photo).

POST /plms/{plm_id}/rolls/extract   upload 1-10 files, returns a job id
GET  /plms/rolls/extract/{job_id}   status, progress and the rows read so far

The rows are only a proposal. The Dashboard shows them for review and then calls the normal
POST /plms/{plm_id}/rolls/import (dry run, then apply), which creates missing colours, skips
rolls that already exist and validates every line.
"""
import json
import threading
import time
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.deps import require_admin
from app.core.redis_client import redis_client
from app.features.plm.models import Plm, PlmColor
from app.features.roll_extract import service

router = APIRouter(prefix="/plms", tags=["Mill report reader"])

MAX_FILES = 10
MAX_BYTES = 15 * 1024 * 1024
JOB_TTL = 60 * 60
HEARTBEAT_SECONDS = 15
STALE_AFTER_SECONDS = 90  # no heartbeat for this long = the worker died (restart/deploy)


def _key(job_id: str) -> str:
    return f"rollextract:{job_id}"


def _save(job_id: str, job: dict) -> None:
    redis_client.set(_key(job_id), json.dumps(job), ex=JOB_TTL)


def _beat_key(job_id: str) -> str:
    return f"rollextract:beat:{job_id}"


def _heartbeat(job_id: str, stop: threading.Event) -> None:
    while not stop.is_set():
        redis_client.set(_beat_key(job_id), str(time.time()), ex=JOB_TTL)
        stop.wait(HEARTBEAT_SECONDS)


def _run(job_id: str, plm_id: str, files: list[tuple[str, bytes]]) -> None:
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True).start()
    try:
        _run_files(job_id, plm_id, files)
    finally:
        stop.set()


def _run_files(job_id: str, plm_id: str, files: list[tuple[str, bytes]]) -> None:
    job = json.loads(redis_client.get(_key(job_id)) or "{}")
    with SessionLocal() as db:
        colors = [c.colour_code for c in db.query(PlmColor).filter(PlmColor.plm_id == plm_id).all()]
    rows, errors = [], []
    for n, (name, data) in enumerate(files, 1):
        job.update(progress=f"Reading {name} ({n} of {len(files)})")
        _save(job_id, job)
        try:
            rows += service.extract_file(name, data, progress=lambda msg: _save(job_id, {**job, "progress": msg}))
        except service.ExtractError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - one bad file must not lose the others
            errors.append(f"{name}: could not be read ({exc.__class__.__name__}).")
    # A sheet without a colour column belongs to the PLM's only colour, when there is one.
    if len(colors) == 1:
        for r in rows:
            r["color"] = r["color"] or colors[0]
    seen = {}
    for i, r in enumerate(rows, 1):
        r["line"] = i
        key = r["roll_no"].lower()
        if key in seen:
            r["unsure"] = sorted(set(r["unsure"] + ["roll_no"]))
        seen.setdefault(key, i)
    job.update(status="done", progress="", rows=rows, errors=errors)
    _save(job_id, job)


@router.post("/{plm_id}/rolls/extract")
async def start_extract(plm_id: uuid.UUID, files: List[UploadFile] = File(...), db: Session = Depends(get_db), _=Depends(require_admin())):
    if not db.query(Plm).get(plm_id):
        raise HTTPException(status_code=404, detail="PLM not found")
    if not files or len(files) > MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Upload 1 to {MAX_FILES} files.")
    payload = []
    for f in files:
        data = await f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"{f.filename} is larger than 15 MB.")
        payload.append((f.filename or "file", data))
    needs_model = any(name.rsplit(".", 1)[-1].lower() not in service.SHEET_TYPES for name, _ in payload)
    if needs_model and not service.vision_enabled():
        raise HTTPException(status_code=422, detail="Photo / PDF reading is not configured on the server (VISION_LLM_*). Excel and CSV still work.")
    job_id = uuid.uuid4().hex
    _save(job_id, {"status": "running", "progress": "Starting...", "rows": [], "errors": [], "model": service.vision_label()})
    redis_client.set(_beat_key(job_id), str(time.time()), ex=JOB_TTL)
    threading.Thread(target=_run, args=(job_id, str(plm_id), payload), daemon=True).start()
    return {"job_id": job_id}


@router.get("/rolls/extract/{job_id}")
def extract_status(job_id: str, _=Depends(require_admin())):
    raw = redis_client.get(_key(job_id))
    if not raw:
        raise HTTPException(status_code=404, detail="This reading job has expired. Upload the files again.")
    job = json.loads(raw)
    if job.get("status") == "running":
        beat = float(redis_client.get(_beat_key(job_id)) or 0)
        if time.time() - beat > STALE_AFTER_SECONDS:
            job.update(status="done", progress="",
                       errors=job.get("errors", []) + ["Reading stopped because the server restarted. Please upload the files again."])
            _save(job_id, job)
    return job
