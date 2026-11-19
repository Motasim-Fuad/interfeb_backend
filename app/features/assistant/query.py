"""Generic question answering over every FabriQ dataset.

Instead of one hand-written tool per question, a question is reduced to
subject (dataset) + operation (list / count / max / min / sum / avg) + metric (column)
+ filters (entities, dates, statuses), then answered from the same backend datasets
that power the viewer, CSV and PDF."""
import difflib
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.features.assistant import vector_store
from app.features.assistant.datasets import run_dataset
from app.features.assistant.tools import (
    Reply, _list_actions, _scope_label, act_prompt, actions, stat, stats, tr,
)

# ---------------------------------------------------------------- typo tolerance

DOMAIN_WORDS = [
    "supplier", "suppliers", "buyer", "buyers", "customer", "customers", "vendor", "vendors", "roll", "rolls", "defect", "defects",
    "schedule", "schedules", "consignment", "consignments", "material", "materials", "report", "reports", "inspection",
    "inspected", "failed", "passed", "pending", "summary", "scorecard", "trend", "moisture", "shrinkage", "relaxation",
    "continuity", "correlation", "shade", "skew", "bowing", "factory", "fabric", "received", "rejected", "accepted",
    "claim", "hold", "details", "detail", "average", "highest", "lowest", "total", "count", "grade", "print", "today",
    "yesterday", "tomorrow", "month", "week", "year", "order", "orders", "quality", "points", "point", "inspector",
    "decision", "disposition", "planned", "quantity", "score", "status", "active", "inactive", "draft", "finalized",
    "completed", "progress", "maximum", "minimum", "number", "receive",
]
_DOMAIN_SET = set(DOMAIN_WORDS)
_ENGLISH_STOP = {
    "give", "what", "which", "with", "from", "this", "that", "have", "many", "much", "there", "their", "they", "your", "about",
    "please", "want", "need", "tell", "know", "does", "were", "when", "where", "show", "list", "some", "more", "most",
    "least", "best", "worst", "than", "them", "into", "over", "under", "only", "just", "also", "very", "good", "name",
    "who", "whose", "are", "the", "and", "for", "how", "all", "any", "can", "you", "has", "had", "was", "per", "top",
}


def correct_typos(message: str) -> str:
    """Snap misspelt domain words ("suppliyer", "shedule", "byer") to the real word."""
    def fix(match: re.Match) -> str:
        word = match.group(0)
        low = word.lower()
        if len(low) < 4 or low in _DOMAIN_SET or low in _ENGLISH_STOP:
            return word
        close = difflib.get_close_matches(low, DOMAIN_WORDS, n=1, cutoff=0.8)
        if not close or close[0].startswith(low) or low.startswith(close[0]):
            return word
        return close[0]
    return re.sub(r"[A-Za-z]+", fix, message)


# ---------------------------------------------------------------- question shape

SUBJECTS = [
    ("defect_types", r"defect\s*types?|ত্রুটির ধরন"),
    ("scorecard", r"scorecard|\bscores?\b|\bgrades?\b|\branks?\b|ranking|স্কোর"),
    ("defect_pareto", r"\bdefects?\b|ডিফেক্ট|ত্রুটি"),
    ("suppliers", r"\bsuppliers?\b|\bmills?\b|\bvendors?\b|সাপ্লায়ার"),
    ("buyers", r"\bbuyers?\b|\bcustomers?\b|\bclients?\b|\bbrands?\b|বায়ার"),
    ("plms", r"\bplms?\b|\borders?\b|পিএলএম"),
    ("rolls", r"\brolls?\b|রোল"),
    ("schedules", r"\bschedules?\b|সিডিউল|শিডিউল"),
    ("consignments", r"\bconsignments?\b|\breceives?\b|\breceived\b|\bshipments?\b|কনসাইনমেন্ট"),
    ("materials", r"\bmaterials?\b|ম্যাটেরিয়াল"),
]

SUBJECT_LABELS = {
    "suppliers": ("suppliers", "সাপ্লায়ার"), "buyers": ("buyers", "বায়ার"), "plms": ("PLMs", "PLM"),
    "rolls": ("inspected rolls", "ইন্সপেক্টেড রোল"), "schedules": ("schedules", "সিডিউল"),
    "consignments": ("consignments", "কনসাইনমেন্ট"), "materials": ("materials", "ম্যাটেরিয়াল"),
    "defect_types": ("defect types", "ত্রুটির ধরন"), "defect_pareto": ("defects", "ত্রুটি"),
    "scorecard": ("suppliers (scorecard)", "সাপ্লায়ার (স্কোরকার্ড)"),
}

