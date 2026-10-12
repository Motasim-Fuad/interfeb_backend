"""HTTP-level smoke test for the v8 floor/offline/analytics API.

Runs the real FastAPI app (auth, routing, headers) against a throwaway PostgreSQL DB:
    DATABASE_URL=postgresql+psycopg2://user@localhost:5432/interfab_smoke python smoke_test_v8_api.py
The script drops and recreates every table in that database, so never point it at real data.
"""
import os
import sys
from datetime import date, datetime, timedelta

if "interfab_smoke" not in os.environ.get("DATABASE_URL", "") and "/smoke" not in os.environ.get("DATABASE_URL", ""):
    sys.exit("Refusing to run: DATABASE_URL must point to a throwaway *smoke* database")

from fastapi.testclient import TestClient

from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_secret
import app.models_registry  # noqa: F401
from app.features.admin_config import routes as admin_routes, schemas as admin_schemas
from app.features.admin_config.models import Buyer, DefectRangeConfig, DefectType, Material, Supplier
from app.features.auth.models import AdminUser
from app.main import app

Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)

db = SessionLocal()
buyer = Buyer(name="Buyer A")
supplier = Supplier(name="Mill One", default_measurement_unit="inch")
material = Material(fabric_name="Single Jersey", is_active=True)
hole = DefectType(name="Hole", sort_order=1, is_active=True)
slub = DefectType(name="Slub", sort_order=2, is_active=True)
db.add_all([buyer, supplier, material, hole, slub])
db.flush()
r4 = DefectRangeConfig(defect_type_id=hole.id, range_label="Above 9", points=4, sort_order=4, is_active=True)
r1 = DefectRangeConfig(defect_type_id=slub.id, range_label="Up to 3", points=1, sort_order=1, is_active=True)
db.add_all([r4, r1, AdminUser(email="admin@x.com", password_hash=hash_secret("secret"), full_name="Admin One")])
db.commit()
admin_routes.save_supplier_qc_profile(
    supplier.id,
    admin_schemas.SupplierQcProfileSaveIn(
        qc_config={
            "fabric_inspection": {"individual_standard": 20, "average_standard": 18},
            "correlation": {"standard": 40, "scs": 1},
            "gsm": {"standard_gsm": 180, "tolerance_percent": 5},
        },
        shade_points=[],
    ),
    db=db,
    _=type("U", (), {"is_admin": True})(),
)
ids = {k: str(v) for k, v in dict(buyer=buyer.id, supplier=supplier.id, material=material.id, hole=hole.id, slub=slub.id, r4=r4.id, r1=r1.id).items()}
db.close()

client = TestClient(app)


def ok(resp, code=200):
    assert resp.status_code == code, f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text[:400]}"
    return resp


admin = {"Authorization": "Bearer " + ok(client.post("/auth/admin/login", json={"email": "admin@x.com", "password": "secret"})).json()["access_token"]}

receive = ok(client.post("/store/fabric-receives", headers=admin, json={
    "consignment": "CONS-1", "buyer_id": ids["buyer"], "supplier_id": ids["supplier"], "received_quantity": 1000,
    "quantity_unit": "yds", "fabric_type": "SJ", "fabric_composition": "Cotton", "qc_setup_status": "finalized", "qc_config": {},
})).json()
plm = ok(client.post("/plms", headers=admin, json={
    "plm_code": "PLM-1", "plm_date": date.today().isoformat(), "fabric_receive_id": receive["id"], "material_id": ids["material"],
    "allocated_quantity": 600,
    "colors": [{"colour_code": "NAVY", "planned_quantity": 600, "rolls": [
        {"roll_no": "R1", "marked_width": 60, "marked_length": 100, "planned_quantity": 100, "supplier_total_point": 10},
        {"roll_no": "R2", "marked_width": 60, "marked_length": 100, "planned_quantity": 100, "supplier_total_point": 30},
    ]}],
})).json()
plm_id = plm["id"]

