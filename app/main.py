from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os

from app.core.config import settings
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

app = FastAPI(
    title=settings.APP_NAME,
    description="Interfab Fabric Inspection backend - feature-based, dashboard-configurable, extensible to Sewing/Cutting/Production later.",
    version="1.0.0",
)

# CORS - reads from .env (comma-separated). "*" for local dev; set real domain(s) for production
# without touching this code.
allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")] if settings.ALLOWED_ORIGINS != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves locally-stored images at /media/... (only relevant while STORAGE_BACKEND=local)
os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")

app.include_router(auth_router)
app.include_router(admin_config_router)
app.include_router(plm_router)
app.include_router(store_router)
app.include_router(attachments_router)
app.include_router(roll_lock_router)
app.include_router(fabric_inspection_router)
app.include_router(gsm_router)
app.include_router(moisture_router)
app.include_router(skew_bowing_router)
app.include_router(relaxation_router)
app.include_router(shade_check_router)
app.include_router(shrinkage_router)
app.include_router(correlation_router)
app.include_router(color_continuity_router)
app.include_router(factory_report_router)
app.include_router(inspection_report_router)
app.include_router(analytics_router)
app.include_router(report_export_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.ENV}