METRICS = [
    ("pass_rate", r"pass\s*rate|pass\s*%|পাস রেট|passing"),
    ("pts_100", r"per\s*100|pts\s*/|density"),
    ("planned_rolls", r"planned"),
    ("score", r"\bscores?\b|স্কোর"),
    ("failed", r"\bfail(ed|ure|s)?\b|ফেল"),
    ("points", r"\bpoints?\b|পয়েন্ট|\bdefects?\b|ডিফেক্ট|ত্রুটি"),
    ("quantity", r"quantity|\bqty\b|yards?|meters?"),
    ("gsm_tolerance", r"tolerance"),
    ("gsm", r"\bgsm\b"),
    ("moisture", r"moisture"),
    ("width", r"\bwidth\b"),
    ("inspected", r"\binspected\b"),
    ("count", r"\btimes\b|frequen|occur"),
    ("rolls", r"\brolls?\b|রোল"),
    ("plms", r"\bplms?\b|\borders?\b"),
]
DEFAULT_METRIC = {
    "suppliers": "pass_rate", "buyers": "plms", "plms": "failed", "rolls": "points", "defect_pareto": "points",
    "defect_types": "points", "scorecard": "score", "materials": "gsm", "consignments": "quantity",
}
HIGHER_IS_BETTER = {"pass_rate", "score", "inspected", "communication"}

_BEST = r"\bbest\b|\bgood\b|\bvalo\b|\bbhalo\b|ভালো|সেরা"
_WORST = r"\bworst\b|\bbad\b|\bkharap\b|খারাপ"
OPS = [
    ("count", r"how\s*many|number\s*of|total\s*(number|count)|\bcount\b|\bkoto\b|kotogula|\bkoyta\b|\bkoiti\b|\bsonkha\b|কত|কয়টি|কয়টা|সংখ্যা"),
    ("avg", r"\baverage\b|\bavg\b|\bmean\b|\bgor\b|গড়"),
    ("sum", r"\btotal\b|\bsum\b|\bmot\b|মোট"),
    ("max", r"highest|\bmost\b|\bmax(imum)?\b|\btop\b|biggest|largest|sob(c|ch)e?ye\s*beshi|সবচেয়ে বেশি|\bbeshi\b|বেশি"),
    ("min", r"lowest|\bleast\b|\bmin(imum)?\b|fewest|smallest|sob(c|ch)e?ye\s*kom|সবচেয়ে কম|\bkom\b|কম"),
]
_WHICH = r"(?:\b(?:which|who|kon|kar|kader)\b|কোন|কার)\s+([\w\u0980-\u09ff]+)"


def _subject(text: str) -> Optional[str]:
    which = re.search(_WHICH, text)
    if which:
        for name, pattern in SUBJECTS:
            if re.match(pattern, which.group(1)):
                return name
    found = []
    for idx, (name, pattern) in enumerate(SUBJECTS):
        m = re.search(pattern, text)
        if m:
            found.append((m.start(), idx, name))
    return min(found)[2] if found else None


def _op(text: str) -> Optional[str]:
    if re.search(_BEST, text) or re.search(_WORST, text):
        return "best" if re.search(_BEST, text) else "worst"
    for name, pattern in OPS:
        if re.search(pattern, text):
            return name
    return None


METRIC_HOME = {"planned_rolls": "plms", "quantity": "consignments", "gsm": "materials", "moisture": "materials",
               "gsm_tolerance": "materials", "width": "materials"}
_FILTER_WORDS = r"\b(inactive|active|missing|configured|without\s+qc|no\s+qc|draft|finalized|grade\s*[abcd])\b"


def detect(text: str) -> Optional[dict]:
    """Returns {"dataset", "op"} when the message is a data question this engine can answer."""
    text = text.lower()
    op = _op(text)
    subject = _subject(text)
    if op:
        for key, home in METRIC_HOME.items():
            if re.search(dict(METRICS)[key], text) and subject in (None, "rolls", "plms", "suppliers", "buyers") and subject != home:
                subject = home
                break
    if not op and subject and re.search(_FILTER_WORDS, text):
        op = "list"
    if not op or not subject:
        return None
    if op in ("best", "worst") and subject in ("suppliers",) and not any(re.search(p, text) for k, p in METRICS if k != "rolls"):
        subject = "scorecard"
    return {"dataset": subject, "op": op}