# Paging header + lite list.
resp = ok(client.get("/plms", params={"limit": 1, "lite": True}))
assert resp.headers["x-total-count"] == "1" and resp.json()[0]["colors"] == []
assert len(ok(client.get("/plms")).json()[0]["colors"]) == 1
assert ok(client.get("/store/fabric-receives", params={"limit": 5})).headers["x-total-count"] == "1"

code = ok(client.post("/auth/access-codes", headers=admin, json={"role": "worker", "label": "Table 3"})).json()["plain_code"]
worker = {"Authorization": "Bearer " + ok(client.post("/auth/app/login", json={"code": code.lower()})).json()["access_token"]}
insp_code = ok(client.post("/auth/access-codes", headers=admin, json={"role": "inspection", "label": "QC Lead"})).json()["plain_code"]
inspector = {"Authorization": "Bearer " + ok(client.post("/auth/app/login", json={"code": insp_code})).json()["access_token"]}

# Offline-friendly save without a report id, with per-defect location.
t0 = datetime.utcnow().replace(microsecond=0)
roll_body = {
    "roll_no": "R1", "width_actual": 60, "length_actual": 100, "client_updated_at": t0.isoformat() + "Z",
    "defects": [
        {"defect_type_id": ids["hole"], "range_config_id": ids["r4"], "count": 1, "position_yard": 12.5, "side": "left", "recorded_at": t0.isoformat() + "Z"},
        {"defect_type_id": ids["slub"], "range_config_id": ids["r1"], "count": 1, "position_yard": 40, "side": "center"},
    ],
}
saved = ok(client.post(f"/fabric-inspection/by-plm/{plm_id}/rolls", headers=worker, json=roll_body)).json()
assert saved["total_points"] == 5 and saved["inspector_name"] == "Table 3" and saved["disposition"] == "pending"
assert abs(saved["percent_points"] - 5 * 3600 / (60 * 100)) < 1e-6 and saved["is_pass"] is True
assert {d["side"] for d in saved["defects"]} == {"left", "center"}

# An older offline replay must not overwrite newer data.
stale = dict(roll_body, client_updated_at=(t0 - timedelta(minutes=5)).isoformat() + "Z", defects=[])
resp = ok(client.post(f"/fabric-inspection/by-plm/{plm_id}/rolls", headers=worker, json=stale))
assert resp.headers.get("x-sync-stale") == "1" and resp.json()["total_points"] == 5

# Second roll fails the individual standard of 20: 6 holes x 4 pts on 60in x 50yd = 28.8.
ok(client.post(f"/fabric-inspection/by-plm/{plm_id}/rolls", headers=inspector, json={
    "roll_no": "R2", "width_actual": 60, "length_actual": 50,
    "defects": [{"defect_type_id": ids["hole"], "range_config_id": ids["r4"], "count": 6}],
}))
assert ok(client.post(f"/fabric-inspection/by-plm/{plm_id}/rolls", headers=worker, json={"roll_no": "NOPE", "defects": []}), 422)

preview = ok(client.post("/fabric-inspection/preview", headers=worker, json={"plm_id": plm_id, "width_actual": 60, "length_actual": 50, "total_points": 24})).json()
assert abs(preview["percent_points"] - 28.8) < 1e-6 and preview["is_pass"] is False and preview["individual_standard"] == 20

resp = ok(client.get("/fabric-inspection/rolls", params={"is_pass": False}))
assert resp.headers["x-total-count"] == "1"
failed_roll = resp.json()[0]
assert failed_roll["roll_no"] == "R2" and failed_roll["supplier_name"] == "Mill One"

ok(client.patch(f"/fabric-inspection/rolls/{failed_roll['id']}/disposition", headers=worker, json={"disposition": "hold"}), 403)
decided = ok(client.patch(f"/fabric-inspection/rolls/{failed_roll['id']}/disposition", headers=admin,
                          json={"disposition": "claim", "disposition_note": "Holes across width", "shade_band": "A"})).json()
