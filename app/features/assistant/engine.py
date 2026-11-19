"""FabriQ conversation engine.

Planning order: deterministic domain rules -> vector intent search -> follow-up on the
previous turn -> optional LLM planner (only for questions the rules did not understand).
Every answer is produced by a backend tool, so the numbers are identical whichever planner
picked the tool."""
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.features.assistant import llm, nlu, query, vector_store
from app.features.assistant.embedder import normalize_tokens
from app.features.assistant.datasets import DATASETS
from app.features.assistant.models import AssistantConversation, AssistantMessage
from app.features.assistant.tools import TOOLS, Reply, llm_tool_specs, tr

ALL_TOOLS = {**TOOLS, "query": query.tool_query, "identity": query.tool_identity}

_LIST = r"list|gula|guli|sob|shob|all|show|dekhao|dakhao|dao|daw|তালিকা|দাও|দেখাও|গুলো|সব"
_QUESTION = r"\bwhat\s+is\b|\bwhat's\b|\bhow\s+(is|does|do|to)\b|\bexplain\b|\bformula\b|\bmeaning\b|\bki\b|\bkivabe\b|\bkeno\b|কী|কি\b|কিভাবে|কেন|মানে"
_FOLLOW_UP = r"\b(these|those|them|this|ei|egula|oigula|oi|shudhu|sudhu|only|just|ekhon|now)\b|এগুলো|এই|ওই|শুধু"
_DATA_INTENTS = {"overview", "list_plms", "list_rolls", "defect_pareto", "supplier_scorecard", "trend", "list_schedules",
                 "list_consignments", "list_buyers", "list_suppliers"}
_INTENT_MIN_SCORE = 0.42
_IDENTITY = (r"your\s+name|who\s+are\s+you|what\s+are\s+you|introduce\s+yourself|who\s+made\s+you|tomar\s+naa?m|apnar\s+naa?m|tmr\s+naa?m|"
             r"\btumi\s+ke\b|\bapni\s+ke\b|তোমার নাম|আপনার নাম|তুমি কে|আপনি কে")
_GENERIC = {"what", "is", "the", "a", "an", "how", "does", "do", "to", "of", "in", "on", "for", "me", "my", "your", "you", "i",
            "show", "list", "give", "tell", "ki", "er", "ar", "count", "all", "please", "can", "are", "it", "this", "that"}
_CODE = re.compile(r"\b[a-z]{2,6}-\d{2,}(?:-\d+)?\b", re.IGNORECASE)

log = logging.getLogger(__name__)

_QUERY_SPEC = {
    "name": "query",
    "description": "Answer any count / total / average / highest / lowest question over a dataset",
    "parameters": {"type": "object", "properties": {
        "dataset": {"type": "string", "enum": list(query.SUBJECT_LABELS)},
        "op": {"type": "string", "enum": ["count", "sum", "avg", "max", "min", "best", "worst"]},
        "metric": {"type": "string", "description": "e.g. pass rate, failed, points, planned, gsm, moisture, quantity, score"},
        "supplier": {"type": "string"}, "buyer": {"type": "string"}, "plm": {"type": "string"},
        "date_from": {"type": "string"}, "date_to": {"type": "string"},
    }, "required": ["dataset", "op"]},
}


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def rule_intent(message: str, slots: nlu.Slots) -> Optional[str]:
    m = message.lower()
    ents = slots.entities
    asks = _has(_QUESTION, m)
    lists = _has(_LIST, m)
    if _has(r"\b(hi|hello|hey|salam|assalamu?alaikum)\b|হ্যালো|কেমন আছ|kemon acho", m) and len(m.split()) <= 4:
        return "greeting"
    if _has(r"^\s*(ok|okay|okk|accha|acha|thik ache|great|nice)\s*[.!]*\s*$|\b(thanks|thank you|thx|dhonnobad)\b|ধন্যবাদ", m):
        return "thanks"
    if _has(r"\bhelp\b|what can you do|ki korte paro|ki ki paro|সাহায্য|কী কী পারো|কি কি পারো", m):
        return "help"
    if _has(_IDENTITY, m):
        return "identity"
    data_q = query.detect(m)
    if asks and not lists and not data_q and not (ents.get("plm") or ents.get("supplier") or ents.get("buyer")) and (slots.module or _has(r"plm|calculat|formula|pass|fail|disposition|unit|access code|flow|scorecard|grade", m)):
        return "knowledge"
    if _has(r"print|pdf|প্রিন্ট|report|রিপোর্ট", m) and (slots.module or ents.get("plm")):
        return "report"
    if slots.module in ("correlation", "factory-report") and ents.get("plm"):
        return "report"
    if data_q:
        return "query"
    if _has(r"defect\s*types?|ত্রুটির ধরন|defect er type", m):
        return "list_defect_types"
    if _has(r"trend|ট্রেন্ড", m):
        return "trend"
    if _has(r"scorecard|best\s*supplier|worst\s*supplier|grade|rank|rating|kon\s*supplier|কোন সাপ্লায়ার|performance|স্কোর", m):
        return "supplier_scorecard"
    if _has(r"defect|ডিফেক্ট|ত্রুটি|pareto", m):
        return "defect_pareto"
    if _has(r"schedul|সিডিউল|শিডিউল", m):
        return "list_schedules"
    if _has(r"consignment|receive|কনসাইনমেন্ট|রিসিভ", m):
        return "list_consignments"
    if _has(r"\brolls?\b|রোল", m):
        return "list_rolls"
    if _has(r"\bplms?\b|পিএলএম|\borders?\b", m):
        if ents.get("plm") and not _has(r"\bplms\b|list|gula|তালিকা", m):
            return "plm_detail"
        return "list_plms"
    if ents.get("plm"):
        return "report" if slots.module else "plm_detail"
    if _has(r"material|ম্যাটেরিয়াল|fabric list", m):
        return "list_materials"
    if _has(r"supplier|mill|vendor|সাপ্লায়ার", m) and not ents.get("supplier"):
        return "list_suppliers"
    if _has(r"buyer|বায়ার", m) and not ents.get("buyer"):
        return "list_buyers"
    if _has(r"summary|kpi|overview|pass\s*rate|obostha|অবস্থা|সারাংশ|পাস রেট|how is quality|koto roll", m):
        return "overview"
    if ents.get("supplier") or ents.get("buyer"):
        return "overview"
    if slots.module and asks:
        return "knowledge"
    return None


