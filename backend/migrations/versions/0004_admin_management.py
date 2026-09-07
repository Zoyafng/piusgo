"""Administrator access, account state, catalog visibility and support replies."""
import json
import time
from pathlib import Path
from alembic import op
import sqlalchemy as sa
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

def upgrade():
    table=op.create_table('site_pages',sa.Column('id',sa.String(50),primary_key=True),sa.Column('title',sa.String(100),nullable=False),sa.Column('intro',sa.Text(),nullable=False),sa.Column('sections',sa.Text(),nullable=False),sa.Column('created',sa.Float(),nullable=False))
    pages=json.loads((Path(__file__).resolve().parents[2]/'pages.json').read_text())
    op.bulk_insert(table,[dict(id=key,title=p['title'],intro=p['intro'],sections=json.dumps(p['sections'],ensure_ascii=False),created=time.time()) for key,p in pages.items()])
    op.add_column('users', sa.Column('role', sa.String(20), nullable=False, server_default='member'))
    op.add_column('users', sa.Column('disabled', sa.Integer(), nullable=False, server_default='0'))
    op.create_check_constraint('ck_users_role', 'users', "role IN ('member','admin')")
    op.create_check_constraint('ck_users_disabled', 'users', 'disabled IN (0,1)')
    op.add_column('products', sa.Column('active', sa.Integer(), nullable=False, server_default='1'))
    op.create_check_constraint('ck_products_active', 'products', 'active IN (0,1)')
    op.add_column('tickets', sa.Column('reply', sa.Text(), nullable=False, server_default=''))
    op.add_column('tickets', sa.Column('replied_at', sa.Float()))

def downgrade():
    op.drop_table('site_pages')
    op.drop_column('tickets', 'replied_at')
    op.drop_column('tickets', 'reply')
    op.drop_constraint('ck_products_active', 'products', type_='check')
    op.drop_column('products', 'active')
    op.drop_constraint('ck_users_disabled', 'users', type_='check')
    op.drop_constraint('ck_users_role', 'users', type_='check')
    op.drop_column('users', 'disabled')
    op.drop_column('users', 'role')
