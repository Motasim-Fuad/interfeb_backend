"""Replace local data with a small demo dataset (3-4 rows per screen) for presentations.

Keeps admin_users and access_codes (so existing logins still work) and wipes
everything else. All inspection data goes through the real API routes, so every
4-point / GSM / skew / moisture figure is calculated by the backend itself.

    docker compose exec -T api python seed_demo.py --yes              # wipe + seed
    docker compose exec -T api python seed_demo.py --yes --wipe-only  # wipe only
"""
import math
import os
import random
import sys
from datetime import date, datetime, time, timedelta

if "--yes" not in sys.argv:
    sys.exit("This wipes all QC data (keeps admin users + access codes). Re-run with --yes.")
if "render.com" in os.environ.get("DATABASE_URL", "") or os.environ.get("ALLOW_SEED") == "0":
    sys.exit("Refusing to seed a remote database")

from fastapi.testclient import TestClient
from sqlalchemy import text

import app.models_registry  # noqa: F401
from app.core.cache import invalidate_prefix
from app.core.database import Base, SessionLocal, engine
from app.core.security import create_token
from app.features.admin_config import routes as admin_routes, schemas as admin_schemas
from app.features.admin_config.models import Buyer, DefectRangeConfig, DefectType, Material, Supplier
from app.features.auth.models import AccessCode, AdminUser
from app.main import app

rng = random.Random(2026)
TODAY = date.today()
KEEP = {"admin_users", "access_codes", "alembic_version"}

# ---------------------------------------------------------------- wipe
with engine.begin() as conn:
    before = {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in ("admin_users", "access_codes")}
    tables = [t.name for t in Base.metadata.sorted_tables if t.name not in KEEP]
    conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    after = {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in ("admin_users", "access_codes")}
    if before != after:
        raise SystemExit(f"Aborted: wipe would touch login tables {before} -> {after}")
print(f"wiped {len(tables)} tables (kept admin users + access codes)")
if "--wipe-only" in sys.argv:
    from app.core.cache import invalidate_prefix as _clear_cache
    _clear_cache("analytics")
    sys.exit(0)

# ---------------------------------------------------------------- master data
db = SessionLocal()
admin = db.query(AdminUser).filter(AdminUser.is_active == True).first()  # noqa: E712
if not admin:
    sys.exit("No active admin user found")
codes = {role: db.query(AccessCode).filter(AccessCode.role == role).first() for role in ("worker", "inspection")}


def bearer(sub, role, label, is_admin=False):
    return {"Authorization": "Bearer " + create_token(str(sub), {"role": role, "is_admin": is_admin, "label": label}, 600)}


ADMIN = bearer(admin.id, "admin", admin.full_name or "Admin", True)
WORKERS = [bearer(codes["worker"].id if codes["worker"] else admin.id, "worker", n) for n in ("Rahim - Table 1", "Karim - Table 2", "Shila - Table 3")]
INSPECTORS = [bearer(codes["inspection"].id if codes["inspection"] else admin.id, "inspection", n) for n in ("QC Nasrin", "QC Habib")]

buyers = [Buyer(name=n) for n in ("H&M", "Primark", "Marks & Spencer", "Next")]

# name, unit, qty unit, 4-pt individual/average, fail rate at start -> end, defect weights,
# communication rating at start -> end (scorecard grade = 70% pass rate + 30% rating)
SUPPLIERS = [
    ("Bousudhara Textiles", "inch", "yds", 20, 18, 0.03, 0.03, {"Slub": 4, "Stain / Oil Spot": 2, "Thick & Thin": 2, "Hole": 1}, 4.5, 4.8),
    ("Jomuna Knit Composite", "cm", "yds", 20, 18, 0.05, 0.05, {"Thick & Thin": 5, "Slub": 2, "Hole": 2}, 3.6, 4.0),
    ("Nabil Fabrics", "inch", "yds", 24, 20, 0.48, 0.15, {"Hole": 4, "Stain / Oil Spot": 4, "Slub": 1}, 2.6, 3.6),
    ("Square Fabrics", "cm", "meter", 18, 16, 0.04, 0.02, {"Slub": 3, "Stain / Oil Spot": 1}, 4.8, 4.9),
]
TAIL_SUPPLIERS = {2: 3, 1: 2}  # in progress today, scheduled tomorrow
suppliers = [Supplier(name=s[0], default_measurement_unit=s[1]) for s in SUPPLIERS]

