"""Idempotent explicit seed operation. Does not change existing stock or prices."""
import json
from pathlib import Path
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select, func
from backend.database import engine
from backend.schema import products, variants

def seed(connection):
    data=json.loads((Path(__file__).parent/'seed.json').read_text())
    for product in data['products']:
        row=dict(product,tags=json.dumps(product['tags'],ensure_ascii=False))
        connection.execute(insert(products).values(**row).on_conflict_do_nothing())
        stock=connection.scalar(select(products.c.stock).where(products.c.id==product['id']))
        connection.execute(insert(variants).values(id=str(product['id'])+'-standard',product_id=product['id'],name='菲区官方充值' if product['id']==188 else '标准规格',price=product['price'],stock=stock).on_conflict_do_nothing())
    result=connection.execute(insert(variants).values(id='188-apple',product_id=188,name='苹果官方充值',price=15800,stock=108).on_conflict_do_nothing())
    if result.rowcount:
        total=connection.scalar(select(func.sum(variants.c.stock)).where(variants.c.product_id==188))
        connection.execute(products.update().where(products.c.id==188).values(stock=total))

if __name__=='__main__':
    with engine.begin() as connection:seed(connection)
    print('Mock catalog seed applied')
