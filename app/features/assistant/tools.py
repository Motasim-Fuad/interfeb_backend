"""Assistant tools. Each returns a Reply made of UI blocks:

text | stats | table (preview + dataset handle) | chart | actions | entity | notice
The dashboard renders the blocks; "View all", CSV and Print page through the dataset."""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.features.analytics import insights
from app.features.assistant import vector_store
from app.features.assistant.datasets import DATASETS, _d, run_dataset
from app.features.assistant.knowledge import MODULE_LABELS
from app.features.color_continuity.models import ColorContinuityCard
from app.features.fabric_inspection.models import FabricInspectionReport
from app.features.gsm.models import GsmCheckReport
from app.features.moisture.models import MoistureCheckReport
from app.features.plm.models import Plm, PlmColor, PlmRoll
from app.features.relaxation.models import FabricRelaxationReport
from app.features.shade_check.models import ShadeCheckReport
from app.features.shrinkage.models import ShrinkagePatternReport
from app.features.skew_bowing.models import SkewBowingReport
from app.core.clock import factory_today

PREVIEW_ROWS = 8
DETAIL_PREVIEW_ROWS = 25

MODULE_REPORT_MODELS = {
    "fabric-inspection": FabricInspectionReport,
    "gsm": GsmCheckReport,
    "moisture": MoistureCheckReport,
    "skew-bowing": SkewBowingReport,
    "relaxation": FabricRelaxationReport,
    "shrinkage": ShrinkagePatternReport,
    "shade-check": ShadeCheckReport,
    "color-continuity": ColorContinuityCard,
}
DERIVED_FROM_FOUR_POINT = ("correlation", "factory-report")


@dataclass
class Reply:
    text: str
    blocks: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)


def tr(lang: str, en: str, bn: str) -> str:
    return bn if lang == "bn" else en


def _fmt_pct(v) -> str:
    return "-" if v is None else f"{v:g}%"


_PERIOD_BN = {"today": "আজ", "yesterday": "গতকাল", "tomorrow": "আগামীকাল", "this week": "এই সপ্তাহ", "last week": "গত সপ্তাহ"}


def _scope_label(a: dict, lang: str) -> str:
    parts = []
    for key in ("supplier_name", "buyer_name", "plm_name", "material_name"):
        if a.get(key):
            parts.append(a[key])
    if a.get("schedule_status"):
        parts.append(tr(lang, f"schedule {a['schedule_status'].replace('_', ' ')}", f"শিডিউল {a['schedule_status'].replace('_', ' ')}"))
    if a.get("period_label"):
        label = a["period_label"]
        parts.append(_PERIOD_BN.get(label, label) if lang == "bn" else label)
    return " • ".join(parts) if parts else tr(lang, "all time", "সব সময়")


def stats(items: list[dict]) -> dict:
    return {"type": "stats", "items": items}


def stat(label: str, value, tone: str = "neutral", hint: Optional[str] = None) -> dict:
    return {"label": label, "value": "-" if value is None else value, "tone": tone, "hint": hint}


def actions(items: list[dict]) -> dict:
    return {"type": "actions", "items": [i for i in items if i]}


def act_dataset(label: str, name: str, args: dict) -> dict:
    return {"kind": "dataset", "label": label, "dataset": {"name": name, "args": args}, "icon": "table"}


def act_print(label: str, name: str, args: dict) -> dict:
    return {"kind": "dataset_pdf", "label": label, "dataset": {"name": name, "args": args}, "icon": "print"}


def act_report(module: str, plm_id: str, plm_code: str) -> dict:
    return {"kind": "report_pdf", "label": f"{MODULE_LABELS.get(module, module)} • {plm_code}", "module": module,
            "plm_id": plm_id, "icon": "pdf"}


def act_prompt(label: str, text: str) -> dict:
    return {"kind": "prompt", "label": label, "text": text, "icon": "spark"}


def act_nav(label: str, route: str) -> dict:
    return {"kind": "navigate", "label": label, "route": route, "icon": "open"}


def table_block(name: str, args: dict, *, detail: bool = False, title: Optional[str] = None, sort: Optional[str] = None,
                desc: bool = False, limit: Optional[int] = None, db: Session) -> tuple[dict, dict]:
    page = run_dataset(db, name, args, sort=sort, desc=desc, limit=limit or (DETAIL_PREVIEW_ROWS if detail else PREVIEW_ROWS))
    block = {"type": "table", "title": title or page["title"], "columns": page["columns"], "rows": page["rows"],
             "total": page["total"], "dataset": {"name": name, "args": args, "sort": sort, "desc": desc}}
    return block, page


def _list_actions(lang: str, name: str, args: dict, total: int, shown: int) -> dict:
    items = []
    if total > shown:
        items.append(act_dataset(tr(lang, f"View all {total}", f"সব {total}টি দেখুন"), name, args))
    else:
        items.append(act_dataset(tr(lang, "Open in viewer", "ভিউয়ারে খুলুন"), name, args))
    items.append(act_print(tr(lang, "Print / PDF", "প্রিন্ট / PDF"), name, args))
    return actions(items)


def _clean(args: dict, *keys) -> dict:
    return {k: v for k, v in args.items() if k in keys and v not in (None, "")}


# ---------------------------------------------------------------- tools

