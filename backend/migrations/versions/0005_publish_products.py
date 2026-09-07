"""Publish typed products with coupon eligibility and editable specifications."""
from alembic import op
import sqlalchemy as sa
revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('orders',sa.Column('source',sa.String(20),nullable=False,server_default='purchase'))
    op.create_check_constraint('ck_orders_source','orders',"source IN ('purchase','lottery')")
    op.add_column('lottery',sa.Column('product_id',sa.Integer(),sa.ForeignKey('products.id')))
    op.add_column('lottery',sa.Column('variant_id',sa.String(100),sa.ForeignKey('variants.id')))
    op.add_column('lottery',sa.Column('order_id',sa.String(50),sa.ForeignKey('orders.id')))
    op.create_unique_constraint('uq_lottery_order_id','lottery',['order_id'])
    op.add_column('products',sa.Column('product_type',sa.String(20),nullable=False,server_default='promotion'))
    op.add_column('products',sa.Column('coupon_eligible',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('products',sa.Column('creation_key',sa.String(100)))
    op.add_column('products',sa.Column('creation_hash',sa.String(64)))
    op.create_unique_constraint('uq_products_creation_key','products',['creation_key'])
    op.create_check_constraint('ck_products_type','products',"product_type IN ('promotion','lottery')")
    op.create_check_constraint('ck_products_coupon','products','coupon_eligible IN (0,1)')
    op.add_column('variants',sa.Column('active',sa.Integer(),nullable=False,server_default='1'))
    op.create_check_constraint('ck_variants_active','variants','active IN (0,1)')

def downgrade():
    connection=op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM products WHERE creation_key IS NOT NULL OR product_type='lottery'")):
        raise RuntimeError('New catalog products exist; restore a backup instead of losing their configuration')
    op.drop_constraint('ck_orders_source','orders',type_='check')
    op.drop_column('orders','source')
    op.drop_constraint('uq_lottery_order_id','lottery',type_='unique')
    for name in ['order_id','variant_id','product_id']:op.drop_column('lottery',name)
    op.drop_constraint('ck_variants_active','variants',type_='check')
    op.drop_column('variants','active')
    op.drop_constraint('ck_products_coupon','products',type_='check')
    op.drop_constraint('ck_products_type','products',type_='check')
    op.drop_constraint('uq_products_creation_key','products',type_='unique')
    for name in ['creation_hash','creation_key','coupon_eligible','product_type']:op.drop_column('products',name)
