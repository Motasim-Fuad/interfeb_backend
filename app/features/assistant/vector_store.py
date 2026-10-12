"""pgvector-backed document store with automatic (re)indexing.

Static corpus (intents + knowledge) is re-embedded when its content hash changes; the
entity corpus (buyers, suppliers, PLMs, ...) when the source tables' fingerprint changes."""
import hashlib
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import bindparam, func, text
from sqlalchemy.orm import Session

from app.features.admin_config.models import Buyer, DefectType, Material, Supplier
from app.features.assistant.embedder import get_embedder
from app.features.assistant.knowledge import INTENT_EXAMPLES, KNOWLEDGE
from app.features.plm.models import Plm
from app.features.store.models import FabricReceive

_lock = threading.Lock()
_checked_at = 0.0
_pgvector: Optional[bool] = None
_CHECK_EVERY_SECONDS = 20


@dataclass
class Hit:
    kind: str
    ref_type: Optional[str]
    ref_id: Optional[str]
    title: str
    content: str
    meta: dict
    score: float


def uses_pgvector(db: Session) -> bool:
    global _pgvector
    if _pgvector is None:
        _pgvector = bool(db.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'assistant_documents' "
            "AND column_name = 'embedding' AND udt_name = 'vector'"
        )).scalar())
    return _pgvector


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _replace(db: Session, kind: str, docs: list[dict], marker: str) -> None:
    embedder = get_embedder()
    db.execute(text("DELETE FROM assistant_documents WHERE embedder = :e AND kind IN (:k, :m)"),
               {"e": embedder.name, "k": kind, "m": f"{kind}:marker"})
    docs = docs + [{"title": marker, "content": marker, "kind": f"{kind}:marker"}]
    vectors = embedder.embed([d.get("embed_text") or f"{d['title']} {d['content']}" for d in docs])
    pg = uses_pgvector(db)
    cast = "CAST(:v AS vector)" if pg else "CAST(:v AS double precision[])"
    stmt = text(
        "INSERT INTO assistant_documents (id, kind, ref_type, ref_id, title, content, meta, embedder, embedding) "
        f"VALUES (:id, :kind, :rt, :rid, :title, :content, CAST(:meta AS json), :e, {cast})"
    )
    for d, v in zip(docs, vectors):
        db.execute(stmt, {
            "id": str(uuid.uuid4()),
            "kind": d.get("kind", kind),
            "rt": d.get("ref_type"),
            "rid": d.get("ref_id"),
            "title": d["title"][:500],
            "content": d["content"],
            "meta": json.dumps(d.get("meta") or {}),
            "e": embedder.name,
            "v": _vec_literal(v) if pg else "{" + ",".join(f"{x:.6f}" for x in v) + "}",
        })


def _marker(db: Session, kind: str) -> Optional[str]:
    return db.execute(text("SELECT title FROM assistant_documents WHERE embedder = :e AND kind = :k LIMIT 1"),
                      {"e": get_embedder().name, "k": f"{kind}:marker"}).scalar()


def _static_docs() -> list[dict]:
    docs = []
    for tool, examples in INTENT_EXAMPLES.items():
        for ex in examples:
            docs.append({"kind": "intent", "ref_type": "tool", "ref_id": tool, "title": ex, "content": ex, "embed_text": ex})
    for k in KNOWLEDGE:
        docs.append({"kind": "knowledge", "ref_type": "module", "ref_id": k.get("module"), "title": k["title"],
                     "content": k["content"], "embed_text": f"{k['title']} {k['title']} {k['content']}"})
    return docs