def _content(text: str) -> set[str]:
    return {t for t in normalize_tokens(text) if t not in _GENERIC and len(t) > 1}


def vector_intent(db: Session, message: str) -> tuple[Optional[str], float]:
    """Nearest intent exemplar, accepted only when it shares a real content word with the message."""
    words = _content(message)
    best: dict[str, float] = defaultdict(float)
    for hit in vector_store.search(db, message, ["intent"], k=12):
        if words & _content(hit.title):
            best[hit.ref_id] = max(best[hit.ref_id], hit.score)
    if not best:
        return None, 0.0
    tool, score = max(best.items(), key=lambda kv: kv[1])
    return tool, score


_KNOWN_WORDS = (set(query.DOMAIN_WORDS) | query._ENGLISH_STOP | _GENERIC | nlu._BANGLISH | set(nlu.MONTHS) | {
    "only", "just", "now", "this", "last", "next", "previous", "past", "days", "day", "weeks", "months", "years", "din",
    "mash", "mas", "soptah", "bochor", "ekhon", "shudhu", "sudhu", "egula", "oigula", "these", "those", "them", "full",
    "more", "less", "again", "also", "and", "but", "with", "without", "show", "see", "view", "dekhi", "dekhte", "pura", "puro",
    "fail", "pass", "rate", "plm", "plms", "roll", "rolls", "top",
})


def _unknown_words(text: str, slots: nlu.Slots) -> set[str]:
    names = " ".join(e["name"].lower() for e in slots.entities.values())
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _KNOWN_WORDS and w not in names}


def _is_follow_up(message: str, slots: nlu.Slots) -> bool:
    m = message.lower()
    entities = {k for k in slots.entities if k not in ("material", "defect_type")}
    refines = any([slots.date_from, slots.is_pass is not None, slots.disposition, slots.schedule_status, slots.qc_status,
                   slots.detail, slots.limit, entities])
    if _unknown_words(m, slots) and not _has(_FOLLOW_UP, m):
        return False
    return len(m.split()) <= 6 and (refines or _has(_FOLLOW_UP, m))


