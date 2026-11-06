"""Add derived, hash-versioned file text without changing stored originals.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('stored_files', sa.Column('body_status', sa.String(32), nullable=False, server_default=sa.text("'pending'")))
    op.add_column('stored_files', sa.Column('body_text', sa.Text(), nullable=True))
    op.add_column('stored_files', sa.Column('body_detail', sa.String(512), nullable=True))
    op.add_column('stored_files', sa.Column('body_sha256', sa.String(64), nullable=True))
    op.add_column('stored_files', sa.Column('body_index_version', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column('stored_files', sa.Column('body_indexed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_stored_files_body_sha256', 'stored_files', ['body_sha256'])


def downgrade():
    op.drop_index('ix_stored_files_body_sha256', table_name='stored_files')
    for name in ('body_indexed_at','body_index_version','body_sha256','body_detail','body_text','body_status'):
        op.drop_column('stored_files', name)