def tool_overview(db: Session, a: dict, lang: str) -> Reply:
    f = dict(buyer_id=a.get("buyer_id"), supplier_id=a.get("supplier_id"), plm_id=a.get("plm_id"),
             date_from=_d(a.get("date_from")), date_to=_d(a.get("date_to")))
    k = insights.kpis(db, **f)
    scope = _scope_label(a, lang)
    if not k["rolls_inspected"]:
        text = tr(lang, f"No inspected rolls for {scope} yet.", f"{scope} — এখনো কোনো রোল ইন্সপেক্ট হয়নি।")
    else:
        text = tr(lang,
                  f"Quality summary for **{scope}**: {k['rolls_inspected']} rolls inspected, pass rate **{_fmt_pct(k['pass_rate'])}**, "
                  f"{k['failed']} failed ({k['pending_disposition']} waiting for a decision).",
                  f"**{scope}** এর কোয়ালিটি সারাংশ: {k['rolls_inspected']}টি রোল ইন্সপেক্ট হয়েছে, পাস রেট **{_fmt_pct(k['pass_rate'])}**, "
                  f"{k['failed']}টি ফেল (এর মধ্যে {k['pending_disposition']}টির সিদ্ধান্ত বাকি)।")
    rate = k["pass_rate"]
    blocks = [
        stats([
            stat(tr(lang, "Rolls inspected", "ইন্সপেক্টেড রোল"), k["rolls_inspected"], "info"),
            stat(tr(lang, "Pass rate", "পাস রেট"), _fmt_pct(rate), "success" if (rate or 0) >= 90 else "warning" if (rate or 0) >= 75 else "danger"),
            stat(tr(lang, "Failed", "ফেল"), k["failed"], "danger" if k["failed"] else "neutral"),
            stat(tr(lang, "Pts / 100 yd²", "পয়েন্ট / ১০০ বর্গগজ"), k["points_per_100_sq_yd"], "neutral"),
            stat(tr(lang, "On hold", "হোল্ড"), k["hold"], "warning" if k["hold"] else "neutral"),
            stat(tr(lang, "Decision pending", "সিদ্ধান্ত বাকি"), k["pending_disposition"], "warning" if k["pending_disposition"] else "neutral"),
            stat(tr(lang, "Rolls today", "আজ রোল"), k["rolls_today"], "info"),
            stat(tr(lang, "Schedules pending", "পেন্ডিং সিডিউল"), k["schedules_pending"], "neutral"),
        ]),
    ]
    if k["passed"] or k["failed"]:
        blocks.append({"type": "chart", "kind": "donut", "title": tr(lang, "Pass vs fail", "পাস বনাম ফেল"),
                       "series": [{"label": "Pass", "value": k["passed"], "tone": "success"},
                                  {"label": "Fail", "value": k["failed"], "tone": "danger"}]})
    base = _clean(a, "buyer_id", "supplier_id", "plm_id", "date_from", "date_to", "period_label", "supplier_name", "buyer_name")
    blocks.append(actions([
        act_dataset(tr(lang, "Failed rolls", "ফেল রোলগুলো"), "rolls", dict(base, is_pass=False)) if k["failed"] else None,
        act_prompt(tr(lang, "Top defects", "শীর্ষ ত্রুটি"), tr(lang, "top defects", "কোন ডিফেক্ট বেশি")),
        act_prompt(tr(lang, "Supplier scorecard", "সাপ্লায়ার স্কোর"), "supplier scorecard"),
        act_nav(tr(lang, "Open Analysis Report", "অ্যানালাইসিস রিপোর্ট খুলুন"), "/analytics"),
    ]))
    return Reply(text, blocks, [tr(lang, "Show failed rolls this month", "এই মাসের ফেল রোল দাও"),
                                tr(lang, "Pass rate trend", "পাস রেট ট্রেন্ড"), tr(lang, "Today's schedule", "আজকের সিডিউল")])


def _simple_list(db: Session, a: dict, lang: str, name: str, keys: tuple, sort: Optional[str], desc: bool,
                 headline, stat_fn, suggestions: list[str]) -> Reply:
    args = _clean(a, *keys)
    block, page = table_block(name, args, detail=a.get("detail", False), sort=sort, desc=desc, db=db)
    blocks = []
    st = stat_fn(page) if stat_fn else None
    if st:
        blocks.append(st)
    blocks.append(block)
    blocks.append(_list_actions(lang, name, args, page["total"], len(page["rows"])))
    return Reply(headline(page), blocks, suggestions)


def tool_list_buyers(db: Session, a: dict, lang: str) -> Reply:
    def headline(p):
        active = sum(1 for r in p["rows"] if r["status"] == "Active") if p["total"] == len(p["rows"]) else None
        top = sorted(run_dataset(db, "buyers", {}, limit=1000)["rows"], key=lambda r: -r["plms"])[:1]
        lead = f" {top[0]['name']} has the most PLMs ({top[0]['plms']})." if top and top[0]["plms"] else ""
        lead_bn = f" সবচেয়ে বেশি PLM {top[0]['name']} এর ({top[0]['plms']}টি)।" if top and top[0]["plms"] else ""
        return tr(lang, f"There are **{p['total']} buyers**" + (f" ({active} active)." if active is not None else ".") + lead,
                  f"মোট **{p['total']} জন বায়ার** আছে" + (f" ({active} জন সক্রিয়)।" if active is not None else "।") + lead_bn)

    def st(p):
        rows = run_dataset(db, "buyers", {}, limit=1000)["rows"]
        return stats([stat(tr(lang, "Buyers", "বায়ার"), p["total"], "info"),
                      stat(tr(lang, "Active", "সক্রিয়"), sum(1 for r in rows if r["status"] == "Active"), "success"),
                      stat(tr(lang, "PLMs", "PLM"), sum(r["plms"] for r in rows), "neutral"),
                      stat(tr(lang, "Rolls inspected", "ইন্সপেক্টেড রোল"), sum(r["rolls"] for r in rows), "neutral")])
    return _simple_list(db, a, lang, "buyers", ("date_from", "date_to"), "plms", True, headline, st,
                        [tr(lang, "PLMs of the first buyer", "এই বায়ারের PLM দাও"), tr(lang, "Supplier list", "সাপ্লায়ারের তালিকা")])