# fabric_name, dyeing, fiber, code, fibers, moisture %, construction, gsm, gsm tol, width in, skew limit
MATERIALS = [
    ("Single Jersey", "Solid Dyed", "Cotton", "FAB-SJ-160", [("Cotton", "100")], 61, "Knit - Single Jersey", 160, 5, 60, 1.5),
    ("Pique", "Yarn Dyed", "Cotton", "FAB-PQ-200", [("Cotton", "100")], 61, "Knit - Pique", 200, 5, 62, 1.5),
    ("Fleece", "Solid Dyed", "CVC", "FAB-FL-280", [("Cotton", "60"), ("Polyester", "40")], 55, "Knit - 3 Thread Fleece", 280, 6, 68, 2.0),
    ("Poplin", "Piece Dyed", "Cotton", "FAB-PP-120", [("Cotton", "100")], 61, "Woven - Plain", 120, 4, 58, 1.0),
]
materials = []
for name, dye, fiber, code, fibers, moist, weave, gsm, tol, width, skew in MATERIALS:
    m = Material(
        fabric_name=name, dyeing_method=dye, primary_fiber=fiber, fabric_code=code,
        accepted_moisture=str(moist), weave_knit_type=weave, gsm_expected=str(gsm), gsm_tolerance=str(tol),
        fabric_width_expected=f'{width}"', width_tolerance=str(skew), fabric_length_per_roll="100",
        finish="Soft", handfeel="Smooth", is_active=True,
    )
    for i, (f, pct) in enumerate(fibers, start=1):
        setattr(m, f"fiber_type_{i}", f)
        setattr(m, f"fiber_{i}_pct", pct)
    materials.append(m)
MAT_INFO = {m.fabric_name: info for m, info in zip(materials, MATERIALS)}

DEFECTS = ["Hole", "Slub", "Stain / Oil Spot", "Thick & Thin"]
defect_types = [DefectType(name=n, sort_order=i + 1, is_active=True) for i, n in enumerate(DEFECTS)]
db.add_all(buyers + suppliers + materials + defect_types)
db.flush()

ranges = {}  # defect name -> [(range id, points)]
for d in defect_types:
    spec = [("Up to 1 in", 2), ("Over 1 in", 4)] if d.name == "Hole" else [("Up to 3 in", 1), ("3 - 6 in", 2), ("6 - 9 in", 3), ("Over 9 in", 4)]
    rows = [DefectRangeConfig(defect_type_id=d.id, range_label=l, points=p, sort_order=i + 1, is_active=True) for i, (l, p) in enumerate(spec)]
    db.add_all(rows)
    db.flush()
    ranges[d.name] = [(str(r.id), r.points) for r in rows]
DEFECT_IDS = {d.name: str(d.id) for d in defect_types}
db.commit()

SHADE_POINTS = [
    ("top_to_middle", "Top to Middle Check", "length"), ("middle_to_bottom", "Middle to Bottom Check", "length"),
    ("bottom_to_top", "Bottom to Top Check", "length"), ("width_4_to_1", "4 to 1 Check", "width"),
    ("width_1_to_3", "1 to 3 Check", "width"), ("width_3_to_5", "3 to 5 Check", "width"), ("width_5_to_2", "5 to 2 Check", "width"),
]
for s, spec in zip(suppliers, SUPPLIERS):
    admin_routes.save_supplier_qc_profile(
        s.id,
        admin_schemas.SupplierQcProfileSaveIn(
            qc_config={
                "fabric_inspection": {"individual_standard": spec[3], "average_standard": spec[4]},
                "correlation": {"standard": spec[3], "scs": 4},
                "relaxation": {"relaxation_hours": 12},
            },
            shade_points=[admin_schemas.SupplierShadePointIn(check_key=k, label=l, check_type=t, sort_order=i + 1) for i, (k, l, t) in enumerate(SHADE_POINTS)],
        ),
        db=db, _=type("U", (), {"is_admin": True})(),
    )
