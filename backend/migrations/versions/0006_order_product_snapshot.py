"""Keep order product identity stable after catalog edits."""
from alembic import op
import sqlalchemy as sa
revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('orders',sa.Column('product_name',sa.Text(),nullable=True))
    op.add_column('orders',sa.Column('product_image',sa.Text(),nullable=True))
    # Historical identity before this migration is not recoverable from the catalog.
    op.execute('UPDATE orders o SET product_name=p.name,product_image=p.image FROM products p WHERE p.id=o.product_id')

def downgrade():
    raise RuntimeError('Order identity snapshots must be retained; restore a backup for rollback')
