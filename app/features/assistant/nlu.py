"""Slot extraction for the offline engine: dates, statuses, modules, entities, language."""
import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.features.assistant import vector_store
from app.features.assistant.embedder import normalize_tokens

_BN_SCRIPT = re.compile(r"[\u0980-\u09ff]")
_BANGLISH = {
    "dao", "daw", "dekhao", "dakhao", "koto", "koyta", "gula", "guli", "er", "ache", "ase", "asa", "ki", "kon",
    "amake", "amka", "amar", "ei", "mash", "mas", "sob", "shob", "kivabe", "keno", "korte", "koro", "hoise",
    "hoyeche", "ajker", "aj", "gotokal", "kal", "jonno", "onujayi", "anujayie", "kemon", "acho", "valo", "beshi", "tumi",
    "thaka", "thake", "theke", "ar", "nai", "nei", "chai", "lagbe", "dekhte", "dekhi", "kar", "kader", "shudhu", "sudhu",
    "egula", "oigula", "kintu", "ekhon", "porer", "agamikal", "kemne", "bolo", "janao", "koren", "den", "din",
}

MONTHS = {
    "jan": 1, "january": 1, "জানুয়ারি": 1, "feb": 2, "february": 2, "ফেব্রুয়ারি": 2, "mar": 3, "march": 3, "মার্চ": 3,
    "apr": 4, "april": 4, "এপ্রিল": 4, "may": 5, "মে": 5, "jun": 6, "june": 6, "জুন": 6, "jul": 7, "july": 7, "জুলাই": 7,
    "aug": 8, "august": 8, "আগস্ট": 8, "sep": 9, "sept": 9, "september": 9, "সেপ্টেম্বর": 9, "oct": 10, "october": 10,
    "অক্টোবর": 10, "nov": 11, "november": 11, "নভেম্বর": 11, "dec": 12, "december": 12, "ডিসেম্বর": 12,
}

MODULE_WORDS = [
    ("factory-report", r"factory\s*report|ফ্যাক্টরি"),
    ("color-continuity", r"colou?r\s*continuity|continuity|কন্টিনিউটি"),
    ("fabric-inspection", r"4\s*-?\s*p(oi|o)?nt|four\s*point|fabric\s*inspection|৪\s*পয়েন্ট|ফোর পয়েন্ট"),
    ("gsm", r"\bgsm\b|জিএসএম"),
    ("moisture", r"moisture|ময়েশ্চার|আর্দ্রতা"),
    ("skew-bowing", r"skew|bowing|স্কিউ"),
    ("relaxation", r"relax"),
    ("shrinkage", r"shrink|pattern"),
    ("shade-check", r"shade"),
    ("correlation", r"correlat|mill\s*report"),
]


@dataclass
class Slots:
    lang: str = "en"
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    period_label: Optional[str] = None
    is_pass: Optional[bool] = None
    disposition: Optional[str] = None
    schedule_status: Optional[str] = None
    qc_status: Optional[str] = None
    module: Optional[str] = None
    detail: bool = False
    limit: Optional[int] = None
    group_by: Optional[str] = None
    entities: dict = field(default_factory=dict)  # ref_type -> {"id", "name"}
    text: str = ""  # message with domain typos corrected
    explicit_all_time: bool = False

    def filters(self) -> dict:
        out = {}
        for key in ("date_from", "date_to", "is_pass", "disposition", "schedule_status", "qc_status", "module", "limit", "group_by"):
            v = getattr(self, key)
            if v is not None:
                out[key] = v.isoformat() if isinstance(v, date) else v
        for ref_type, ent in self.entities.items():
            out[f"{ref_type}_id"] = ent["id"]
            out[f"{ref_type}_name"] = ent["name"]
        if self.period_label:
            out["period_label"] = self.period_label
        return out


