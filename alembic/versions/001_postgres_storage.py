"""PostgreSQL partitioned storage, FTS and compression.

Revision ID: 001_postgres_storage
Revises:
Create Date: 2026-11-22
"""

from alembic import op

revision = "001_postgres_storage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from fast_parser.pg_schema import ensure_postgres

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        ensure_postgres(bind.engine)


def downgrade() -> None:
    pass