def _resolve_llm_args(db: Session, args: dict, slots: nlu.Slots) -> dict:
    out = slots.filters()
    for ref_type in ("supplier", "buyer", "plm"):
        name = args.pop(ref_type, None)
        if name and f"{ref_type}_id" not in out:
            hits = vector_store.search(db, str(name), ["entity"], k=1, ref_type=ref_type)
            if hits and hits[0].score >= 0.45:
                out[f"{ref_type}_id"], out[f"{ref_type}_name"] = hits[0].ref_id, hits[0].title
    for key in ("date_from", "date_to"):
        value = str(args.get(key) or "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and key not in out:
            out[key] = value
    if isinstance(args.get("is_pass"), bool) and "is_pass" not in out:
        out["is_pass"] = args["is_pass"]
    if str(args.get("disposition") or "").lower() in _DISPOSITIONS and "disposition" not in out:
        out["disposition"] = str(args["disposition"]).lower()
    for key in ("module", "schedule_status"):
        if args.get(key) not in (None, "") and key not in out:
            out[key] = args[key]
    return out


_LLM_OPS = {"count", "list", "sum", "avg", "max", "min", "best", "worst"}
_DISPOSITIONS = {"pending", "accepted", "hold", "rejected", "claim"}


def _plan_with_llm(db: Session, message: str, history: list[dict], prev_tool, prev_args: dict, slots: nlu.Slots, text: str):
    if not llm.llm_enabled():
        return None
    hint = f"previous tool={prev_tool} args={prev_args}" if prev_tool else "new conversation"
    decision = llm.plan_with_llm(message, history, llm_tool_specs() + [_QUERY_SPEC], hint)
    if decision and decision.get("tool") in ALL_TOOLS:
        raw = dict(decision.get("args") or {})
        args = _resolve_llm_args(db, raw, slots)
        if decision["tool"] == "query":
            dataset = raw.get("dataset") if raw.get("dataset") in DATASETS else "plms"
            op = raw.get("op") if raw.get("op") in _LLM_OPS else "count"
            args.update({"dataset": dataset, "op": op, "question": f"{raw.get('metric', '')} {text}".lower()})
        return decision["tool"], args, slots, "llm"
    if decision and decision.get("reply"):
        return "_llm_reply", {"text": decision["reply"]}, slots, "llm"
    return None


def plan(db: Session, message: str, context: dict, history: list[dict]) -> tuple[str, dict, nlu.Slots, str]:
    """Returns (tool, args, slots, planner)."""
    slots = nlu.parse(db, message)
    prev_tool, prev_args = context.get("tool"), dict(context.get("args") or {})
    filters = slots.filters()
    text = slots.text or message

    code = _CODE.search(message)
    if code and not (slots.entities.get("plm") or slots.entities.get("consignment")):
        return "_not_found", {"code": code.group(0).upper()}, slots, "rules"

    tool = rule_intent(text, slots)
    planner = "rules"
    if not tool:
        vtool, score = vector_intent(db, text)
        if prev_tool and _is_follow_up(text, slots) and score < 0.75:
            args = {**prev_args, **filters}
            if slots.detail:
                args["detail"] = True
            return prev_tool, args, slots, "follow-up"
        if vtool and score >= _INTENT_MIN_SCORE:
            tool, planner = vtool, "vector"
    if not tool:
        # The built-in rules understood nothing: only now ask the AI to pick a tool. The rules are
        # exact for the questions they know, while a small local model guesses dates and filters.
        llm_plan = _plan_with_llm(db, message, history, prev_tool, prev_args, slots, text)
        if llm_plan:
            return llm_plan
        return "_fallback", {}, slots, "none"

    args = dict(filters)
    if tool == "query":
        args = query.build_args(text, query.detect(text.lower()), filters, slots.limit)
    if slots.detail:
        args["detail"] = True
    if prev_tool and tool in _DATA_INTENTS | {"query"} and _has(_FOLLOW_UP, message.lower()):
        for key in ("supplier_id", "supplier_name", "buyer_id", "buyer_name", "plm_id", "plm_name", "date_from", "date_to", "period_label"):
            if key in prev_args and key not in args:
                args[key] = prev_args[key]
    if prev_tool == tool and slots.detail and not filters:
        args = {**prev_args, "detail": True}
    return tool, args, slots, planner


def _fallback(lang: str, message: str, db: Session) -> Reply:
    found = query.tool_search(db, message, lang)
    if found:
        return found
    hits = vector_store.search(db, message, ["knowledge"], k=1)
    if hits and hits[0].score >= 0.5 and _content(message) & _content(f"{hits[0].title} {hits[0].content}"):
        return TOOLS["knowledge"](db, {}, lang, message=message)
    return Reply(tr(lang, "I didn't quite get that. You can ask things like:", "ঠিক বুঝতে পারিনি। এভাবে জিজ্ঞেস করতে পারেন:"),
                 [{"type": "actions", "items": [
                     {"kind": "prompt", "label": tr(lang, "Buyer list", "বায়ারের তালিকা"), "text": tr(lang, "show buyers", "বায়ারের তালিকা দাও"), "icon": "spark"},
                     {"kind": "prompt", "label": tr(lang, "This month's PLMs", "এই মাসের PLM"), "text": tr(lang, "PLMs this month", "এই মাসের PLM দাও"), "icon": "spark"},
                     {"kind": "prompt", "label": tr(lang, "Failed rolls", "ফেল রোল"), "text": tr(lang, "failed rolls this month", "এই মাসের ফেল রোল দাও"), "icon": "spark"},
                     {"kind": "prompt", "label": tr(lang, "What can you do?", "কী কী পারো?"), "text": "help", "icon": "spark"},
                 ]}])


def _not_found(db: Session, code: str, lang: str, message: str) -> Reply:
    hits = [h for h in vector_store.search(db, code, ["entity"], k=8) if h.ref_type in ("plm", "consignment")]
    partial = [h for h in hits if code.lower() in h.title.lower()]
    if len(partial) > 1:
        return Reply(tr(lang, f"Several records match **{code}**. Which one?", f"**{code}** এর সাথে একাধিক মিল আছে। কোনটি?"),
                     [{"type": "actions", "items": [{"kind": "prompt", "label": h.title, "icon": "spark",
                                                     "text": re.sub(re.escape(code), h.title, message, flags=re.IGNORECASE)}
                                                    for h in partial]}])
    items = [{"kind": "prompt", "label": h.title, "text": f"{h.title} details" if h.ref_type == "plm" else f"PLMs of {h.title}",
              "icon": "spark"} for h in hits]
    return Reply(tr(lang, f"I couldn't find **{code}**. Did you mean one of these?",
                    f"**{code}** খুঁজে পাইনি। এগুলোর কোনোটি কি?"),
                 [{"type": "actions", "title": tr(lang, "Closest matches", "কাছাকাছি মিল"), "items": items}] if items else [])


def run_tool(db: Session, tool: str, args: dict, lang: str, message: str) -> Reply:
    if tool == "_llm_reply":
        return Reply(args["text"])
    if tool == "_fallback":
        return _fallback(lang, message, db)
    if tool == "_not_found":
        return _not_found(db, args["code"], lang, message)
    fn = ALL_TOOLS[tool]
    if tool == "knowledge":
        return fn(db, args, lang, message=message)
    return fn(db, args, lang)


def _title_from(message: str) -> str:
    t = re.sub(r"\s+", " ", message).strip()
    return (t[:57] + "…") if len(t) > 58 else t or "New chat"


def serialize_message(m: AssistantMessage) -> dict:
    return {"id": str(m.id), "role": m.role, "content": m.content, "blocks": m.blocks or [],
            "suggestions": (m.meta or {}).get("suggestions", []), "meta": m.meta or {},
            "created_at": m.created_at.isoformat() + "Z"}


def serialize_conversation(c: AssistantConversation, with_messages: bool = False) -> dict:
    out = {"id": str(c.id), "title": c.title, "updated_at": c.updated_at.isoformat() + "Z", "created_at": c.created_at.isoformat() + "Z"}
    if with_messages:
        out["messages"] = [serialize_message(m) for m in c.messages]
    return out


def chat(db: Session, owner_id: str, message: str, conversation_id: Optional[str] = None) -> dict:
    vector_store.ensure_index(db)
    conv = None
    if conversation_id:
        conv = db.query(AssistantConversation).filter(AssistantConversation.id == conversation_id,
                                                      AssistantConversation.owner_id == owner_id).first()
    if conv is None:
        conv = AssistantConversation(owner_id=owner_id, title=_title_from(message), context={})
        db.add(conv)
        db.flush()
    history = [{"role": m.role, "content": m.content} for m in conv.messages[-6:]]
    context = dict(conv.context or {})
    user_msg = AssistantMessage(conversation_id=conv.id, role="user", content=message, blocks=[], meta={})
    db.add(user_msg)
    db.commit()

    tool, args, slots, planner = plan(db, message, context, history)
    try:
        reply = run_tool(db, tool, args, slots.lang, message)
    except Exception:  # noqa: BLE001 - a broken tool must not break the chat
        db.rollback()
        log.exception("FabriQ tool %s failed", tool)
        reply = Reply(tr(slots.lang, "Something went wrong while fetching that. Please try again.",
                         "ডাটা আনতে গিয়ে সমস্যা হয়েছে। আবার চেষ্টা করুন।"))

    bot_msg = AssistantMessage(conversation_id=conv.id, role="assistant", content=reply.text, blocks=reply.blocks,
                               meta={"tool": tool, "args": args, "planner": planner, "lang": slots.lang,
                                     "engine": llm.llm_label() if planner == "llm" else "Offline engine",
                                     "suggestions": reply.suggestions})
    bot_msg.created_at = datetime.utcnow()
    db.add(bot_msg)
    if not tool.startswith("_") and tool not in ("help", "greeting", "thanks"):
        conv.context = {"tool": tool, "args": {k: v for k, v in args.items() if k != "detail"}, "lang": slots.lang}
    conv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user_msg)
    db.refresh(bot_msg)
    return {"conversation": serialize_conversation(conv), "user_message": serialize_message(user_msg),
            "message": serialize_message(bot_msg)}
