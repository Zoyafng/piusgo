"""Transactional catalog publishing shared by the admin create and edit routes."""
import hashlib
import json
import secrets
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from fastapi import HTTPException
from backend.database import db

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')

class VariantEdit(Input):
    id: str | None = Field(default=None,min_length=1,max_length=100)
    name: str = Field(min_length=1,max_length=100)
    price: StrictInt = Field(ge=0,le=100000000)
    stock: StrictInt = Field(ge=0,le=10000000)

class ProductFields(Input):
    product_type: Literal['promotion','lottery']
    name: str = Field(min_length=1,max_length=200)
    category: StrictInt
    image: str = Field(min_length=1,max_length=1000)
    badge: str = Field(default='',max_length=40)
    tags: list[str] = Field(max_length=10)
    active: Literal[0,1]
    coupon_eligible: Literal[0,1]
    price: StrictInt = Field(ge=0,le=100000000)
    stock: StrictInt = Field(ge=0,le=500000000)
    variants: list[VariantEdit] = Field(min_length=1,max_length=50)

class ProductEdit(ProductFields):
    version: str = Field(min_length=64,max_length=64)

class ProductCreate(ProductFields):
    request_key: str = Field(min_length=16,max_length=100)


def fail(message,status=400):raise HTTPException(status,message)

def product_version(row,variants):
    return hashlib.sha256(json.dumps([dict(row),[dict(v) for v in variants]],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def validate(body):
    categories=json.loads((Path(__file__).parent/'seed.json').read_text())['categories']
    if body.category not in [x['id'] for x in categories]:fail('请选择有效商品分类')
    if not body.name.strip() or any(not v.name.strip() for v in body.variants):fail('商品和规格名称不能为空')
    if not (body.image.startswith('/assets/') or body.image.startswith('/api/product-images/') or body.image.startswith('https://')):fail('请上传商品图片或使用 HTTPS 图片地址')
    if any(len(t)>40 or not t.strip() for t in body.tags):fail('商品标签应为 1–40 字')
    if len(set(t.strip() for t in body.tags))!=len(body.tags):fail('商品标签不能重复')
    if len(set(v.name.strip() for v in body.variants))!=len(body.variants):fail('规格名称不能重复')
    ids=[v.id for v in body.variants if v.id]
    if len(set(ids))!=len(ids):fail('不能重复提交同一规格')
    if body.price!=min(v.price for v in body.variants):fail('商品到手价须等于最低规格单价')
    if body.stock!=sum(v.stock for v in body.variants):fail('商品总库存须等于所有规格库存之和')
    return dict(body.model_dump(exclude={'variants','version','request_key'}),name=body.name.strip(),tags=json.dumps([t.strip() for t in body.tags],ensure_ascii=False))

def save_product(body,user,audit,oid=None):
    values=validate(body)
    with db(True) as c:
        if oid is None:
            if any(v.id for v in body.variants):fail('新增商品的规格编号由系统生成')
            c.lock('catalog-create')
            fingerprint=hashlib.sha256(body.model_dump_json(exclude={'request_key'}).encode()).hexdigest()
            existing=c.execute('SELECT id,creation_hash FROM products WHERE creation_key=:key',{'key':body.request_key}).fetchone()
            if existing:
                if existing['creation_hash']!=fingerprint:fail('重复请求标识对应的商品内容不同',409)
                return {'id':existing['id'],'message':'商品已保存，请勿重复提交'}
            oid=c.execute('SELECT COALESCE(max(id),0)+1 id FROM products').fetchone()['id']
            values.update(id=oid,creation_key=body.request_key,creation_hash=fingerprint)
            c.execute('INSERT INTO products(id,category,name,image,price,stock,badge,tags,active,product_type,coupon_eligible,creation_key,creation_hash) VALUES(:id,:category,:name,:image,:price,:stock,:badge,:tags,:active,:product_type,:coupon_eligible,:creation_key,:creation_hash)',values)
            for i,v in enumerate(body.variants):
                vid=f'{oid}-standard' if i==0 else f'{oid}-{secrets.token_hex(8)}'
                c.execute('INSERT INTO variants(id,product_id,name,price,stock) VALUES(:id,:pid,:name,:price,:stock)',dict(v.model_dump(),id=vid,pid=oid,name=v.name.strip()))
            event='product_created'
        else:
            c.lock('catalog-product:'+str(oid))
            current=c.execute('SELECT * FROM products WHERE id=:id FOR UPDATE',{'id':oid}).fetchone()
            if not current:fail('商品不存在',404)
            variants=list(c.execute('SELECT id,name,price,stock FROM variants WHERE product_id=:id AND active=1 ORDER BY id FOR UPDATE',{'id':oid}))
            if product_version(current,variants)!=body.version:fail('商品或库存已变化，请重新加载后编辑',409)
            existing_ids={v['id'] for v in variants};incoming_ids={v.id for v in body.variants if v.id}
            if not incoming_ids.issubset(existing_ids):fail('规格不属于此商品或已移除')
            if current['product_type']!=body.product_type and c.execute("SELECT id FROM orders WHERE product_id=:id AND status='pending' LIMIT 1",{'id':oid}).fetchone():fail('此商品仍有待付款订单，请处理后再修改类型',409)
            for vid in existing_ids-incoming_ids:
                if c.execute("SELECT id FROM orders WHERE variant_id=:id AND status='pending' LIMIT 1",{'id':vid}).fetchone():fail('待付款订单正在使用被移除的规格，请先处理订单',409)
                c.execute('UPDATE variants SET active=0,stock=0 WHERE id=:id',{'id':vid})
            for v in body.variants:
                if v.id:
                    c.execute('UPDATE variants SET name=:name,price=:price,stock=:stock WHERE id=:id',dict(v.model_dump(),name=v.name.strip()))
                else:
                    c.execute('INSERT INTO variants(id,product_id,name,price,stock) VALUES(:id,:pid,:name,:price,:stock)',dict(v.model_dump(),id=f'{oid}-{secrets.token_hex(8)}',pid=oid,name=v.name.strip()))
            c.execute('UPDATE products SET name=:name,category=:category,image=:image,badge=:badge,tags=:tags,active=:active,price=:price,stock=:stock,product_type=:product_type,coupon_eligible=:coupon_eligible WHERE id=:id',dict(values,id=oid))
            event='product_updated'
        audit(c,user,event,oid)
    return {'id':oid,'message':'商品已上架，前台商品列表已同步' if body.active else '商品已保存为下架状态'}
