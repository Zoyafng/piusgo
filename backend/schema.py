"""SQLAlchemy Core schema; production changes are applied through Alembic."""
from sqlalchemy import (MetaData, Table, Column as C, String, Text, Integer, BigInteger,
    Float, ForeignKey as FK, ForeignKeyConstraint as FKC, UniqueConstraint as UQ,
    CheckConstraint as CK, Index)

metadata = MetaData(naming_convention={'ix':'ix_%(table_name)s_%(column_0_name)s','uq':'uq_%(table_name)s_%(column_0_name)s','fk':'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s','pk':'pk_%(table_name)s'})
users=Table('users',metadata,C('id',String(32),primary_key=True),C('email',String(191),nullable=False,unique=True),C('password',Text,nullable=False),C('balance',BigInteger,nullable=False,server_default='0'),C('created',Float,nullable=False),C('display_name',String(40),nullable=False,server_default=''),C('phone',String(24),nullable=False,server_default=''),C('last_login_at',Float),C('last_login_ip',String(64)),C('previous_login_at',Float),C('previous_login_ip',String(64)),CK('balance >= 0',name='ck_users_balance'))
sessions=Table('sessions',metadata,C('token',String(64),primary_key=True),C('user_id',String(32),FK('users.id',ondelete='CASCADE'),nullable=False),C('expires',Float,nullable=False))
challenges=Table('challenges',metadata,C('id',String(100),primary_key=True),C('purpose',String(20),nullable=False),C('email',String(191),nullable=False),C('answer',String(64),nullable=False),C('expires',Float,nullable=False),C('attempts',Integer,nullable=False,server_default='0'),CK('attempts >= 0',name='ck_challenge_attempts'))
rates=Table('rates',metadata,C('key',String(300),primary_key=True),C('hits',Integer,nullable=False),C('expires',Float,nullable=False))
products=Table('products',metadata,C('id',Integer,primary_key=True,autoincrement=False),C('category',Integer,nullable=False),C('name',Text,nullable=False),C('image',Text,nullable=False),C('price',BigInteger,nullable=False),C('stock',Integer,nullable=False),C('badge',Text,nullable=False),C('tags',Text,nullable=False),CK('price >= 0 AND stock >= 0',name='ck_product_values'))
variants=Table('variants',metadata,C('id',String(100),primary_key=True),C('product_id',Integer,FK('products.id'),nullable=False),C('name',Text,nullable=False),C('price',BigInteger,nullable=False),C('stock',Integer,nullable=False),UQ('id','product_id',name='uq_variant_product'),CK('price >= 0 AND stock >= 0',name='ck_variant_values'))
coupons=Table('coupons',metadata,C('id',String(64),primary_key=True),C('user_id',String(32),FK('users.id'),nullable=False),C('kind',String(100),nullable=False),C('amount',BigInteger,nullable=False),C('minimum',BigInteger,nullable=False),C('used',Integer,nullable=False,server_default='0'),UQ('user_id','kind'),UQ('id','user_id',name='uq_coupon_owner'),CK('amount >= 0 AND minimum >= 0 AND used IN (0,1)',name='ck_coupon_values'))
orders=Table('orders',metadata,C('id',String(50),primary_key=True),C('user_id',String(32),FK('users.id'),nullable=True),C('guest_key',String(64)),C('paid_at',Float),C('product_id',Integer,FK('products.id'),nullable=False),C('quantity',Integer,nullable=False),C('account',String(191),nullable=False),C('total',BigInteger,nullable=False),C('discount',BigInteger,nullable=False),C('coupon_id',String(64)),C('status',String(20),nullable=False),C('delivery',Text),C('created',Float,nullable=False),C('request_key',String(100),nullable=False),C('variant_id',String(100),nullable=False),C('variant_name',Text,nullable=False),C('payment_method',String(20),nullable=False),UQ('user_id','request_key'),UQ('guest_key','request_key',name='uq_guest_order_request'),UQ('id','guest_key',name='uq_order_guest_owner'),CK('(user_id IS NOT NULL) <> (guest_key IS NOT NULL)',name='ck_order_owner_kind'),UQ('id','user_id',name='uq_order_owner'),FKC(['coupon_id','user_id'],['coupons.id','coupons.user_id'],name='fk_order_coupon_owner'),FKC(['variant_id','product_id'],['variants.id','variants.product_id'],name='fk_order_variant_product'),CK('quantity BETWEEN 1 AND 10 AND total >= 0 AND discount >= 0',name='ck_order_values'),CK("status IN ('pending','paid','cancelled')",name='ck_order_status'),CK("payment_method IN ('mock','alipay','wechat','balance')",name='ck_order_payment'))
tickets=Table('tickets',metadata,C('id',String(50),primary_key=True),C('user_id',String(32),FK('users.id'),nullable=True),C('guest_key',String(64)),C('priority',String(10),nullable=False,server_default='medium'),C('order_id',String(50),FK('orders.id')),UQ('order_id',name='uq_ticket_order'),CK("priority IN ('low','medium','high')",name='ck_ticket_priority'),CK('(user_id IS NOT NULL) <> (guest_key IS NOT NULL)',name='ck_ticket_owner_kind'),C('title',String(100),nullable=False),C('body',Text,nullable=False),C('status',String(20),nullable=False),C('created',Float,nullable=False),FKC(['order_id','user_id'],['orders.id','orders.user_id'],name='fk_ticket_order_owner'))
ledger=Table('ledger',metadata,C('id',String(50),primary_key=True),C('user_id',String(32),FK('users.id'),nullable=False),C('amount',BigInteger,nullable=False),C('bonus',BigInteger,nullable=False),C('created',Float,nullable=False),C('request_key',String(100),nullable=False),C('payment_method',String(20),nullable=False,server_default='mock'),UQ('user_id','request_key'),CK('amount > 0 AND bonus >= 0',name='ck_ledger_values'))
lottery=Table('lottery',metadata,C('user_id',String(32),FK('users.id'),primary_key=True),C('campaign',String(30),primary_key=True),C('won',Integer,nullable=False),C('created',Float,nullable=False),CK('won IN (0,1)',name='ck_lottery_result'))
audit_events=Table('audit_events',metadata,C('id',BigInteger,primary_key=True),C('user_id',String(32),FK('users.id'),nullable=True),C('event',String(80),nullable=False),C('object_id',String(100),nullable=False),C('created',Float,nullable=False))
for table in (orders,tickets,ledger,audit_events):Index('ix_'+table.name+'_user_created',table.c.user_id,table.c.created)
Index('ix_sessions_user',sessions.c.user_id)
for table in (sessions,challenges,rates):Index('ix_'+table.name+'_expires',table.c.expires)