assert decided["disposition"] == "claim" and decided["shade_band"] == "A"
assert ok(client.get("/fabric-inspection/rolls", params={"disposition": "claim,hold"})).headers["x-total-count"] == "1"

# Analytics.
f = {"date_from": (date.today() - timedelta(days=7)).isoformat(), "date_to": date.today().isoformat()}
k = ok(client.get("/analytics/kpis", params=f)).json()
assert k["rolls_inspected"] == 2 and k["passed"] == 1 and k["failed"] == 1 and k["pass_rate"] == 50.0 and k["claim"] == 1
expected_density = round((5 + 24) * 3600 / (60 * 100 + 60 * 50), 2)
assert k["points_per_100_sq_yd"] == expected_density, (k, expected_density)
trend = ok(client.get("/analytics/trend", params=f)).json()
assert trend and trend[-1]["rolls"] == 2
pareto = ok(client.get("/analytics/defect-pareto", params=f)).json()
assert pareto[0]["defect_type_name"] == "Hole" and pareto[-1]["cumulative_percent"] == 100.0
card = ok(client.get("/analytics/supplier-scorecard", params=f)).json()
assert card[0]["supplier_name"] == "Mill One" and card[0]["grade"] == "D" and len(card[0]["trend"]) == 6
heat = ok(client.get("/analytics/defect-heatmap", params=f)).json()
assert heat["suppliers"] == ["Mill One"] and set(heat["defect_types"]) == {"Hole", "Slub"}
scatter = ok(client.get("/analytics/correlation-scatter", params=f)).json()
assert {(p["roll_no"], p["mill_points"], p["factory_points"]) for p in scatter} == {("R1", 10.0, 5), ("R2", 30.0, 24)}
disp = {d["disposition"]: d["count"] for d in ok(client.get("/analytics/disposition-summary", params=f)).json()}
assert disp == {"pending": 1, "accepted": 0, "hold": 0, "rejected": 0, "claim": 1}
pf = ok(client.get("/analytics/pass-fail-summary", params={"buyer_id": ids["buyer"]})).json()
assert pf["total_rolls"] == 2 and pf["passed"] == 1
assert ok(client.get("/analytics/analysis-overview")).json()["supplier_quality"][0]["total"] == 2
assert ok(client.get("/analytics/inspection-rolls", params={"limit": 1})).json()["total"] == 2

# Derived reports were refreshed by the 4-point saves; global lists are read-only and paged.
corr = ok(client.get("/correlation", params={"plm_id": plm_id})).json()[0]
assert {r["roll_no"]: r["factory_total_points"] for r in corr["rolls"]} == {"R1": 5, "R2": 24}
assert ok(client.get("/correlation")).headers["x-total-count"] == "1"
assert ok(client.get("/factory-report")).json()[0]["total_point"] == 29
assert ok(client.get("/gsm")).headers["x-total-count"] == "0"
assert ok(client.get("/plms", params={"q": "nothing-matches"})).headers["x-total-count"] == "0"

# Schedules: filters + worker may only start.
sched = ok(client.post("/plms/schedules", headers=inspector, json={"plm_id": plm_id, "scheduled_date": date.today().isoformat()})).json()
resp = ok(client.get("/plms/schedules/list", params={"status": "pending,in_progress", "date_from": date.today().isoformat(), "q": "PLM"}))
assert resp.headers["x-total-count"] == "1" and resp.json()[0]["plm_code"] == "PLM-1" and resp.json()[0]["supplier_name"] == "Mill One"
ok(client.patch(f"/plms/schedules/{sched['id']}/status", headers=worker, json={"status": "in_progress"}))
ok(client.patch(f"/plms/schedules/{sched['id']}/status", headers=worker, json={"status": "completed"}), 403)
assert ok(client.get("/analytics/kpis")).json()["schedules_in_progress"] == 1