def detect_lang(message: str) -> str:
    """Bangla replies only for Bangla script; Banglish and English get English."""
    return "bn" if _BN_SCRIPT.search(message) else "en"


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def parse_dates(msg: str, slots: Slots, today: Optional[date] = None) -> None:
    today = today or date.today()
    m = msg.lower()

    def setr(a, b, label):
        slots.date_from, slots.date_to, slots.period_label = a, b, label

    if re.search(r"all\s*time|sob\s*somoy|সব সময়|ever|overall time", m):
        slots.explicit_all_time = True
        return
    if re.search(r"\btoday\b|\baj\b|\baaj\b|ajker|ajke|আজ", m):
        return setr(today, today, "today")
    if re.search(r"yesterday|gotokal|গতকাল", m):
        y = today - timedelta(days=1)
        return setr(y, y, "yesterday")
    if re.search(r"tomorrow|agamikal|আগামীকাল|\bkal\b", m):
        t = today + timedelta(days=1)
        return setr(t, t, "tomorrow")
    if match := re.search(r"(?:last|past|gato|sesh|শেষ|গত)\s*(\d{1,3})\s*(day|din|দিন|week|soptah|সপ্তাহ|month|mash|mas|মাস)", m):
        n, unit = int(match.group(1)), match.group(2)
        days = n * 7 if unit in ("week", "soptah", "সপ্তাহ") else n * 30 if unit in ("month", "mash", "mas", "মাস") else n
        return setr(today - timedelta(days=days - 1), today, f"last {n} {unit}")
    if re.search(r"this\s*week|\b(ei|ai)\s*soptah|এই সপ্তাহ", m):
        start = today - timedelta(days=today.weekday())
        return setr(start, today, "this week")
    if re.search(r"last\s*week|gato\s*soptah|গত সপ্তাহ", m):
        start = today - timedelta(days=today.weekday() + 7)
        return setr(start, start + timedelta(days=6), "last week")
    if re.search(r"last\s*month|previous\s*month|got[oa]\s*mash|gotomash|ager\s*mash|গত মাস", m):
        first = today.replace(day=1) - timedelta(days=1)
        a, b = _month_range(first.year, first.month)
        return setr(a, b, first.strftime("%B %Y"))
    if re.search(r"this\s*month|\b(ei|ai|ey|e)\s*(mash|mas|masher|maser)\b|চলতি মাস|এই মাস|current month", m):
        a, b = _month_range(today.year, today.month)
        return setr(a, b, today.strftime("%B %Y"))
    if re.search(r"this\s*year|\b(ei|ai)\s*(bochor|bochorer)|এই বছর", m):
        return setr(date(today.year, 1, 1), today, str(today.year))
    if re.search(r"last\s*year|got[oa]\s*bochor|গত বছর", m):
        return setr(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), str(today.year - 1))
    if match := re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", m):
        try:
            d = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            return setr(d, d, d.strftime("%d %b %Y"))
        except ValueError:
            pass
    year_match = re.search(r"\b(20\d{2})\b", m)
    for word in re.findall(r"[a-z]+|[\u0980-\u09ff]+", m):
        if word in MONTHS:
            month = MONTHS[word]
            year = int(year_match.group(1)) if year_match else (today.year if month <= today.month else today.year - 1)
            a, b = _month_range(year, month)
            return setr(a, b, date(year, month, 1).strftime("%B %Y"))
    if year_match:
        y = int(year_match.group(1))
        return setr(date(y, 1, 1), date(y, 12, 31), str(y))


