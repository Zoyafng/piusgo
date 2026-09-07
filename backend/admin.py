"""Admin-only business views. Identifiers/columns come exclusively from allowlists."""
import re
import json
import time
from pathlib import Path
from typing import Literal
from fastapi.responses import FileResponse
from backend.product_images import ImageUpload, store_image, DIRECTORY
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from backend.database import db
from backend.order_service import expire_orders
from backend.catalog_admin import ProductCreate, ProductEdit, save_product, product_version

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')

class UserEdit(Input):
    display_name: str = Field(max_length=40)
    phone: str = Field(max_length=24)
    disabled: Literal[0,1]

class PageSection(Input):
    title: str = Field(min_length=1,max_length=100)
    text: str = Field(min_length=1,max_length=5000)

class PageEdit(Input):
    title: str = Field(min_length=1,max_length=100)
    intro: str = Field(max_length=1000)
    sections: list[PageSection] = Field(min_length=1,max_length=30)

class TicketEdit(Input):
    status: Literal['open','waiting_user','resolved','closed']
    priority: Literal['low','medium','high']
    reply: str = Field(max_length=5000)

class Action(Input):
    action: Literal['cancel','retry','revoke']

# table, selected columns, searchable columns, filter column, stable ordering
VIEWS = {
    'pages': ('site_pages', 'id,title,intro,sections,created', ['id','title','intro'], 'id', 'id'),
    'users': ('users', 'id,email,display_name,phone,role,disabled,balance,created,last_login_at,last_login_ip,previous_login_at,previous_login_ip', ['id','email','display_name','phone'], 'disabled', 'created DESC,id DESC'),
    'products': ('products', 'id,category,name,image,price,stock,badge,tags,active,product_type,coupon_eligible', ['name','CAST(id AS TEXT)'], 'active', 'id DESC'),
    'orders': ('orders', 'id,source,user_id,product_id,product_name,product_image,quantity,account,total,discount,status,created,paid_at,variant_name,payment_method,delivery', ['id','account','user_id','variant_name'], 'status', 'created DESC,id DESC'),
    'tickets': ('tickets', 'id,user_id,order_id,title,body,status,priority,created,reply,replied_at', ['id','title','order_id','user_id'], 'status', 'created DESC,id DESC'),
    'coupons': ('coupons', 'id,user_id,kind,amount,minimum,used', ['id','user_id','kind'], 'used', 'id DESC'),
    'ledger': ('ledger', 'id,user_id,amount,bonus,payment_method,created', ['id','user_id'], 'payment_method', 'created DESC,id DESC'),
    'lottery': ('lottery', 'user_id,campaign,product_id,variant_id,order_id,won,created', ['user_id','campaign','order_id'], 'won', 'created DESC,user_id DESC,campaign DESC'),
    'mail': ('mail_outbox', 'id,order_id,recipient,status,attempts,created,sent_at,error', ['id','order_id','recipient'], 'status', 'created DESC,id DESC'),
    'audit': ('audit_events', 'id,user_id,event,object_id,created', ['user_id','event','object_id'], 'event', 'created DESC,id DESC'),
}

def fail(message, status=400):
    raise HTTPException(status, message)

def audit(c, user, event, oid):
    c.execute('INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:uid,:event,:oid,:now)', {'uid':user['id'],'event':'admin_'+event,'oid':str(oid),'now':time.time()})

def require_row(c, table, oid):
    row=c.execute(f'SELECT * FROM {table} WHERE id=:id FOR UPDATE', {'id':oid}).fetchone()
    if not row: fail('记录不存在',404)
    return row