mail_outbox=Table('mail_outbox',metadata,C('id',String(40),primary_key=True),C('order_id',String(50),FK('orders.id'),nullable=False,unique=True),C('recipient',String(191),nullable=False),C('subject',Text,nullable=False),C('body',Text,nullable=False),C('status',String(20),nullable=False),C('attempts',Integer,nullable=False,server_default='0'),C('next_attempt',Float,nullable=False),C('locked_at',Float),C('created',Float,nullable=False),C('sent_at',Float),C('error',String(100)),CK("status IN ('queued','sending','sent','mock_delivered','failed')",name='ck_mail_status'))
Index('ix_mail_status_next',mail_outbox.c.status,mail_outbox.c.next_attempt)
Index('ix_guest_orders_created',orders.c.guest_key,orders.c.created)
Index('ix_guest_tickets_created',tickets.c.guest_key,tickets.c.created)

# Administrator management fields (migration 0004).
users.append_column(C('role',String(20),nullable=False,server_default='member'))
users.append_column(C('disabled',Integer,nullable=False,server_default='0'))
users.append_constraint(CK("role IN ('member','admin')",name='ck_users_role'))
users.append_constraint(CK('disabled IN (0,1)',name='ck_users_disabled'))
products.append_column(C('active',Integer,nullable=False,server_default='1'))
products.append_constraint(CK('active IN (0,1)',name='ck_products_active'))
tickets.append_column(C('reply',Text,nullable=False,server_default=''))
tickets.append_column(C('replied_at',Float))

site_pages=Table('site_pages',metadata,C('id',String(50),primary_key=True),C('title',String(100),nullable=False),C('intro',Text,nullable=False),C('sections',Text,nullable=False),C('created',Float,nullable=False))

# Catalog publishing (migration 0005).
products.append_column(C('product_type',String(20),nullable=False,server_default='promotion'))
products.append_column(C('coupon_eligible',Integer,nullable=False,server_default='1'))
products.append_column(C('creation_key',String(100)))
products.append_column(C('creation_hash',String(64)))
products.append_constraint(UQ('creation_key',name='uq_products_creation_key'))
products.append_constraint(CK("product_type IN ('promotion','lottery')",name='ck_products_type'))
products.append_constraint(CK('coupon_eligible IN (0,1)',name='ck_products_coupon'))
variants.append_column(C('active',Integer,nullable=False,server_default='1'))
variants.append_constraint(CK('active IN (0,1)',name='ck_variants_active'))

lottery.append_column(C('product_id',Integer,FK('products.id')))
lottery.append_column(C('variant_id',String(100),FK('variants.id')))
lottery.append_column(C('order_id',String(50),FK('orders.id')))
lottery.append_constraint(UQ('order_id',name='uq_lottery_order_id'))

orders.append_column(C('source',String(20),nullable=False,server_default='purchase'))
orders.append_constraint(CK("source IN ('purchase','lottery')",name='ck_orders_source'))