def tool_list_suppliers(db: Session, a: dict, lang: str) -> Reply:
    def headline(p):
        rows = run_dataset(db, "suppliers", _clean(a, "date_from", "date_to"), limit=1000)["rows"]
        graded = [r for r in rows if r["pass_rate"] is not None]
        best = max(graded, key=lambda r: r["pass_rate"]) if graded else None
        missing = sum(1 for r in rows if r["qc_profile"] == "Missing")
        en = f"There are **{p['total']} suppliers**."
        bn = f"মোট **{p['total']}টি সাপ্লায়ার** আছে।"
        if best:
            en += f" Best pass rate: **{best['name']}** ({_fmt_pct(best['pass_rate'])})."
            bn += f" সবচেয়ে ভালো পাস রেট: **{best['name']}** ({_fmt_pct(best['pass_rate'])})।"
        if missing:
            en += f" {missing} still need a QC profile."
            bn += f" {missing}টির QC প্রোফাইল এখনো নেই।"
        return tr(lang, en, bn)

    def st(p):
        rows = run_dataset(db, "suppliers", _clean(a, "date_from", "date_to"), limit=1000)["rows"]
        return stats([stat(tr(lang, "Suppliers", "সাপ্লায়ার"), p["total"], "info"),
                      stat(tr(lang, "QC profile ready", "QC প্রোফাইল আছে"), sum(1 for r in rows if r["qc_profile"] == "Configured"), "success"),
                      stat(tr(lang, "Grade A", "গ্রেড A"), sum(1 for r in rows if r["grade"] == "A"), "success"),
                      stat(tr(lang, "Grade C/D", "গ্রেড C/D"), sum(1 for r in rows if r["grade"] in ("C", "D")), "danger")])
    return _simple_list(db, a, lang, "suppliers", ("date_from", "date_to"), "pass_rate", True, headline, st,
                        [tr(lang, "Supplier scorecard", "সাপ্লায়ার স্কোরকার্ড"), tr(lang, "Worst supplier this month", "এই মাসে কোন সাপ্লায়ার খারাপ")])


def tool_list_plms(db: Session, a: dict, lang: str) -> Reply:
    args = _clean(a, "buyer_id", "supplier_id", "material_id", "consignment_id", "date_from", "date_to",
                  "period_label", "supplier_name", "buyer_name", "schedule_status")
    block, page = table_block("plms", args, detail=a.get("detail", False), db=db)
    all_rows = run_dataset(db, "plms", args, limit=5000)["rows"]
    scope = _scope_label(a, lang)
    inspected = sum(r["inspected"] for r in all_rows)
    failed = sum(r["failed"] for r in all_rows)
    not_started = sum(1 for r in all_rows if not r["inspected"])
    text = tr(lang,
              f"**{page['total']} PLMs** for {scope}: {inspected} rolls inspected, {failed} failed, {not_started} PLM(s) not inspected yet.",
              f"{scope} — মোট **{page['total']}টি PLM**: {inspected}টি রোল ইন্সপেক্টেড, {failed}টি ফেল, {not_started}টি PLM এ এখনো ইন্সপেকশন শুরু হয়নি।")
    if not page["total"]:
        text = tr(lang, f"No PLMs found for {scope}.", f"{scope} — কোনো PLM পাওয়া যায়নি।")
    blocks = [stats([
        stat("PLMs", page["total"], "info"),
        stat(tr(lang, "Planned rolls", "প্ল্যানড রোল"), sum(r["planned_rolls"] for r in all_rows)),
        stat(tr(lang, "Inspected", "ইন্সপেক্টেড"), inspected, "success" if inspected else "neutral"),
        stat(tr(lang, "Failed", "ফেল"), failed, "danger" if failed else "neutral"),
    ]), block, _list_actions(lang, "plms", args, page["total"], len(page["rows"]))]
    if a.get("group_by") and all_rows:
        blocks[1:1] = _group_blocks(all_rows, a["group_by"], lang)
    first = page["rows"][0]["plm_code"] if page["rows"] else None
    sugg = [tr(lang, f"{first} details", f"{first} এর বিস্তারিত"), tr(lang, f"4 point report {first}", f"{first} এর ৪ পয়েন্ট প্রিন্ট")] if first else []
    return Reply(text, blocks, sugg + [tr(lang, "Failed rolls of these PLMs", "এই PLM গুলোর ফেল রোল")])