def install_admin(app, member):
    @app.get('/api/product-images/{filename}')
    def product_image(filename:str):
        if not re.fullmatch(r'[0-9a-f]{32}\.webp',filename) or not (DIRECTORY/filename).is_file():fail('图片不存在',404)
        return FileResponse(DIRECTORY/filename,media_type='image/webp')

    @app.get('/api/pages/{slug}')
    def public_page(slug:str):
        with db() as c:
            row=c.execute('SELECT id,title,intro,sections FROM site_pages WHERE id=:id',{'id':slug}).fetchone()
            if not row: fail('页面不存在',404)
            return dict(row,sections=json.loads(row['sections']))

    def administrator(user=Depends(member)):
        if user['role']!='admin': fail('此账号没有后台管理权限',403)
        return user

    router=APIRouter(prefix='/api/admin', dependencies=[Depends(administrator)])

    @router.post('/product-image')
    def upload_image(body:ImageUpload,user=Depends(administrator)):
        result=store_image(body)
        with db(True) as c:audit(c,user,'product_image_uploaded',result['url'].rsplit('/',1)[1])
        return result

    @router.get('/session')
    def session(user=Depends(administrator)):
        return {'id':user['id'],'email':user['email'],'display_name':user['display_name']}

    @router.get('/overview')
    def overview():
        expire_orders()
        with db() as c:
            counts={key:c.execute(f'SELECT count(*) n FROM {table}').fetchone()['n'] for key,table in [('users','users'),('products','products'),('orders','orders')]}
            counts.update(dict(c.execute("SELECT COALESCE(sum(total) FILTER (WHERE status='paid'),0) revenue,count(*) FILTER (WHERE status='pending') pending,count(*) FILTER (WHERE status='paid') paid FROM orders").fetchone()))
            counts['tickets']=c.execute("SELECT count(*) n FROM tickets WHERE status IN ('open','waiting_user')").fetchone()['n']
            counts['failed_mail']=c.execute("SELECT count(*) n FROM mail_outbox WHERE status='failed'").fetchone()['n']
            counts['low_stock']=c.execute('SELECT count(*) n FROM products WHERE active=1 AND stock<10').fetchone()['n']
            rows=[dict(x) for x in c.execute("SELECT to_char(to_timestamp(paid_at) AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS date_key,sum(total) revenue,count(*) orders FROM orders WHERE status='paid' AND paid_at>=:cutoff GROUP BY date_key ORDER BY date_key",{'cutoff':time.time()-7*86400})]
        return {'counts':counts,'trend':[{'day':r['date_key'],'revenue':r['revenue'],'orders':r['orders']} for r in rows]}

    @router.get('/catalog-options')
    def catalog_options():
        return {'categories':json.loads((Path(__file__).parent/'seed.json').read_text())['categories']}

    @router.get('/list/{resource}')
    def listing(resource:str,q:str=Query('',max_length=100),status:str=Query('',max_length=80),page:int=Query(1,ge=1,le=1000000),page_size:int=Query(20,ge=1,le=100)):
        if resource not in VIEWS: fail('管理模块不存在',404)
        if resource=='orders': expire_orders()
        table,columns,search,filter_column,sort=VIEWS[resource]
        params={'q':'%'+q.replace('!','!!').replace('%','!%').replace('_','!_')+'%','status':status,'size':page_size,'offset':(page-1)*page_size}
        where=' AND '.join((["("+' OR '.join(f"{col} ILIKE :q ESCAPE '!'" for col in search)+")"] if q else [])+([f'CAST({filter_column} AS TEXT)=:status'] if status else [])) or 'TRUE'
        with db() as c:
            # Lists omit delivery secrets; delivery is available only in the explicit detail view.
            list_columns=columns.replace(',delivery','').replace(',body', '').replace(',reply,', ',')
            items=[dict(x) for x in c.execute(f'SELECT {list_columns} FROM {table} WHERE {where} ORDER BY {sort} LIMIT :size OFFSET :offset',params)]
            total=c.execute(f'SELECT count(*) n FROM {table} WHERE {where}',params).fetchone()['n']
        return {'items':items,'total':total,'page':page,'page_size':page_size}

    @router.get('/detail/{resource}/{oid}')
    def detail(resource:str,oid:str):
        if resource not in VIEWS or resource=='lottery': fail('管理模块不存在',404)
        table,columns,*_=VIEWS[resource]
        if resource in ('products','audit'):
            try: oid=int(oid)
            except ValueError: fail('记录不存在',404)
        with db() as c:
            if resource=='products':c.lock('catalog-product:'+str(oid))
            row=c.execute(f'SELECT {columns} FROM {table} WHERE id=:id',{'id':oid}).fetchone()
            if not row: fail('记录不存在',404)
            result=dict(row)
            if resource=='pages': result['sections']=json.loads(result['sections'])
            if resource=='products':
                raw_product=c.execute('SELECT * FROM products WHERE id=:id',{'id':oid}).fetchone()
                raw_variants=list(c.execute('SELECT id,name,price,stock FROM variants WHERE product_id=:id AND active=1 ORDER BY id',{'id':oid}))
                result['version']=product_version(raw_product,raw_variants)
                result['tags']=json.loads(result['tags'])
                result['variants']=[dict(x) for x in c.execute('SELECT id,name,price,stock FROM variants WHERE product_id=:id AND active=1 ORDER BY id',{'id':oid})]
            if resource=='users':
                result['order_count']=c.execute('SELECT count(*) n FROM orders WHERE user_id=:id',{'id':oid}).fetchone()['n']
        return result

    @router.post('/users/{oid}')
    def edit_user(oid:str,body:UserEdit,user=Depends(administrator)):
        with db(True) as c:
            row=require_row(c,'users',oid)
            if row['role']=='admin' and body.disabled: fail('管理员账号不能在后台禁用')
            c.execute('UPDATE users SET display_name=:display_name,phone=:phone,disabled=:disabled WHERE id=:id',dict(body.model_dump(),id=oid))
            if body.disabled: c.execute('DELETE FROM sessions WHERE user_id=:id',{'id':oid})
            audit(c,user,'user_updated',oid)
        return {'message':'用户资料已保存'}

    @router.post('/products')
    def create_product(body:ProductCreate,user=Depends(administrator)):
        return save_product(body,user,audit)

    @router.post('/products/{oid}')
    def edit_product(oid:int,body:ProductEdit,user=Depends(administrator)):
        return save_product(body,user,audit,oid)

    @router.post('/pages/{oid}')
    def edit_page(oid:str,body:PageEdit,user=Depends(administrator)):
        if not body.title.strip() or any(not x.title.strip() or not x.text.strip() for x in body.sections): fail('标题和段落内容不能为空')
        with db(True) as c:
            require_row(c,'site_pages',oid)
            c.execute('UPDATE site_pages SET title=:title,intro=:intro,sections=:sections WHERE id=:id',{'id':oid,'title':body.title,'intro':body.intro,'sections':json.dumps([x.model_dump() for x in body.sections],ensure_ascii=False)})
            audit(c,user,'page_updated',oid)
        return {'message':'页面内容已保存，前台页面已同步更新'}

    @router.post('/tickets/{oid}')
    def edit_ticket(oid:str,body:TicketEdit,user=Depends(administrator)):
        with db(True) as c:
            row=require_row(c,'tickets',oid)
            replied_at=time.time() if body.reply!=row['reply'] else row['replied_at']
            c.execute('UPDATE tickets SET status=:status,priority=:priority,reply=:reply,replied_at=:replied_at WHERE id=:id',dict(body.model_dump(),id=oid,replied_at=replied_at))
            audit(c,user,'ticket_updated',oid)
        return {'message':'工单已更新，回复可在用户工单中心查看'}

    @router.post('/actions/{resource}/{oid}')
    def action(resource:str,oid:str,body:Action,user=Depends(administrator)):
        allowed={('orders','cancel'),('mail','retry'),('coupons','revoke')}
        if (resource,body.action) not in allowed: fail('不支持此操作')
        with db(True) as c:
            row=require_row(c,VIEWS[resource][0],oid)
            if resource=='orders':
                if row['status']!='pending': fail('只能关闭待付款订单',409)
                c.execute("UPDATE orders SET status='cancelled' WHERE id=:id",{'id':oid})
            elif resource=='mail':
                if row['status']!='failed': fail('只能重试投递失败的邮件',409)
                c.execute("UPDATE mail_outbox SET status='queued',attempts=0,error=NULL,locked_at=NULL,next_attempt=:now WHERE id=:id",{'id':oid,'now':time.time()})
            else:
                if row['used']: fail('该优惠券已使用或停用',409)
                c.execute('UPDATE coupons SET used=1 WHERE id=:id',{'id':oid})
            audit(c,user,resource+'_'+body.action,oid)
        return {'message':'操作成功'}

    app.include_router(router)
