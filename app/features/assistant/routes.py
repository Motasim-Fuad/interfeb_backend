from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, require_admin
from app.core.rate_limit import rate_limiter
from app.features.assistant import engine, llm, vector_store
from app.features.assistant.datasets import DATASETS, MAX_ROWS, run_dataset
from app.features.assistant.embedder import get_embedder
from app.features.assistant.models import AssistantConversation
from app.features.assistant.pdf import dataset_pdf

router = APIRouter(prefix="/assistant", tags=["FabriQ Assistant"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[UUID] = None


class DatasetIn(BaseModel):
    name: str = Field(max_length=64)
    args: dict = Field(default_factory=dict, max_length=30)
    q: str = Field(default="", max_length=200)
    filters: dict = Field(default_factory=dict, max_length=30)
    sort: Optional[str] = Field(default=None, max_length=64)
    desc: bool = False
    limit: int = Field(default=50, ge=1, le=MAX_ROWS)
    offset: int = Field(default=0, ge=0)


def _scope_text(args: dict) -> str:
    parts = [args[k] for k in ("supplier_name", "buyer_name", "plm_name", "material_name", "period_label") if args.get(k)]
    if args.get("is_pass") is False:
        parts.append("Failed only")
    if args.get("disposition"):
        parts.append(f"Decision: {str(args['disposition']).title()}")
    if args.get("schedule_status"):
        parts.append(str(args["schedule_status"]).replace("_", " ").title())
    return " • ".join(parts)


@router.get("/status")
def status(db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    vector_store.ensure_index(db)
    return {
        "name": settings.ASSISTANT_NAME,
        "engine": llm.llm_label(),
        "llm_enabled": llm.llm_enabled(),
        "embedder": get_embedder().name,
        "vector_store": "pgvector" if vector_store.uses_pgvector(db) else "postgres-array",
        "documents": vector_store.document_count(db),
    }


@router.post("/reindex", dependencies=[Depends(rate_limiter("assistant_reindex", 3, 300))])
def reindex(db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    result = vector_store.ensure_index(db, force=True)
    return {**result, "documents": vector_store.document_count(db)}


@router.post("/chat")
def chat(payload: ChatIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    return engine.chat(db, user.sub, payload.message.strip(), str(payload.conversation_id) if payload.conversation_id else None)


@router.get("/conversations")
def conversations(db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    rows = (db.query(AssistantConversation).filter(AssistantConversation.owner_id == user.sub)
            .order_by(AssistantConversation.updated_at.desc()).limit(50).all())
    return [engine.serialize_conversation(c) for c in rows]


def _own(db: Session, conversation_id: UUID, user: CurrentUser) -> AssistantConversation:
    conv = db.query(AssistantConversation).filter(AssistantConversation.id == conversation_id,
                                                  AssistantConversation.owner_id == user.sub).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    return engine.serialize_conversation(_own(db, conversation_id, user), with_messages=True)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    db.delete(_own(db, conversation_id, user))
    db.commit()
    return Response(status_code=204)


@router.post("/dataset")
def dataset(payload: DatasetIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    if payload.name not in DATASETS:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    page = run_dataset(db, payload.name, payload.args, q=payload.q, filters=payload.filters, sort=payload.sort,
                       desc=payload.desc, limit=payload.limit, offset=payload.offset)
    return {**page, "scope": _scope_text(payload.args), "offset": payload.offset, "limit": payload.limit}


@router.post("/dataset/pdf")
def dataset_print(payload: DatasetIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    if payload.name not in DATASETS:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    page = run_dataset(db, payload.name, payload.args, q=payload.q, filters=payload.filters, sort=payload.sort,
                       desc=payload.desc, limit=MAX_ROWS, offset=0)
    pdf = dataset_pdf(page, _scope_text(payload.args), payload.q, payload.filters)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="fabriq-{payload.name}.pdf"'})