def _group_blocks(rows: list[dict], by: str, lang: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in rows:
        name = r.get(by) or tr(lang, "Unassigned", "নির্ধারিত নয়")
        g = groups.setdefault(name, {"name": name, "plms": 0, "inspected": 0, "failed": 0})
        g["plms"] += 1
        g["inspected"] += r["inspected"]
        g["failed"] += r["failed"]
    ordered = sorted(groups.values(), key=lambda g: (-g["plms"], g["name"]))
    for g in ordered:
        g["pass_rate"] = round((g["inspected"] - g["failed"]) / g["inspected"] * 100, 1) if g["inspected"] else None
    label = tr(lang, "Supplier", "সাপ্লায়ার") if by == "supplier" else tr(lang, "Buyer", "বায়ার")
    chart = {"type": "chart", "kind": "bar", "title": tr(lang, f"PLMs by {label.lower()}", f"{label} অনুযায়ী PLM"),
             "series": [{"label": g["name"], "value": g["plms"], "tone": "info"} for g in ordered[:12]]}
    table = {"type": "table", "title": tr(lang, f"By {label.lower()}", f"{label} অনুযায়ী"), "total": len(ordered), "dataset": None,
             "columns": [{"key": "name", "label": label, "kind": "text"}, {"key": "plms", "label": "PLMs", "kind": "number"},
                         {"key": "inspected", "label": tr(lang, "Inspected", "ইন্সপেক্টেড"), "kind": "number"},
                         {"key": "failed", "label": tr(lang, "Failed", "ফেল"), "kind": "number"},
                         {"key": "pass_rate", "label": tr(lang, "Pass rate", "পাস রেট"), "kind": "percent"}],
             "rows": ordered}
    return [chart, table]


def _module_status(db: Session, plm_id) -> dict:
    out = {}
    for module, model in MODULE_REPORT_MODELS.items():
        out[module] = int(db.query(func.count(model.id)).filter(model.plm_id == plm_id).scalar() or 0)
    for module in DERIVED_FROM_FOUR_POINT:
        out[module] = out["fabric-inspection"]
    return out


def tool_plm_detail(db: Session, a: dict, lang: str) -> Reply:
    plm_id = a.get("plm_id")
    if not plm_id:
        return _pick_plm(db, a, lang, tr(lang, "Which PLM? Pick one:", "কোন PLM? একটি বেছে নিন:"), "{code} details")
    plm = db.get(Plm, plm_id)
    if not plm:
        return Reply(tr(lang, "That PLM no longer exists.", "PLM টি আর নেই।"))
    k = insights.kpis(db, plm_id=plm.id)
    planned = db.query(func.count(PlmRoll.id)).join(PlmColor, PlmColor.id == PlmRoll.plm_color_id).filter(PlmColor.plm_id == plm.id).scalar() or 0
    colors = [c.colour_code for c in plm.colors]
    status = _module_status(db, plm.id)
    done = [m for m, n in status.items() if n]
    text = tr(lang,
              f"**{plm.plm_code}** ({plm.buyer_name or '-'} • {plm.supplier_name or '-'}): {k['rolls_inspected']}/{planned} rolls inspected, "
              f"pass rate **{_fmt_pct(k['pass_rate'])}**. {len(done)} report(s) ready.",
              f"**{plm.plm_code}** ({plm.buyer_name or '-'} • {plm.supplier_name or '-'}): {planned}টির মধ্যে {k['rolls_inspected']}টি রোল ইন্সপেক্টেড, "
              f"পাস রেট **{_fmt_pct(k['pass_rate'])}**। {len(done)}টি রিপোর্ট প্রস্তুত।")
    entity = {"type": "entity", "title": plm.plm_code, "subtitle": plm.plm_style or plm.material_display_name or "",
              "fields": [
                  {"label": tr(lang, "Buyer", "বায়ার"), "value": plm.buyer_name},
                  {"label": tr(lang, "Supplier", "সাপ্লায়ার"), "value": plm.supplier_name},
                  {"label": tr(lang, "Consignment", "কনসাইনমেন্ট"), "value": plm.consignment},
                  {"label": tr(lang, "Material", "ম্যাটেরিয়াল"), "value": plm.material_display_name},
                  {"label": tr(lang, "PLM date", "PLM তারিখ"), "value": plm.plm_date.isoformat()},
                  {"label": tr(lang, "Colours", "কালার"), "value": ", ".join(colors) or "-"},
                  {"label": tr(lang, "Unit", "ইউনিট"), "value": f"{(plm.measurement_unit or '').upper()} / {(plm.quantity_unit or '').upper()}"},
                  {"label": tr(lang, "QC setup", "QC সেটআপ"), "value": f"v{plm.qc_setup_version}"},
              ],
              "modules": [{"module": m, "label": MODULE_LABELS[m], "count": n, "ready": bool(n)} for m, n in status.items()]}
    blocks = [entity, stats([
        stat(tr(lang, "Planned rolls", "প্ল্যানড রোল"), planned),
        stat(tr(lang, "Inspected", "ইন্সপেক্টেড"), k["rolls_inspected"], "info"),
        stat(tr(lang, "Pass rate", "পাস রেট"), _fmt_pct(k["pass_rate"]), "success" if (k["pass_rate"] or 0) >= 90 else "warning"),
        stat(tr(lang, "Failed", "ফেল"), k["failed"], "danger" if k["failed"] else "neutral"),
    ])]
    rargs = {"plm_id": str(plm.id), "plm_name": plm.plm_code}
    if k["rolls_inspected"]:
        block, page = table_block("rolls", rargs, detail=a.get("detail", False), title=tr(lang, "Inspected rolls", "ইন্সপেক্টেড রোল"), db=db)
        blocks.append(block)
    report_buttons = [act_report(m, str(plm.id), plm.plm_code) for m in done]
    if report_buttons:
        blocks.append({"type": "actions", "title": tr(lang, "Print reports", "রিপোর্ট প্রিন্ট"), "items": report_buttons})
    if k["rolls_inspected"]:
        blocks.append(actions([act_dataset(tr(lang, "All rolls", "সব রোল"), "rolls", rargs),
                               act_dataset(tr(lang, "Defect Pareto", "ডিফেক্ট পারেটো"), "defect_pareto", rargs)]))
    return Reply(text, blocks, [tr(lang, f"Failed rolls of {plm.plm_code}", f"{plm.plm_code} এর ফেল রোল"),
                                tr(lang, f"Top defects in {plm.plm_code}", f"{plm.plm_code} এ কোন ডিফেক্ট বেশি")])


def _pick_plm(db: Session, a: dict, lang: str, question: str, template: str) -> Reply:
    args = _clean(a, "buyer_id", "supplier_id", "date_from", "date_to")
    rows = run_dataset(db, "plms", args, limit=8)["rows"]
    if not rows:
        return Reply(tr(lang, "I couldn't find any PLM. Try a PLM code like MNS-489.", "কোনো PLM পাইনি। PLM কোড লিখুন, যেমন MNS-489।"))
    items = [act_prompt(f"{r['plm_code']} • {r['supplier'] or '-'}", template.format(code=r["plm_code"])) for r in rows]
    return Reply(question, [{"type": "actions", "title": tr(lang, "Recent PLMs", "সাম্প্রতিক PLM"), "items": items}])


def tool_report(db: Session, a: dict, lang: str) -> Reply:
    module = a.get("module")
    plm_id = a.get("plm_id")
    if not plm_id:
        label = MODULE_LABELS.get(module, tr(lang, "report", "রিপোর্ট"))
        tmpl = f"{label} report {{code}}" if module else "{code} reports"
        return _pick_plm(db, a, lang, tr(lang, f"Which PLM's {label}?", f"কোন PLM এর {label}?"), tmpl)
    plm = db.get(Plm, plm_id)
    status = _module_status(db, plm.id)
    if module:
        if not status.get(module):
            return Reply(tr(lang, f"{MODULE_LABELS[module]} has no data yet for {plm.plm_code}, so there is nothing to print.",
                            f"{plm.plm_code} এর {MODULE_LABELS[module]} এ এখনো ডাটা নেই, তাই প্রিন্ট করার কিছু নেই।"),
                         [{"type": "actions", "title": tr(lang, "Available reports", "যেসব রিপোর্ট আছে"),
                           "items": [act_report(m, str(plm.id), plm.plm_code) for m, n in status.items() if n]}])
        return Reply(tr(lang, f"Here is the **{MODULE_LABELS[module]}** report for **{plm.plm_code}**. It's the same backend PDF used by the app.",
                        f"**{plm.plm_code}** এর **{MODULE_LABELS[module]}** রিপোর্ট। অ্যাপ যে PDF প্রিন্ট করে, হুবহু সেটাই।"),
                     [{"type": "actions", "items": [act_report(module, str(plm.id), plm.plm_code)], "primary": True}],
                     [tr(lang, f"{plm.plm_code} details", f"{plm.plm_code} এর বিস্তারিত")])
    ready = [m for m, n in status.items() if n]
    return Reply(tr(lang, f"{len(ready)} report(s) are ready for **{plm.plm_code}**.", f"**{plm.plm_code}** এর {len(ready)}টি রিপোর্ট প্রস্তুত।"),
                 [{"type": "actions", "title": tr(lang, "Print reports", "রিপোর্ট প্রিন্ট"),
                   "items": [act_report(m, str(plm.id), plm.plm_code) for m in ready]}])


def tool_list_rolls(db: Session, a: dict, lang: str) -> Reply:
    args = _clean(a, "buyer_id", "supplier_id", "plm_id", "date_from", "date_to", "is_pass", "disposition",
                  "period_label", "supplier_name", "buyer_name", "plm_name")
    block, page = table_block("rolls", args, detail=a.get("detail", False), db=db)
    scope = _scope_label(a, lang)
    what_en = "failed rolls" if a.get("is_pass") is False else "passed rolls" if a.get("is_pass") else "inspected rolls"
    what_bn = "ফেল রোল" if a.get("is_pass") is False else "পাস রোল" if a.get("is_pass") else "ইন্সপেক্টেড রোল"
    if a.get("disposition"):
        what_en = f"{a['disposition']} rolls"
        what_bn = f"{a['disposition']} রোল"
    rows = run_dataset(db, "rolls", args, limit=5000)["rows"]
    pending = sum(1 for r in rows if r["disposition"] == "Pending" and r["result"] == "FAIL")
    text = tr(lang, f"**{page['total']} {what_en}** for {scope}." + (f" {pending} still need a decision." if pending else ""),
              f"{scope} — **{page['total']}টি {what_bn}**।" + (f" {pending}টির সিদ্ধান্ত এখনো বাকি।" if pending else ""))
    blocks = [stats([
        stat(tr(lang, "Rolls", "রোল"), page["total"], "info"),
        stat("PASS", sum(1 for r in rows if r["result"] == "PASS"), "success"),
        stat("FAIL", sum(1 for r in rows if r["result"] == "FAIL"), "danger"),
        stat(tr(lang, "Decision pending", "সিদ্ধান্ত বাকি"), pending, "warning" if pending else "neutral"),
    ]), block, _list_actions(lang, "rolls", args, page["total"], len(page["rows"]))]
    if pending:
        blocks[-1]["items"].append(act_nav(tr(lang, "Decide in Roll Decisions", "রোল ডিসিশনে সিদ্ধান্ত দিন"), "/quality/roll-decisions"))
    return Reply(text, blocks, [tr(lang, "Top defects for these rolls", "এগুলোর শীর্ষ ত্রুটি"), tr(lang, "Rolls on hold", "হোল্ডে থাকা রোল")])


def tool_defect_pareto(db: Session, a: dict, lang: str) -> Reply:
    args = _clean(a, "buyer_id", "supplier_id", "plm_id", "date_from", "date_to", "period_label", "supplier_name", "buyer_name", "plm_name")
    block, page = table_block("defect_pareto", args, detail=True, db=db, limit=a.get("limit") or 10)
    scope = _scope_label(a, lang)
    if not page["total"]:
        return Reply(tr(lang, f"No defects recorded for {scope}.", f"{scope} — কোনো ত্রুটি রেকর্ড হয়নি।"))
    top = page["rows"][0]
    vital = sum(1 for r in run_dataset(db, "defect_pareto", args, limit=100)["rows"] if r["cumulative"] - r["percent"] < 80)
    text = tr(lang, f"Top defect for {scope} is **{top['name']}** with {top['points']} points ({_fmt_pct(top['percent'])}). "
                    f"{vital} defect type(s) make up about 80% of all points.",
              f"{scope} — সবচেয়ে বেশি ত্রুটি **{top['name']}**, {top['points']} পয়েন্ট ({_fmt_pct(top['percent'])})। "
              f"{vital}টি ধরন মিলে প্রায় ৮০% পয়েন্ট।")
    chart = {"type": "chart", "kind": "bar", "title": tr(lang, "Points by defect", "ত্রুটি অনুযায়ী পয়েন্ট"),
             "series": [{"label": r["name"], "value": r["points"], "tone": "danger" if i == 0 else "info"} for i, r in enumerate(page["rows"][:10])]}
    return Reply(text, [chart, block, _list_actions(lang, "defect_pareto", args, page["total"], len(page["rows"]))],
                 [tr(lang, f"Rolls with {top['name']}", f"{top['name']} আছে এমন রোল"), tr(lang, "Supplier scorecard", "সাপ্লায়ার স্কোরকার্ড")])


def tool_supplier_scorecard(db: Session, a: dict, lang: str) -> Reply:
    args = _clean(a, "buyer_id", "date_from", "date_to", "period_label", "buyer_name")
    block, page = table_block("scorecard", args, detail=True, db=db, limit=a.get("limit") or 10)
    if not page["total"]:
        return Reply(tr(lang, "No inspected rolls yet, so no supplier scores.", "এখনো কোনো রোল ইন্সপেক্ট হয়নি, তাই স্কোর নেই।"))
    rows = page["rows"]
    best, worst = rows[0], rows[-1]
    if len(rows) == 1:
        text = tr(lang, f"Only **{best['name']}** has inspected rolls in this period: grade {best['grade']}, score {best['score']}, "
                        f"pass rate {_fmt_pct(best['pass_rate'])}.",
                  f"এই সময়ে শুধু **{best['name']}** এর রোল ইন্সপেক্ট হয়েছে: গ্রেড {best['grade']}, স্কোর {best['score']}, "
                  f"পাস রেট {_fmt_pct(best['pass_rate'])}।")
    else:
        text = None
    text = text or tr(lang, f"Best supplier: **{best['name']}** (grade {best['grade']}, score {best['score']}). "
                    f"Needs attention: **{worst['name']}** (grade {worst['grade']}, pass rate {_fmt_pct(worst['pass_rate'])}).",
              f"সেরা সাপ্লায়ার: **{best['name']}** (গ্রেড {best['grade']}, স্কোর {best['score']})। "
              f"নজর দরকার: **{worst['name']}** (গ্রেড {worst['grade']}, পাস রেট {_fmt_pct(worst['pass_rate'])})।")
    chart = {"type": "chart", "kind": "bar", "title": tr(lang, "Supplier score", "সাপ্লায়ার স্কোর"), "max": 100,
             "series": [{"label": r["name"], "value": r["score"] or 0,
                         "tone": "success" if r["grade"] == "A" else "info" if r["grade"] == "B" else "warning" if r["grade"] == "C" else "danger"}
                        for r in rows]}
    return Reply(text, [chart, block, _list_actions(lang, "scorecard", args, page["total"], len(rows))],
                 [tr(lang, f"Failed rolls of {worst['name']}", f"{worst['name']} এর ফেল রোল"), tr(lang, f"PLMs of {best['name']}", f"{best['name']} এর PLM")])


def tool_trend(db: Session, a: dict, lang: str) -> Reply:
    df, dt = _d(a.get("date_from")), _d(a.get("date_to"))
    gran = insights.default_trend_granularity(df, dt) if (df or dt) else "month"
    rows = insights.trend(db, gran, buyer_id=a.get("buyer_id"), supplier_id=a.get("supplier_id"), date_from=df, date_to=dt)
    if not rows:
        return Reply(tr(lang, "No inspection data for a trend yet.", "ট্রেন্ড দেখানোর মতো ডাটা নেই।"))
    first, last = rows[0], rows[-1]
    direction = "up" if (last["pass_rate"] or 0) >= (first["pass_rate"] or 0) else "down"
    text = tr(lang, f"Pass rate by {gran}: from {_fmt_pct(first['pass_rate'])} ({first['period']}) to {_fmt_pct(last['pass_rate'])} ({last['period']}), trending **{direction}**.",
              f"{gran} ভিত্তিক পাস রেট: {first['period']} এ {_fmt_pct(first['pass_rate'])} থেকে {last['period']} এ {_fmt_pct(last['pass_rate'])}, ট্রেন্ড **{'উপরে' if direction == 'up' else 'নিচে'}**।")
    chart = {"type": "chart", "kind": "line", "title": tr(lang, "Pass rate %", "পাস রেট %"), "max": 100,
             "series": [{"label": r["period"], "value": r["pass_rate"] or 0, "extra": r["rolls"]} for r in rows]}
    return Reply(text, [chart], [tr(lang, "Overall summary", "সারাংশ দাও"), tr(lang, "Top defects", "শীর্ষ ত্রুটি")])


def tool_list_schedules(db: Session, a: dict, lang: str) -> Reply:
    args = _clean(a, "schedule_status", "supplier_id", "buyer_id", "plm_id", "date_from", "date_to", "period_label", "supplier_name", "buyer_name")
    block, page = table_block("schedules", args, detail=a.get("detail", False), db=db)
    rows = run_dataset(db, "schedules", args, limit=5000)["rows"]
    scope = _scope_label(a, lang)
    text = tr(lang, f"**{page['total']} schedule(s)** for {scope}.", f"{scope} — **{page['total']}টি সিডিউল**।")
    if not page["total"]:
        text = tr(lang, f"No schedules for {scope}.", f"{scope} — কোনো সিডিউল নেই।")
    blocks = [stats([stat(tr(lang, "Pending", "পেন্ডিং"), sum(1 for r in rows if r["status"] == "Pending"), "warning"),
                     stat(tr(lang, "In progress", "চলছে"), sum(1 for r in rows if r["status"] == "In Progress"), "info"),
                     stat(tr(lang, "Completed", "সম্পন্ন"), sum(1 for r in rows if r["status"] == "Completed"), "success")]),
              block, _list_actions(lang, "schedules", args, page["total"], len(page["rows"]))]
    blocks[-1]["items"].append(act_nav(tr(lang, "Open Schedule page", "সিডিউল পেজ খুলুন"), "/schedule"))
    return Reply(text, blocks, [tr(lang, "Tomorrow's schedule", "আগামীকালের সিডিউল"), tr(lang, "Pending schedules", "পেন্ডিং সিডিউল")])


def tool_list_consignments(db: Session, a: dict, lang: str) -> Reply:
    args = _clean(a, "supplier_id", "buyer_id", "qc_status", "date_from", "date_to", "period_label", "supplier_name", "buyer_name")
    block, page = table_block("consignments", args, detail=a.get("detail", False), db=db)
    rows = run_dataset(db, "consignments", args, limit=5000)["rows"]
    drafts = sum(1 for r in rows if r["qc_setup"].startswith("Draft"))
    text = tr(lang, f"**{page['total']} consignment(s)** for {_scope_label(a, lang)}." + (f" {drafts} still in draft QC setup." if drafts else ""),
              f"{_scope_label(a, lang)} — **{page['total']}টি কনসাইনমেন্ট**।" + (f" {drafts}টির QC সেটআপ এখনো ড্রাফট।" if drafts else ""))
    blocks = [stats([stat(tr(lang, "Consignments", "কনসাইনমেন্ট"), page["total"], "info"),
                     stat(tr(lang, "Finalized", "ফাইনাল"), page["total"] - drafts, "success"),
                     stat(tr(lang, "Draft", "ড্রাফট"), drafts, "warning" if drafts else "neutral")]),
              block, _list_actions(lang, "consignments", args, page["total"], len(page["rows"]))]
    return Reply(text, blocks, [tr(lang, "Draft consignments", "ড্রাফট কনসাইনমেন্ট"), tr(lang, "PLM list", "PLM তালিকা")])


def tool_list_materials(db: Session, a: dict, lang: str) -> Reply:
    return _simple_list(db, a, lang, "materials", (), "name", False,
                        lambda p: tr(lang, f"There are **{p['total']} materials**. GSM, moisture and width standards come from here.",
                                     f"মোট **{p['total']}টি ম্যাটেরিয়াল**। GSM, ময়েশ্চার ও উইডথ স্ট্যান্ডার্ড এখান থেকেই আসে।"),
                        None, [tr(lang, "Defect types", "ত্রুটির ধরন")])


def tool_list_defect_types(db: Session, a: dict, lang: str) -> Reply:
    return _simple_list(db, a, lang, "defect_types", ("date_from", "date_to"), "points", True,
                        lambda p: tr(lang, f"There are **{p['total']} defect types** used for 4-point inspection.",
                                     f"৪-পয়েন্ট ইন্সপেকশনে মোট **{p['total']}টি ত্রুটির ধরন** আছে।"),
                        None, [tr(lang, "Top defects this month", "এই মাসের শীর্ষ ত্রুটি")])


def tool_knowledge(db: Session, a: dict, lang: str, message: str = "") -> Reply:
    hits = vector_store.search(db, message, ["knowledge"], k=3)
    if a.get("module"):
        from app.features.assistant.knowledge import KNOWLEDGE
        exact = [k for k in KNOWLEDGE if k.get("module") == a["module"]]
        if exact:
            hits = [vector_store.Hit("knowledge", "module", exact[0]["module"], exact[0]["title"], exact[0]["content"], {}, 1.0)] + \
                   [h for h in hits if h.title != exact[0]["title"]]
    if not hits:
        return Reply(tr(lang, "I don't have notes on that yet.", "এ বিষয়ে আমার কাছে তথ্য নেই।"))
    top = hits[0]
    blocks = [{"type": "knowledge", "title": top.title, "content": top.content,
               "related": [{"title": h.title, "content": h.content} for h in hits[1:3] if h.score > 0.25]}]
    module = top.ref_id
    sugg = [tr(lang, f"Print {MODULE_LABELS[module]} for a PLM", f"একটি PLM এর {MODULE_LABELS[module]} প্রিন্ট")] if module in MODULE_LABELS else []
    return Reply(tr(lang, f"Here's how **{top.title}** works:", f"**{top.title}** যেভাবে কাজ করে:"), blocks,
                 sugg + [tr(lang, "What else can you do?", "আর কী করতে পারো?")])


CAPABILITIES = [
    ("Summary & KPIs", "এই মাসের অবস্থা কেমন", "How is quality this month?"),
    ("Buyers / suppliers", "বায়ারের তালিকা দাও", "Show suppliers"),
    ("PLMs", "Square এর এই মাসের PLM দাও", "PLMs of Square this month"),
    ("PLM details", "MNS-489 এর বিস্তারিত", "MNS-489 details"),
    ("Print reports", "MNS-489 এর ৪ পয়েন্ট প্রিন্ট", "4 point report MNS-489"),
    ("Failed rolls", "এই মাসের ফেল রোল দাও", "Failed rolls this month"),
    ("Defects", "কোন ডিফেক্ট বেশি", "Top defects"),
    ("Supplier scores", "কোন সাপ্লায়ার ভালো", "Supplier scorecard"),
    ("Schedule", "আজকের সিডিউল", "Today's schedule"),
    ("QC rules", "৪ পয়েন্ট কী", "How is GSM calculated?"),
]


def tool_help(db: Session, a: dict, lang: str) -> Reply:
    return Reply(tr(lang, "I'm **FabriQ**, your fabric QC assistant. Ask in English, Bangla or Banglish. I can:",
                    "আমি **FabriQ**, আপনার ফ্যাব্রিক QC অ্যাসিস্ট্যান্ট। ইংরেজি, বাংলা বা বাংলিশে জিজ্ঞেস করুন। আমি যা পারি:"),
                 [{"type": "actions", "title": tr(lang, "Try these", "এগুলো চেষ্টা করুন"),
                   "items": [act_prompt(name, bn if lang == "bn" else en) for name, bn, en in CAPABILITIES]}])


def tool_greeting(db: Session, a: dict, lang: str) -> Reply:
    k = insights.kpis(db, date_from=factory_today().replace(day=1), date_to=factory_today())
    return Reply(tr(lang, f"Hi! I'm FabriQ. This month {k['rolls_inspected']} rolls are inspected with a {_fmt_pct(k['pass_rate'])} pass rate. What do you need?",
                    f"হ্যালো! আমি FabriQ। এই মাসে {k['rolls_inspected']}টি রোল ইন্সপেক্ট হয়েছে, পাস রেট {_fmt_pct(k['pass_rate'])}। কী জানতে চান?"),
                 [], [tr(lang, "Summary of this month", "এই মাসের সারাংশ"), tr(lang, "What can you do?", "তুমি কী কী পারো?")])


def tool_thanks(db: Session, a: dict, lang: str) -> Reply:
    return Reply(tr(lang, "Anytime! Ask me whenever you need data or a report.", "স্বাগতম! ডাটা বা রিপোর্ট লাগলে যেকোনো সময় জিজ্ঞেস করুন।"))


TOOLS = {
    "overview": tool_overview,
    "list_buyers": tool_list_buyers,
    "list_suppliers": tool_list_suppliers,
    "list_plms": tool_list_plms,
    "plm_detail": tool_plm_detail,
    "report": tool_report,
    "list_rolls": tool_list_rolls,
    "defect_pareto": tool_defect_pareto,
    "supplier_scorecard": tool_supplier_scorecard,
    "trend": tool_trend,
    "list_schedules": tool_list_schedules,
    "list_consignments": tool_list_consignments,
    "list_materials": tool_list_materials,
    "list_defect_types": tool_list_defect_types,
    "knowledge": tool_knowledge,
    "help": tool_help,
    "greeting": tool_greeting,
    "thanks": tool_thanks,
}

_FILTER_PROPS = {
    "supplier": {"type": "string", "description": "Supplier name"},
    "buyer": {"type": "string", "description": "Buyer name"},
    "plm": {"type": "string", "description": "PLM code, e.g. MNS-489"},
    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
}

TOOL_DESCRIPTIONS = {
    "overview": "Quality KPIs (rolls inspected, pass rate, failed, holds, schedules)",
    "list_buyers": "List buyers with PLM count and pass rate",
    "list_suppliers": "List suppliers with QC profile status, pass rate and grade",
    "list_plms": "List PLMs (production orders), optionally by supplier, buyer or date range",
    "plm_detail": "Details of one PLM: info, rolls, module report readiness and print buttons",
    "report": "Print / open the PDF of a module report for a PLM",
    "list_rolls": "List inspected 4-point rolls; filters: failed, disposition (hold/rejected/claim/accepted/pending)",
    "defect_pareto": "Top defects by points",
    "supplier_scorecard": "Supplier grades and scores",
    "trend": "Pass rate trend over time",
    "list_schedules": "Inspection schedules; filter by status pending/in_progress/completed",
    "list_consignments": "Fabric receives / consignments and QC setup status",
    "list_materials": "Materials with GSM / moisture / width standards",
    "list_defect_types": "Defect types and point ranges",
    "knowledge": "Explain a QC rule or how the system works",
    "help": "What the assistant can do",
}


def llm_tool_specs() -> list[dict]:
    specs = []
    for name, desc in TOOL_DESCRIPTIONS.items():
        props = dict(_FILTER_PROPS)
        if name == "report":
            props["module"] = {"type": "string", "enum": list(MODULE_LABELS)}
        if name == "list_rolls":
            props["is_pass"] = {"type": "boolean"}
            props["disposition"] = {"type": "string", "enum": ["pending", "accepted", "hold", "rejected", "claim"]}
        if name == "list_schedules":
            props["schedule_status"] = {"type": "string", "enum": ["pending", "in_progress", "completed"]}
        if name == "knowledge":
            props = {"topic": {"type": "string"}}
        specs.append({"name": name, "description": desc, "parameters": {"type": "object", "properties": props}})
    return specs


def dataset_titles() -> dict:
    return {k: v[0] for k, v in DATASETS.items()}
