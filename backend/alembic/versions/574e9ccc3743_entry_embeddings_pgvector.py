"""entry_embeddings pgvector

Revision ID: 574e9ccc3743
Revises: 1de5e47dfa1d
Create Date: 2026-07-24 11:33:25.557039

The vector store, in Postgres (pgvector) since Chroma. Hand-written —
autogenerate can't emit the extension, vector(384) column, or HNSW index.
Postgres-only; no-ops on SQLite (outside SQLModel.metadata).
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '574e9ccc3743'
down_revision: Union[str, Sequence[str], None] = '1de5e47dfa1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (Postgres only)."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE entry_embeddings (
            entry_id  INTEGER PRIMARY KEY REFERENCES journalentry(id) ON DELETE CASCADE,
            user_id   INTEGER NOT NULL REFERENCES users(id),
            embedding vector(384) NOT NULL
        )
        """
    )
    # Isolation filter (search_similar always scopes WHERE user_id).
    op.execute("CREATE INDEX ix_entry_embeddings_user_id ON entry_embeddings (user_id)")
    # ANN index for cosine distance (<=>), covering `embedding` only — user_id
    # filtering is applied post-scan (needs pgvector >= 0.8; see
    # _ITERATIVE_SCAN in services/embeddings.py).
    op.execute(
        "CREATE INDEX ix_entry_embeddings_hnsw ON entry_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Downgrade schema (Postgres only)."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS entry_embeddings")
    # Leave the `vector` extension in place — other objects may depend on it.
