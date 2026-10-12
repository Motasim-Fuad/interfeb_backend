import logging

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import os

from app.core.config import settings
from app.core.deps import get_current_user
from app import models_registry  # noqa: F401 - ensures every model is registered before routes/migrations run

from app.features.auth.routes import router as auth_router
from app.features.admin_config.routes import router as admin_config_router
from app.features.plm.routes import router as plm_router
from app.features.store.routes import router as store_router
from app.features.attachments.routes import router as attachments_router
from app.features.roll_lock.routes import router as roll_lock_router
from app.features.fabric_inspection.routes import router as fabric_inspection_router
from app.features.gsm.routes import router as gsm_router
from app.features.moisture.routes import router as moisture_router
from app.features.skew_bowing.routes import router as skew_bowing_router
from app.features.relaxation.routes import router as relaxation_router
from app.features.shade_check.routes import router as shade_check_router
from app.features.shrinkage.routes import router as shrinkage_router
from app.features.correlation.routes import router as correlation_router
from app.features.color_continuity.routes import router as color_continuity_router
from app.features.factory_report.routes import router as factory_report_router
from app.features.inspection_report.routes import router as inspection_report_router
from app.features.analytics.routes import router as analytics_router
from app.features.report_export.routes import router as report_export_router
from app.features.audit.routes import router as audit_router
from app.features.assistant.routes import router as assistant_router
from app.core.list_query import TOTAL_COUNT_HEADER
from app.core.sync import SYNC_STALE_HEADER

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings.assert_secure()

app = FastAPI(
    title=settings.APP_NAME,
    description="Interfab Fabric Inspection backend - feature-based, dashboard-configurable, extensible to Sewing/Cutting/Production later.",
    version="1.0.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# CORS - reads from .env (comma-separated). "*" for local dev; set real domain(s) for production
# without touching this code.
allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")] if settings.ALLOWED_ORIGINS != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,  # bearer tokens, no cookies
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[TOTAL_COUNT_HEADER, SYNC_STALE_HEADER, "Content-Disposition"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Serves locally-stored images at /media/... (only relevant while STORAGE_BACKEND=local)
os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")


@app.middleware("http")
async def media_headers(request, call_next):
    # Uploaded files are public (image widgets can't send auth headers): never let a browser
    # sniff one into HTML/script on the API origin.
    response = await call_next(request)
    if request.url.path.startswith("/media/"):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return response


app.include_router(auth_router)

# Everything except login/health needs a valid session; feature routes add their own role checks on top.
_authenticated = [Depends(get_current_user)]
for feature_router in (
    admin_config_router, plm_router, store_router, attachments_router, roll_lock_router,
    fabric_inspection_router, gsm_router, moisture_router, skew_bowing_router, relaxation_router,
    shade_check_router, shrinkage_router, correlation_router, color_continuity_router,
    factory_report_router, inspection_report_router, analytics_router, report_export_router,
    audit_router, assistant_router,
):
    app.include_router(feature_router, dependencies=_authenticated)


@app.get("/health", tags=["Health"])
def health_check():
    from sqlalchemy import text

    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "down"
    return {"status": "ok" if database == "ok" else "degraded", "app": settings.APP_NAME, "database": database}