# Audit trail.
logs = ok(client.get("/audit-logs", headers=admin, params={"limit": 100})).json()
actions = {l["action"] for l in logs}
assert {"roll_created", "roll_disposition", "plm_created", "fabric_receive_created", "schedule_created", "schedule_status"} <= actions, actions
assert any(l["actor_label"] == "Table 3" for l in logs)
ok(client.get("/audit-logs", headers=worker), 403)

# PDF still renders with the new decision + location sections.
pdf = ok(client.get(f"/reports/pdf/fabric-inspection/{plm_id}"))
assert pdf.content.startswith(b"%PDF") and len(pdf.content) > 2000

# Attachments: images only.
ok(client.post("/attachments", headers=worker, data={"owner_type": "fabric_inspection_defect_photo", "owner_id": plm_id},
               files={"file": ("x.txt", b"not an image", "application/octet-stream")}), 415)
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ok(client.post("/attachments", headers=worker, data={"owner_type": "fabric_inspection_defect_photo", "owner_id": plm_id},
               files={"file": ("x.png", png, "application/octet-stream")}))

# Module rolls: an older offline copy never overwrites a newer save of the same roll.
gsm = ok(client.post("/gsm", headers=inspector, json={"plm_id": plm_id, "color": "Navy", "report_date": date.today().isoformat()})).json()
newer = (datetime.utcnow()).isoformat() + "Z"
older = (datetime.utcnow() - timedelta(minutes=10)).isoformat() + "Z"
ok(client.post(f"/gsm/{gsm['id']}/rolls", headers=worker, json={"roll_no": "R1", "actual_gsm_before": 181, "client_updated_at": newer}))
resp = ok(client.post(f"/gsm/{gsm['id']}/rolls", headers=worker, json={"roll_no": "R1", "actual_gsm_before": 150, "client_updated_at": older}))
assert resp.headers.get("x-sync-stale") == "1" and float(resp.json()["actual_gsm_before"]) == 181
resp = ok(client.post(f"/gsm/{gsm['id']}/rolls", headers=worker, json={"roll_no": "R1", "actual_gsm_before": 182}))
assert "x-sync-stale" not in resp.headers and float(resp.json()["actual_gsm_before"]) == 182
card = ok(client.post("/color-continuity", headers=inspector, json={"plm_id": plm_id})).json()
ok(client.post(f"/color-continuity/{card['id']}/rolls", headers=worker, json={"roll_no": "R1", "shade_group_before": "A", "client_updated_at": newer}))
resp = ok(client.post(f"/color-continuity/{card['id']}/rolls", headers=worker, json={"roll_no": "R1", "shade_group_before": "B", "client_updated_at": older}))
assert resp.headers.get("x-sync-stale") == "1" and resp.json()["shade_group_before"] == "A"

# Floor undo: a mistaken roll can be deleted until QC decides on it.
ok(client.delete(f"/fabric-inspection/rolls/{failed_roll['id']}", headers=worker), 409)
r1_id = saved["id"]
ok(client.delete(f"/fabric-inspection/rolls/{r1_id}"), 401)
ok(client.delete(f"/fabric-inspection/rolls/{r1_id}", headers=worker), 204)
ok(client.delete(f"/fabric-inspection/rolls/{r1_id}", headers=worker), 404)
rolls_left = {r["roll_no"] for r in ok(client.get("/fabric-inspection", params={"plm_id": plm_id})).json()[0]["rolls"]}
assert rolls_left == {"R2"}, rolls_left
corr = ok(client.get("/correlation", params={"plm_id": plm_id})).json()[0]
assert "R1" not in {r["roll_no"] for r in corr["rolls"] if r.get("factory_total_points")}
logs = ok(client.get("/audit-logs", headers=admin, params={"limit": 100})).json()
assert any(l["action"] == "roll_deleted" for l in logs)

# Brute-force protection on app login.
codes = [client.post("/auth/app/login", json={"code": "WO-000000"}).status_code for _ in range(25)]
assert 429 in codes, codes

print("V8_API_SMOKE_OK")