def _entity_fingerprint(db: Session) -> str:
    parts = []
    for model in (Buyer, Supplier, Material, DefectType, Plm, FabricReceive):
        n, latest = db.query(func.count(model.id), func.max(model.updated_at)).one()
        parts.append(f"{model.__tablename__}:{n}:{latest}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _entity_docs(db: Session) -> list[dict]:
    docs = []
    for b in db.query(Buyer).all():
        docs.append({"ref_type": "buyer", "ref_id": str(b.id), "title": b.name, "content": f"buyer {b.name}",
                     "embed_text": b.name, "meta": {"name": b.name}})
    for s in db.query(Supplier).all():
        docs.append({"ref_type": "supplier", "ref_id": str(s.id), "title": s.name, "content": f"supplier {s.name}",
                     "embed_text": s.name, "meta": {"name": s.name}})
    for m in db.query(Material).all():
        name = m.display_name or m.fabric_name
        docs.append({"ref_type": "material", "ref_id": str(m.id), "title": name, "content": f"material {name}",
                     "embed_text": f"{name} {m.fabric_code or ''}", "meta": {"name": name}})
    for d in db.query(DefectType).all():
        docs.append({"ref_type": "defect_type", "ref_id": str(d.id), "title": d.name, "content": f"defect {d.name}",
                     "embed_text": d.name, "meta": {"name": d.name}})
    for p in db.query(Plm).all():
        docs.append({"ref_type": "plm", "ref_id": str(p.id), "title": p.plm_code,
                     "content": f"plm {p.plm_code} {p.plm_style or ''} {p.consignment or ''}",
                     "embed_text": f"{p.plm_code} {p.plm_style or ''}", "meta": {"name": p.plm_code}})
    for r in db.query(FabricReceive).all():
        docs.append({"ref_type": "consignment", "ref_id": str(r.id), "title": r.consignment,
                     "content": f"consignment {r.consignment}", "embed_text": r.consignment, "meta": {"name": r.consignment}})
    return docs


def ensure_index(db: Session, force: bool = False) -> dict:
    """Cheap to call on every request: real work happens only when something changed."""
    global _checked_at
    now = time.time()
    if not force and now - _checked_at < _CHECK_EVERY_SECONDS:
        return {"rebuilt": []}
    with _lock:
        rebuilt = []
        static_hash = "static:" + hashlib.sha1(json.dumps([INTENT_EXAMPLES, KNOWLEDGE], sort_keys=True).encode()).hexdigest()
        if force or _marker(db, "static") != static_hash:
            docs = _static_docs()
            _replace(db, "static", [], static_hash)
            for kind in ("intent", "knowledge"):
                _replace(db, kind, [d for d in docs if d["kind"] == kind], f"{kind}:{static_hash}")
            rebuilt.append("static")
        entity_hash = "entity:" + _entity_fingerprint(db)
        if force or _marker(db, "entity") != entity_hash:
            _replace(db, "entity", [dict(d, kind="entity") for d in _entity_docs(db)], entity_hash)
            rebuilt.append("entity")
        db.commit()
        _checked_at = now
        return {"rebuilt": rebuilt}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def search(db: Session, query: str, kinds: Iterable[str], k: int = 8, ref_type: Optional[str] = None) -> list[Hit]:
    embedder = get_embedder()
    vec = embedder.embed([query])[0]
    kinds = list(kinds)
    params = {"e": embedder.name, "kinds": kinds, "k": k}
    where = "embedder = :e AND kind IN :kinds"
    if ref_type:
        where += " AND ref_type = :rt"
        params["rt"] = ref_type
    if uses_pgvector(db):
        params["v"] = _vec_literal(vec)
        rows = db.execute(text(
            f"SELECT kind, ref_type, ref_id, title, content, meta, 1 - (embedding <=> CAST(:v AS vector)) AS score "
            f"FROM assistant_documents WHERE {where} ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k"
        ).bindparams(bindparam("kinds", expanding=True)), params).fetchall()
        return [Hit(r[0], r[1], r[2], r[3], r[4], r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"), float(r[6]))
                for r in rows]
    rows = db.execute(text(
        f"SELECT kind, ref_type, ref_id, title, content, meta, embedding FROM assistant_documents WHERE {where}"
    ).bindparams(bindparam("kinds", expanding=True)), params).fetchall()
    scored = [Hit(r[0], r[1], r[2], r[3], r[4], r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
                  _cosine(vec, list(r[6]))) for r in rows]
    return sorted(scored, key=lambda h: -h.score)[:k]


def document_count(db: Session) -> int:
    return int(db.execute(text("SELECT count(*) FROM assistant_documents WHERE embedder = :e AND kind NOT LIKE :suffix"),
                          {"e": get_embedder().name, "suffix": "%:marker"}).scalar() or 0)