def _metric(text: str, dataset: str, columns: list[dict]) -> Optional[str]:
    numeric = {c["key"] for c in columns if c["kind"] in ("number", "percent")}
    for key, pattern in METRICS:
        if key in numeric and re.search(pattern, text) and not (dataset == key or (dataset == "rolls" and key == "rolls")):
            return key
    return None


def _facet_filters(text: str, rows: list[dict], columns: list[dict], skip_pass: bool) -> dict:
    out = {}
    text = re.sub(r"\b(without|no)\s+qc", "missing qc", text)
    if match := re.search(r"grade\s*([abcd])\b", text):
        if any(c["key"] == "grade" for c in columns):
            out["grade"] = match.group(1).upper()
    for c in columns:
        if c["kind"] != "status" or c["key"] in out:
            continue
        values = {str(r.get(c["key"])) for r in rows if r.get(c["key"]) not in (None, "")}
        if c["key"] == "status":
            values |= {"Active", "Inactive"}
        elif c["key"] == "qc_profile":
            values |= {"Configured", "Missing"}
        for v in sorted(values, key=len, reverse=True):
            low = v.lower()
            if len(low) < 3 or (skip_pass and low in ("pass", "fail")):
                continue
            if re.search(rf"\b{re.escape(low)}\b", text):
                out[c["key"]] = v
                break
    return out


def build_args(text: str, plan: dict, slot_filters: dict, slots_limit: Optional[int]) -> dict:
    return {**slot_filters, "dataset": plan["dataset"], "op": plan["op"], "question": text.lower(),
            **({"limit": slots_limit} if slots_limit else {})}


def _row_name(r: dict) -> str:
    if r.get("roll_no") is not None:
        return f"{r.get('plm_code')} • roll {r.get('roll_no')}"
    return str(r.get("name") or r.get("plm_code") or r.get("consignment") or r.get("date") or "-")


def _fmt(v, kind: str) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        v = round(v, 2)
        v = int(v) if v == int(v) else v
    return f"{v}%" if kind == "percent" else str(v)


# ---------------------------------------------------------------- tool