def parse_flags(msg: str, slots: Slots) -> None:
    m = msg.lower()
    tokens = set(normalize_tokens(msg))
    if "failed" in tokens or re.search(r"\bfail|ফেল|অকৃতকার্য", m):
        slots.is_pass = False
    elif "passed" in tokens and not re.search(r"pass\s*rate", m):
        slots.is_pass = True
    for disp in ("hold", "rejected", "claim", "accepted"):
        if disp in tokens or disp[:5] in m:
            slots.disposition = disp
    if re.search(r"pending\s*(decision|disposition)|decision\s*pending|sidhanto", m):
        slots.disposition = "pending"
    if re.search(r"in\s*progress|cholche|চলছে", m):
        slots.schedule_status = "in_progress"
    elif re.search(r"completed|done|shesh|সম্পন্ন", m):
        slots.schedule_status = "completed"
    elif "pending" in m and slots.disposition != "pending":
        slots.schedule_status = "pending"
    if "draft" in m:
        slots.qc_status = "draft"
    elif "final" in m:
        slots.qc_status = "finalized"
    for module, pattern in MODULE_WORDS:
        if re.search(pattern, m):
            slots.module = module
            break
    if "detail" in tokens or re.search(r"full|সম্পূর্ণ|all rows|puro|pura", m):
        slots.detail = True
    if match := re.search(r"(supplier|buyer|সাপ্লায়ার|বায়ার)\s*(onujayi|anujayi|anujayie|onujaye|wise|by|ভিত্তিক|অনুযায়ী)|by\s*(supplier|buyer)", m):
        word = match.group(1) or match.group(3)
        slots.group_by = "supplier" if word in ("supplier", "সাপ্লায়ার") else "buyer"
    if match := re.search(r"\btop\s*(\d{1,3})\b|\b(\d{1,3})\s*ta\b", m):
        slots.limit = int(match.group(1) or match.group(2))


# Codes (PLM, consignment) must match literally; names may also match fuzzily (typos, partial names).
_CODE_TYPES = ("plm", "consignment")
_FUZZY_MIN_SCORE = {"buyer": 0.72, "supplier": 0.72, "material": 0.75, "defect_type": 0.75}
_NOT_NAMES = {
    "plm", "plms", "roll", "rolls", "list", "buyer", "buyers", "supplier", "suppliers", "report", "print", "point", "defect",
    "defects", "material", "materials", "schedule", "details", "detail", "show", "this", "month", "today", "fail", "failed",
    "pass", "rate", "summary", "trend", "fabric", "fabrics", "textile", "textiles", "composite", "mills", "knit", "check",
    "type", "types", "consignment", "receive", "hold", "claim", "top", "best", "worst", "dao", "gula", "mash",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9\u0980-\u09ff&-]+", text.lower())


def resolve_entities(db: Session, msg: str, slots: Slots) -> None:
    """Literal code/name matches first; fuzzy vector similarity only for names, with a high bar."""
    low = msg.lower()
    words = [w for w in _words(low) if len(w) >= 3 and w not in _NOT_NAMES]
    for ref_type in ("plm", "consignment", "supplier", "buyer", "material", "defect_type"):
        if ref_type in slots.entities or not words:
            continue
        hits = vector_store.search(db, msg, ["entity"], k=8, ref_type=ref_type)
        exact = [h for h in hits if h.title and h.title.lower() in low]
        if not exact and ref_type in _CODE_TYPES:
            coded = [w for w in words if any(ch.isdigit() for ch in w)]
            exact = [h for h in hits if any(w in h.title.lower() for w in coded)]
            if len(exact) > 1:
                continue
        elif not exact:
            exact = [h for h in hits if any(tw.startswith(w) or w.startswith(tw) for w in words
                                            for tw in _words(h.title) if len(tw) >= 3 and tw not in _NOT_NAMES)]
        if exact:
            best = max(exact, key=lambda h: (h.title.lower() in low, h.score))
            slots.entities[ref_type] = {"id": best.ref_id, "name": best.title}
            continue
        if ref_type in _CODE_TYPES or not hits:
            continue
        if hits[0].score >= _FUZZY_MIN_SCORE[ref_type] and (len(hits) == 1 or hits[0].score - hits[1].score > 0.08):
            slots.entities[ref_type] = {"id": hits[0].ref_id, "name": hits[0].title}


def parse(db: Session, msg: str) -> Slots:
    from app.features.assistant.query import correct_typos

    fixed = correct_typos(msg)
    slots = Slots(lang=detect_lang(msg), text=fixed)
    parse_dates(fixed, slots)
    parse_flags(fixed, slots)
    resolve_entities(db, msg, slots)
    return slots