ids = dict(
    buyers=[str(b.id) for b in buyers],
    suppliers=[(str(s.id), spec) for s, spec in zip(suppliers, SUPPLIERS)],
    materials=[(str(m.id), m.fabric_name) for m in materials],
)
db.close()
print("master data: 4 buyers, 4 suppliers (+QC profiles), 4 materials, 4 defect types")

# ---------------------------------------------------------------- API helpers
client = TestClient(app)


def call(method, url, headers=None, json=None, expect=200):
    r = client.request(method, url, headers=headers, json=json)
    if r.status_code != expect:
        raise SystemExit(f"{method} {url} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def round_to(v, step=50):
    return int(math.ceil(v / step) * step)


COLOURS = ["NAVY", "BLACK", "WHITE", "HEATHER GREY", "OLIVE", "MAROON", "SKY BLUE", "CHARCOAL", "BEIGE", "RED"]
STYLES = {"H&M": "HM", "Primark": "PRM", "Marks & Spencer": "MNS", "Next": "NXT", "C&A": "CNA"}
BUYER_NAMES = ["H&M", "Primark", "Marks & Spencer", "Next"]


def fail_rate(spec, when: date, start: date, end: date) -> float:
    k = max(0.0, min(1.0, (when - start).days / max(1, (end - start).days)))
    return spec[5] + (spec[6] - spec[5]) * k


def defects_for(target_points: int, weights: dict, length: float):
    names, w = list(weights), list(weights.values())
    out, left = [], target_points
    while left > 0:
        name = rng.choices(names, w)[0]
        options = [(rid, p) for rid, p in ranges[name] if p <= left] or [min(ranges["Slub"], key=lambda x: x[1])]
        rid, pts = rng.choices(options, [4 if p == 1 else 3 if p == 2 else 2 if p == 3 else 1 for _, p in options])[0]
        out.append({
            "defect_type_id": DEFECT_IDS[name if any(r == rid for r, _ in ranges[name]) else "Slub"],
            "range_config_id": rid, "count": 1,
            "position_yard": round(rng.uniform(1, max(2, length - 1)), 1),
            "side": rng.choice(["left", "center", "center", "right"]),
        })
        left -= pts
    return out


# ---------------------------------------------------------------- timeline
# 4 consignments: two finished, one being inspected today, one scheduled for tomorrow
start = TODAY - timedelta(days=30)
receive_dates = [start, TODAY - timedelta(days=16), TODAY - timedelta(days=3), TODAY - timedelta(days=2)]

stats = dict(receives=0, plms=0, rolls=0, inspected=0, failed=0)
plm_rows = []
seq = 0
for idx, rdate in enumerate(receive_dates):
    tail = len(receive_dates) - idx  # 2 = in progress today, 1 = tomorrow
    sup_id, spec = ids["suppliers"][TAIL_SUPPLIERS.get(tail, idx)]
    mat_id, mat_name = ids["materials"][idx]
    minfo = MAT_INFO[mat_name]
    buyer_i = idx
    unit, qty_unit = spec[1], spec[2]
    width = minfo[9] if unit == "inch" else round(minfo[9] * 2.54)
    n_plms = 1

    plans = []
    for p in range(n_plms):
        colours = [COLOURS[idx]]
        colour_plans = []
        for ci, colour in enumerate(colours):
            rolls = []
            for ri in range(4):
                length = rng.choice([95, 98, 100, 100, 102, 105, 110, 120]) if qty_unit == "yds" else rng.choice([88, 90, 92, 95, 100])
                rolls.append({"roll_no": f"{ci + 1}{ri + 1:02d}", "marked_width": width, "marked_length": length,
                              "planned_quantity": length, "supplier_total_point": 0})
            colour_plans.append({"colour_code": colour, "planned_quantity": sum(r["planned_quantity"] for r in rolls), "rolls": rolls})
        plans.append(colour_plans)
    total = sum(c["planned_quantity"] for plan in plans for c in plan)

    seq += 1
    receive = call("POST", "/store/fabric-receives", ADMIN, {
        "consignment": f"CN-{rdate:%y%m}-{seq:03d}", "buyer_id": ids["buyers"][buyer_i], "supplier_id": sup_id,
        "received_quantity": round_to(total * rng.uniform(1.05, 1.25)), "quantity_unit": qty_unit,
        "fabric_type": mat_name, "fabric_composition": " / ".join(f"{p}% {f}" for f, p in minfo[4]),
        "received_at": rdate.isoformat(), "qc_setup_status": "finalized",
        "qc_config": {"fabric_inspection": {"checked_quantity": round_to(total, 10)}},
        "notes": f"{rng.choice(['Sea', 'Road', 'Local'])} shipment, {sum(len(c['rolls']) for plan in plans for c in plan)} rolls",
    })
    stats["receives"] += 1

    for p, colour_plans in enumerate(plans):
        plm_date = rdate + timedelta(days=1 + p)
        if tail == 2:
            start_day = TODAY
        elif tail == 1:
            start_day = TODAY + timedelta(days=1)
        else:
            start_day = plm_date + timedelta(days=rng.randint(1, 3))
        # mills under-report: declared points ~ 55-100% of what the factory will find
        rolls_flat = [(c, r) for c in colour_plans for r in c["rolls"]]
        fr = fail_rate(spec, start_day, start, TODAY)
        outcomes = {}
        for c, r in rolls_flat:
            w_in = width if unit == "inch" else width / 2.54
            l_yd = r["marked_length"] if qty_unit == "yds" else r["marked_length"] * 1.0936
            fail = rng.random() < fr or (idx == 1 and r is rolls_flat[1][1])  # always one failed roll to show
            pct = rng.uniform(spec[3] * 1.08, spec[3] * 1.7) if fail else rng.uniform(spec[3] * 0.15, spec[3] * 0.85)
            pts = max(1, round(pct * w_in * l_yd / 3600))
            outcomes[r["roll_no"]] = (pts, fail)
            r["supplier_total_point"] = max(0, round(pts * rng.uniform(0.55, 1.0)))
        plm = call("POST", "/plms", ADMIN, {
            "plm_code": f"PLM-{plm_date:%y%m%d}-{p + 1}", "plm_date": plm_date.isoformat(), "fabric_receive_id": receive["id"],
            "material_id": mat_id, "plm_style": f"{STYLES[BUYER_NAMES[buyer_i]]}-{rng.randint(1000, 9999)}",
            "allocated_quantity": sum(c["planned_quantity"] for c in colour_plans), "colors": colour_plans,
        })
        stats["plms"] += 1
        stats["rolls"] += len(rolls_flat)
        plm_rows.append((plm, plm_date, start_day))
        inspector = rng.choice(INSPECTORS)

        sched = call("POST", "/plms/schedules", inspector, {
            "plm_id": plm["id"], "scheduled_date": start_day.isoformat(),
            "assigned_to": rng.choice(["Rahim", "Karim", "Shila"]), "machine_note": f"Machine {rng.randint(1, 4)}",
        })
        if start_day > TODAY:
            continue
        in_progress = tail == 2
        call("PATCH", f"/plms/schedules/{sched['id']}/status", ADMIN, {"status": "in_progress" if in_progress else "completed"})

        # ---- 4-point on the floor
        todo = rolls_flat[: max(2, int(len(rolls_flat) * 0.6))] if in_progress else rolls_flat
        clock = datetime.combine(start_day, time(3, 0)) + timedelta(minutes=rng.randint(0, 40))  # 9:00 local
        saved_rolls = []
        for c, r in todo:
            if clock > datetime.utcnow() - timedelta(minutes=5):
                break
            pts, fail = outcomes[r["roll_no"]]
            length_actual = round(r["marked_length"] + rng.uniform(-2.5, 1.5), 1)
            width_actual = round(r["marked_width"] + rng.uniform(-0.6, 0.3), 1)
            if clock.hour >= 11:  # past 17:00 local, continue next morning
                clock = datetime.combine(clock.date() + timedelta(days=1), time(3, 0))
            body = {
                "roll_no": r["roll_no"], "colour_code": c["colour_code"], "width_actual": width_actual,
                "length_actual": length_actual, "inspected_at": iso(clock),
                "defects": defects_for(pts, spec[7], length_actual),
                "remarks": "Shade variation at selvedge" if fail and rng.random() < 0.3 else None,
            }
            saved = call("POST", f"/fabric-inspection/by-plm/{plm['id']}/rolls", rng.choice(WORKERS), body)
            saved_rolls.append((saved, clock))
            stats["inspected"] += 1
            stats["failed"] += 0 if saved["is_pass"] else 1
            clock += timedelta(minutes=rng.randint(14, 26))

        # ---- QC decisions (recent rolls stay pending for the Roll Decisions page)
        for saved, at in saved_rolls:
            if (TODAY - at.date()).days < 3:
                continue
            if saved["is_pass"]:
                if rng.random() < 0.92:
                    call("PATCH", f"/fabric-inspection/rolls/{saved['id']}/disposition", ADMIN, {"disposition": "accepted"})
            elif rng.random() < 0.8:
                choice = rng.choices(["hold", "rejected", "claim"], [3, 2, 3])[0]
                note = {"hold": "Hold for buyer approval", "rejected": "Too many defects - return to mill", "claim": "Claim raised with mill"}[choice]
                call("PATCH", f"/fabric-inspection/rolls/{saved['id']}/disposition", ADMIN,
                     {"disposition": choice, "disposition_note": note, "shade_band": rng.choice(["A", "B"])})

        # ---- lab / module tests on sample rolls
        sample = rolls_flat[:3]
        if in_progress:
            sample = sample[:2]
        before_day, after_day = start_day, start_day + timedelta(days=1)
        after = not in_progress and after_day <= TODAY
        status = "after_wash_done" if after else "before_wash_done"
        colours_txt = ", ".join(c["colour_code"] for c in colour_plans)
        quality = 1.0 - fr  # better mills sit closer to the standard

        gsm_std, gsm_tol = minfo[7], minfo[8]
        gsm = call("POST", "/gsm", inspector, {"plm_id": plm["id"], "report_date": before_day.isoformat()})
        bias = rng.uniform(-0.03, 0.03) * (1.6 - quality)
        for c, r in sample:
            before_v = round(gsm_std * (1 + bias + rng.gauss(0, 0.015)), 1)
            call("POST", f"/gsm/{gsm['id']}/rolls", rng.choice(WORKERS), {
                "roll_no": r["roll_no"], "color": c["colour_code"], "actual_gsm_before": before_v,
                "actual_gsm_after": round(before_v * rng.uniform(1.01, 1.04), 1) if after else None,
                "weight": round(rng.uniform(17, 26), 1), "before_wash_date": before_day.isoformat(),
                "after_wash_date": after_day.isoformat() if after else None, "roll_status": status,
            })

        moist_std = minfo[5]
        moist = call("POST", "/moisture", inspector, {"plm_id": plm["id"], "report_date": before_day.isoformat(), "report_time": "10:30", "factory": "Interfab Unit 1"})
        for c, r in sample:
            wet = rng.random() < (1 - quality) * 0.5
            b = round(moist_std + rng.uniform(0.5, 3.5) if wet else moist_std - rng.uniform(1.5, 9), 1)
            call("POST", f"/moisture/{moist['id']}/rolls", rng.choice(WORKERS), {
                "roll_no": r["roll_no"], "color": c["colour_code"], "fabric_composition": receive.get("fabric_composition"),
                "swatch_rating_before": b, "swatch_rating_after": round(b - rng.uniform(0.5, 2.5), 1) if after else None,
                "before_wash_date": before_day.isoformat(), "after_wash_date": after_day.isoformat() if after else None, "roll_status": status,
            })

        skew_std = minfo[10]
        skew = call("POST", "/skew-bowing", inspector, {"plm_id": plm["id"], "report_date": before_day.isoformat(),
                                                       "comments_action_plan": "Monitor bias on next lot" if quality < 0.8 else None})
        for c, r in sample:
            base = r["marked_width"] * 1.235

            def diff():
                return skew_std * (rng.uniform(1.1, 1.6) if rng.random() < (1 - quality) * 0.6 else rng.uniform(0.1, 0.9))

            d1, d2 = diff(), diff()
            call("POST", f"/skew-bowing/{skew['id']}/rolls", rng.choice(WORKERS), {
                "roll_no": r["roll_no"], "colour_code": c["colour_code"],
                "ac_value_before": round(base + d1 / 2, 2), "bd_value_before": round(base - d1 / 2, 2),
                "ac_value_after": round(base + d2 / 2, 2) if after else None, "bd_value_after": round(base - d2 / 2, 2) if after else None,
                "before_wash_date": before_day.isoformat(), "after_wash_date": after_day.isoformat() if after else None, "roll_status": status,
            })

        shade = call("POST", "/shade-check", inspector, {"plm_id": plm["id"], "comments": "Checked under D65 light box"})
        for c, r in sample:
            results = [{"point_definition_id": pt["id"], "first_ok": rng.random() > 0.04, "middle_ok": rng.random() > 0.06 * (2 - quality),
                        "last_ok": rng.random() > 0.05} for pt in shade["points_snapshot"]]
            call("POST", f"/shade-check/{shade['id']}/rolls", rng.choice(WORKERS), {
                "roll_no": r["roll_no"], "colour": c["colour_code"], "roll_date": before_day.isoformat(), "results": results,
            })

        if in_progress:
            continue

        relax = call("POST", "/relaxation", inspector, {"plm_id": plm["id"], "color": colours_txt, "comments": "Relaxed on open table"})
        for c, r in sample[:4]:
            bl, bw = 50.0, float(r["marked_width"])
            call("POST", f"/relaxation/{relax['id']}/rolls", rng.choice(WORKERS), {
                "roll_no": r["roll_no"], "color_code": c["colour_code"], "fabric_yds_mtr": r["marked_length"], "roll_date": before_day.isoformat(),
                "before_relax_start_time": "09:00", "before_relax_length": bl, "before_relax_width": bw,
                "after_relax_time": "21:00", "after_relax_length": round(bl * (1 - rng.uniform(0.2, 1.8) / 100), 2),
                "after_relax_width": round(bw * (1 - rng.uniform(0.0, 1.4) / 100), 2), "relaxation_hours": 12,
            })

        shrink = call("POST", "/shrinkage", inspector, {"plm_id": plm["id"], "season": "Winter 26" if start_day.month >= 8 else "Summer 26",
                                                       "color": colours_txt, "qc_name": "Nasrin", "report_date": after_day.isoformat()})
        for c, r in sample[:4]:
            call("POST", f"/shrinkage/{shrink['id']}/rolls", rng.choice(WORKERS), {
                "roll_no": r["roll_no"], "yds": r["marked_length"], "pattern": rng.choice(["Body", "Sleeve", "Collar"]), "shade": "A",
                "before_wash_date": before_day.isoformat(), "after_wash_date": after_day.isoformat(), "roll_status": "after_wash_done",
                "measurements": [{"reading_order": i + 1, "before_length": 50, "before_width": 50,
                                  "after_length": round(50 * (1 - rng.uniform(1.0, 4.5) / 100), 2),
                                  "after_width": round(50 * (1 + rng.uniform(-3.0, 2.0) / 100), 2)} for i in range(3)],
            })

        card = call("POST", "/color-continuity", inspector, {"plm_id": plm["id"]})
        for c, r in rolls_flat:
            g = rng.choices(["A", "B", "C"], [7, 3, 1])[0]
            call("POST", f"/color-continuity/{card['id']}/rolls", rng.choice(WORKERS), {
                "roll_no": r["roll_no"], "shade_group_before": g, "shade_group_after": g if rng.random() > 0.1 else rng.choice("AB"),
                "yardage": r["marked_length"], "before_wash_date": before_day.isoformat(), "after_wash_date": after_day.isoformat(),
                "roll_status": "after_wash_done",
            })

    print(f"  {receive['consignment']}  {spec[0]:<24} {len(plans)} PLM  {'(in progress today)' if tail == 2 else '(scheduled tomorrow)' if tail == 1 else ''}")

# supplier communication rating, last 6 months
for sup_id, spec in ids["suppliers"]:
    for back in range(3):
        y, m = TODAY.year, TODAY.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        rating = spec[9] + (spec[8] - spec[9]) * back / 2 + rng.uniform(-0.2, 0.2)
        call("PUT", "/analytics/supplier-monthly-inputs", ADMIN, {
            "supplier_id": sup_id, "year": y, "month": m, "communication_rating": round(min(5, max(1, rating)), 1),
        })

# ---------------------------------------------------------------- realistic timestamps
with engine.begin() as conn:
    q = conn.execute
    q(text("update fabric_receives set created_at = received_at + time '09:00', updated_at = received_at + time '09:00'"))
    q(text("update plms set created_at = plm_date + time '10:00', updated_at = plm_date + time '10:00'"))
    q(text("update plm_colors c set created_at = p.created_at, updated_at = p.created_at from plms p where c.plm_id = p.id"))
    q(text("update plm_rolls r set created_at = c.created_at, updated_at = c.created_at from plm_colors c where r.plm_color_id = c.id"))
    q(text("update inspection_schedules s set created_at = p.created_at, updated_at = p.created_at from plms p where s.plm_id = p.id"))
    q(text("update fabric_inspection_rolls set created_at = inspected_at, updated_at = inspected_at + interval '2 day' * (disposition <> 'pending')::int"))
    q(text("update fabric_inspection_roll_defects d set created_at = r.inspected_at, updated_at = r.inspected_at from fabric_inspection_rolls r where d.roll_id = r.id"))

    # every per-PLM report starts on the PLM's first inspection day; its rolls follow it
    first_day = "(select s.scheduled_date + time '09:30' from inspection_schedules s where s.plm_id = t.plm_id order by s.scheduled_date limit 1)"
    for table in Base.metadata.sorted_tables:
        if "plm_id" in table.c and "created_at" in table.c and table.name not in ("plms", "plm_colors", "inspection_schedules", "audit_logs", "attachments"):
            q(text(f"update {table.name} t set created_at = coalesce({first_day}, t.created_at)"
                   + (f", updated_at = coalesce({first_day}, t.updated_at)" if "updated_at" in table.c else "")))
            for child in Base.metadata.sorted_tables:
                fks = [fk for fk in child.foreign_keys if fk.column.table.name == table.name]
                if child.name == "fabric_inspection_rolls" or not fks or "created_at" not in child.c:
                    continue
                col = fks[0].parent.name
                q(text(f"update {child.name} c set created_at = p.created_at + (random() * interval '7 hour')"
                       + (", updated_at = p.created_at + interval '8 hour'" if "updated_at" in child.c else "")
                       + f" from {table.name} p where c.{col} = p.id"))

    q(text("update audit_logs a set created_at = r.inspected_at from fabric_inspection_rolls r where a.entity_id = r.id and a.action <> 'roll_disposition'"))
    q(text("update audit_logs a set created_at = r.inspected_at + interval '2 day' from fabric_inspection_rolls r where a.entity_id = r.id and a.action = 'roll_disposition'"))
    q(text("update audit_logs a set created_at = f.created_at from fabric_receives f where a.entity_id = f.id"))
    q(text("update audit_logs a set created_at = s.created_at from inspection_schedules s where a.entity_id = s.id and a.action = 'schedule_created'"))
    q(text("update audit_logs a set created_at = s.scheduled_date + time '09:00' from inspection_schedules s where a.entity_id = s.id and a.action = 'schedule_status'"))
    q(text("update audit_logs a set created_at = p.created_at from plms p where a.entity_id = p.id"))

invalidate_prefix("analytics")

pass_rate = 100 * (stats["inspected"] - stats["failed"]) / max(1, stats["inspected"])
print(f"\nDEMO_SEED_OK  consignments={stats['receives']} plms={stats['plms']} planned_rolls={stats['rolls']} "
      f"inspected={stats['inspected']} failed={stats['failed']} pass_rate={pass_rate:.1f}%")