def tool_query(db: Session, a: dict, lang: str) -> Reply:
    dataset, op, text = a["dataset"], a["op"], a.get("question", "")
    base = {k: v for k, v in a.items() if k not in ("dataset", "op", "question", "limit", "detail", "match")}
    if dataset != "rolls":
        base.pop("is_pass", None)
    if dataset == "consignments" and not base.get("qc_status") and re.search(r"\b(without|no|missing|pending)\s+qc|not\s+finali[sz]ed", text):
        base["qc_status"] = "draft"
    full = run_dataset(db, dataset, base, limit=5000)
    columns = full["columns"]
    metric = _metric(text, dataset, columns)
    match = a.get("match") or _facet_filters(text, full["rows"], columns, skip_pass=metric == "pass_rate" or "is_pass" in base)
    args = {**base, **({"match": match} if match else {})}
    rows = run_dataset(db, dataset, args, limit=5000)["rows"]
    # "Alpha Mills er pass rate koto": a named supplier/buyer narrows its own list to that one row.
    entity_key = {"suppliers": "supplier_id", "buyers": "buyer_id"}.get(dataset)
    if entity_key and base.get(entity_key):
        rows = [r for r in rows if r.get("id") == str(base[entity_key])]
        if len(rows) == 1 and metric and op not in ("max", "min", "best", "worst"):
            r = rows[0]
            kinds = {c["key"]: c["kind"] for c in columns}
            labels = {c["key"]: c["label"] for c in columns}
            scope = _scope_label(a, lang)
            text_out = tr(lang, f"**{r['name']}** — {labels[metric]} ({scope}): **{_fmt(r.get(metric), kinds[metric])}**"
                                f" ({r.get('rolls', 0)} rolls inspected).",
                          f"**{r['name']}** — {labels[metric]} ({scope}): **{_fmt(r.get(metric), kinds[metric])}**"
                          f" ({r.get('rolls', 0)}টি রোল ইন্সপেক্ট)।")
            return _with_table(db, dataset, args, rows, columns, text_out, lang, sort=None)
    total = len(rows)
    label_en, label_bn = SUBJECT_LABELS.get(dataset, (dataset, dataset))
    scope = _scope_label(a, lang)
    flags = []
    if base.get("is_pass") is False:
        flags.append(tr(lang, "failed", "ফেল"))
    elif base.get("is_pass") is True:
        flags.append(tr(lang, "passed", "পাস"))
    for key in ("disposition", "qc_status"):
        if base.get(key):
            flags.append(str(base[key]).replace("_", " "))
    filt = ", ".join(flags + [str(v) for v in match.values()])
    where_en = f" ({filt})" if filt else ""
    if not total:
        return Reply(tr(lang, f"No {label_en}{where_en} found for {scope}.", f"{scope} — কোনো {label_bn}{where_en} পাওয়া যায়নি।"),
                     [actions([act_prompt(tr(lang, "Show all", "সব দেখাও"), f"{label_en} list")])])

    if op in ("best", "worst"):
        metric = metric or DEFAULT_METRIC.get(dataset)
        higher_better = metric in HIGHER_IS_BETTER
        op = "max" if (op == "best") == higher_better else "min"

    col_kind = {c["key"]: c["kind"] for c in columns}
    col_label = {c["key"]: c["label"] for c in columns}

    if op in ("count", "list") or (op == "sum" and not metric):
        text_out = tr(lang, f"There are **{total} {label_en}**{where_en} for {scope}.",
                      f"{scope} — মোট **{total}টি {label_bn}**{where_en}।")
        return _with_table(db, dataset, args, rows, columns, text_out, lang, sort=None)

    metric = metric or DEFAULT_METRIC.get(dataset)
    if not metric or metric not in col_kind:
        return _with_table(db, dataset, args, rows, columns,
                           tr(lang, f"**{total} {label_en}**{where_en} for {scope}.", f"{scope} — **{total}টি {label_bn}**{where_en}।"), lang, sort=None)
    mlabel = col_label[metric]
    values = [r[metric] for r in rows if isinstance(r.get(metric), (int, float))]

    if op in ("sum", "avg"):
        if not values:
            return Reply(tr(lang, f"No {mlabel} values yet for {scope}.", f"{scope} — {mlabel} এর কোনো মান নেই।"))
        result = sum(values) if op == "sum" else sum(values) / len(values)
        what_en = "Total" if op == "sum" else "Average"
        what_bn = "মোট" if op == "sum" else "গড়"
        text_out = tr(lang, f"{what_en} **{mlabel}** across {len(values)} {label_en}{where_en} ({scope}): **{_fmt(result, col_kind[metric])}**.",
                      f"{scope} — {len(values)}টি {label_bn}{where_en} এর {what_bn} **{mlabel}**: **{_fmt(result, col_kind[metric])}**।")
        reply = _with_table(db, dataset, args, rows, columns, text_out, lang, sort=metric, desc=True)
        reply.blocks.insert(0, stats([stat(f"{what_en} {mlabel}", _fmt(result, col_kind[metric]), "info"),
                                      stat(tr(lang, label_en.capitalize(), label_bn), len(values)),
                                      stat("Max", _fmt(max(values), col_kind[metric]), "success"),
                                      stat("Min", _fmt(min(values), col_kind[metric]), "warning")]))
        return reply

    desc = op == "max"
    ranked = [r for r in rows if isinstance(r.get(metric), (int, float))]
    if not ranked:
        return Reply(tr(lang, f"No {mlabel} values yet for {scope}.", f"{scope} — {mlabel} এর কোনো মান নেই।"))
    ranked.sort(key=lambda r: r[metric], reverse=desc)
    top = ranked[0]
    word_en = "highest" if desc else "lowest"
    word_bn = "সবচেয়ে বেশি" if desc else "সবচেয়ে কম"
    ties = [r for r in ranked if r[metric] == top[metric]]
    names = ", ".join(f"**{_row_name(r)}**" for r in ties[:4])
    text_out = tr(lang, f"{names} {'have' if len(ties) > 1 else 'has'} the {word_en} {mlabel}: **{_fmt(top[metric], col_kind[metric])}** ({scope}{where_en}).",
                  f"{scope}{where_en} — {word_bn} {mlabel}: {names} (**{_fmt(top[metric], col_kind[metric])}**)।")
    if a.get("limit") and a["limit"] > 1:
        listed = "; ".join(f"{i + 1}. **{_row_name(r)}** ({_fmt(r[metric], col_kind[metric])})" for i, r in enumerate(ranked[:a["limit"]]))
        text_out = tr(lang, f"Top {a['limit']} by {mlabel} ({scope}{where_en}): {listed}.",
                      f"{scope}{where_en} — {mlabel} অনুযায়ী শীর্ষ {a['limit']}: {listed}।")
    n = a.get("limit") or 10
    chart = {"type": "chart", "kind": "bar", "title": f"{mlabel} — {word_en}",
             "max": 100 if col_kind[metric] == "percent" else None,
             "series": [{"label": _row_name(r), "value": r[metric], "tone": "success" if i == 0 and desc == (metric in HIGHER_IS_BETTER) else "danger" if i == 0 else "info"}
                        for i, r in enumerate(ranked[:n])]}
    reply = _with_table(db, dataset, args, ranked, columns, text_out, lang, sort=metric, desc=desc, limit=n)
    reply.blocks.insert(0, chart)
    return reply


