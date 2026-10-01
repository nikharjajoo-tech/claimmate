"""wording reviews

Revision ID: c7a1e4b90d32
Revises: 093529326416
Create Date: 2026-10-01 10:12:44.019283

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1e4b90d32'
down_revision: Union[str, Sequence[str], None] = '093529326416'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'wording_reviews',
        sa.Column('claim_id', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('review_json', sa.Text(), nullable=True),
        sa.Column('wording_ref', sa.String(length=40), nullable=False),
        sa.Column('prompt_version', sa.String(length=10), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('pipeline_revision', sa.Integer(), nullable=True),
        sa.Column('runs', sa.Integer(), nullable=False),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('claim_id'),
    )
    op.create_index(op.f('ix_wording_reviews_status'), 'wording_reviews', ['status'], unique=False)
    op.create_index(op.f('ix_wording_reviews_updated_at'), 'wording_reviews', ['updated_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_wording_reviews_updated_at'), table_name='wording_reviews')
    op.drop_index(op.f('ix_wording_reviews_status'), table_name='wording_reviews')
    op.drop_table('wording_reviews')
