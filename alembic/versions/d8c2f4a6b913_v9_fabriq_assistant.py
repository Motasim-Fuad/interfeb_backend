"""v9: FabriQ assistant - conversations, messages and the vector document store

Revision ID: d8c2f4a6b913
Revises: c3a7d5e1f842
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d8c2f4a6b913"
down_revision = "c3a7d5e1f842"
branch_labels = None
depends_on = None


def _pgvector_available(bind) -> bool:
    return bool(bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar())


def upgrade():
    op.create_table(
        "assistant_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_assistant_conversations_id", "assistant_conversations", ["id"])
    op.create_index("ix_assistant_conversations_owner", "assistant_conversations", ["owner_id", "updated_at"])

    op.create_table(
        "assistant_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("blocks", sa.JSON(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_assistant_messages_id", "assistant_messages", ["id"])
    op.create_index("ix_assistant_messages_conversation", "assistant_messages", ["conversation_id", "created_at"])

    bind = op.get_bind()
    use_vector = _pgvector_available(bind)
    if use_vector:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Dimension-less column: the embedder (offline hash or an API model) decides the size,
    # and every query filters on `embedder` so vectors of different models never mix.
    embedding_type = "vector" if use_vector else "double precision[]"
    op.execute(f"""
        CREATE TABLE assistant_documents (
            id uuid PRIMARY KEY,
            kind varchar NOT NULL,
            ref_type varchar,
            ref_id varchar,
            title varchar NOT NULL,
            content text NOT NULL,
            meta json NOT NULL DEFAULT '{{}}',
            embedder varchar NOT NULL,
            embedding {embedding_type} NOT NULL,
            created_at timestamp NOT NULL DEFAULT now()
        )
    """)
    op.create_index("ix_assistant_documents_kind", "assistant_documents", ["embedder", "kind"])


def downgrade():
    op.drop_table("assistant_documents")
    op.drop_table("assistant_messages")
    op.drop_table("assistant_conversations")