def _with_table(db, dataset, args, rows, columns, text, lang, *, sort, desc=False, limit=8) -> Reply:
    preview = rows[:limit]
    block = {"type": "table", "title": SUBJECT_LABELS.get(dataset, (dataset,))[0].capitalize(), "columns": columns, "rows": preview,
             "total": len(rows), "dataset": {"name": dataset, "args": args, "sort": sort, "desc": desc}}
    return Reply(text, [block, _list_actions(lang, dataset, args, len(rows), len(preview))],
                 [tr(lang, "Only this month", "শুধু এই মাসের"), tr(lang, "Show details", "বিস্তারিত দাও")])


# ---------------------------------------------------------------- search + identity

_SEARCH_STOP = _ENGLISH_STOP | {"dao", "daw", "dekhao", "gula", "er", "ar", "ache", "ase", "ki", "kon", "amake", "amka", "details", "detail"}


def tool_search(db: Session, message: str, lang: str) -> Optional[Reply]:
    words = [w for w in re.findall(r"[a-z0-9\u0980-\u09ff&-]+", message.lower()) if len(w) >= 3 and w not in _SEARCH_STOP]
    if not words:
        return None
    hits = vector_store.search(db, message, ["entity"], k=12)
    found = [h for h in hits if any(tw.startswith(w) or w.startswith(tw) for w in words
                                    for tw in re.findall(r"[a-z0-9\u0980-\u09ff&-]+", h.title.lower()) if len(tw) >= 3)]
    if not found:
        return None
    type_label = {"plm": "PLM", "supplier": "Supplier", "buyer": "Buyer", "consignment": "Consignment", "material": "Material",
                  "defect_type": "Defect type"}
    prompts = {"plm": "{t} details", "supplier": "{t} summary", "buyer": "{t} summary", "consignment": "PLMs of {t}",
               "material": "material list", "defect_type": "top defects"}
    rows = [{"type": type_label.get(h.ref_type, h.ref_type), "name": h.title} for h in found]
    return Reply(tr(lang, f"I found **{len(found)}** matching record(s). Pick one to see its data:",
                    f"**{len(found)}টি** মিল পাওয়া গেছে। ডাটা দেখতে একটি বেছে নিন:"),
                 [{"type": "table", "title": tr(lang, "Matches", "মিল"), "total": len(rows), "dataset": None, "rows": rows,
                   "columns": [{"key": "type", "label": "Type", "kind": "status"}, {"key": "name", "label": "Name", "kind": "text"}]},
                  actions([act_prompt(h.title, prompts.get(h.ref_type, "{t}").format(t=h.title)) for h in found[:8]])])


def tool_identity(db: Session, a: dict, lang: str) -> Reply:
    from app.features.assistant.llm import llm_label
    from app.features.assistant.tools import tool_help
    help_reply = tool_help(db, a, lang)
    return Reply(tr(lang, f"I'm **FabriQ**, the Interfab fabric QC assistant (running on: {llm_label()}). I read live data from the QC backend — "
                          "PLMs, rolls, defects, suppliers, buyers, schedules, consignments — and open the same printable reports as the app.",
                    f"আমি **FabriQ**, Interfab এর ফ্যাব্রিক QC অ্যাসিস্ট্যান্ট (চলছে: {llm_label()})। QC ব্যাকএন্ড থেকে লাইভ ডাটা পড়ি — "
                    "PLM, রোল, ডিফেক্ট, সাপ্লায়ার, বায়ার, সিডিউল, কনসাইনমেন্ট — আর অ্যাপের মতোই প্রিন্টেবল রিপোর্ট খুলি।"),
                 help_reply.blocks)
