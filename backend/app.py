"""Local mock commerce API. All financial values are integer cents."""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.exc import IntegrityError
from backend.database import db
from backend import order_service
import qrcode

ROOT = Path(__file__).parent
MOCK = os.getenv('MOCK_MODE', 'true').lower() == 'true'
PRODUCTION = os.getenv('APP_ENV') == 'production'
if PRODUCTION and MOCK:
    raise RuntimeError('MOCK_MODE must be disabled before production deployment')
ORIGIN = os.getenv('WEB_ORIGIN', 'http://localhost:3000')
app = FastAPI(title='PiusGo 数字服务 API', version='0.1.0')


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RequestSizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope['method'] in ('GET','HEAD','OPTIONS'):
            return await self.app(scope,receive,send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type']=='http.disconnect':
                return
            body.extend(message.get('body',b''))
            maximum=1500000 if scope.get('path')=='/api/admin/product-image' else 65536
            if len(body)>maximum:
                return await JSONResponse({'detail':'请求内容过大'},status_code=413)(scope,receive,send)
            if not message.get('more_body',False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)


app.add_middleware(RequestSizeLimit)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Never echo rejected password, token, or other submitted values.
    return JSONResponse({'detail':'请求字段不正确，请检查输入','fields':['.'.join(map(str,e['loc'])) for e in exc.errors()]},status_code=422)




def fail(message, status=400):
    raise HTTPException(status, message)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def password_hash(password):
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
    return salt + ':' + key


def password_matches(password, stored):
    salt, expected = stored.split(':')
    actual = hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
    return hmac.compare_digest(actual, expected)


def limit(key, maximum=10, seconds=600):
    now = time.time()
    with db(True) as c:
        c.lock('rate:'+key)
        c.execute('DELETE FROM rates WHERE expires < :p0', {'p0': now})
        row = c.execute('SELECT * FROM rates WHERE key=:p0', {'p0': key}).fetchone()
        if row and row['hits'] >= maximum:
            fail('操作过于频繁，请稍后重试', 429)
        c.execute('INSERT INTO rates VALUES(:p0,1,:p1) ON CONFLICT(key) DO UPDATE SET hits=rates.hits+1', {'p0': key, 'p1': now+seconds})


@app.middleware('http')
async def security(request: Request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        allowed = {ORIGIN} if PRODUCTION else {ORIGIN,'http://localhost:3000','http://127.0.0.1:3000'}
        if request.headers.get('origin') not in allowed or request.headers.get('x-requested-with') != 'PiusGo':
            return Response('Forbidden origin', status_code=403)
        if request.headers.get('content-type','').split(';')[0].strip()!='application/json':
            return JSONResponse({'detail':'请求必须为 JSON'},status_code=415)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


def optional_member(request: Request):
    token = request.cookies.get('piusgo_session', '')
    with db() as c:
        u = c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=:p0 AND s.expires>:p1 AND u.disabled=0', {'p0': digest(token), 'p1': time.time()}).fetchone()
    return dict(u) if u else None


def member(request: Request):
    u=optional_member(request)
    if not u:fail('请先登录后继续',401)
    return u


def guest_identity(request: Request,response=None):
    token=request.cookies.get('piusgo_guest','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
        if response is None:return None
        token=secrets.token_urlsafe(32)
        response.set_cookie('piusgo_guest',token,httponly=True,secure=PRODUCTION,samesite='lax',max_age=31536000,path='/')
    return digest(token)


@app.get('/api/checkout/session')
def checkout_session(request:Request,response:Response):
    guest_identity(request,response)
    u=optional_member(request)
    return {'user':me(u) if u else None}


def start_session(response, user_id, request, expected_password=None):
    token = secrets.token_urlsafe(32)
    with db(True) as c:
        latest=c.execute('SELECT * FROM users WHERE id=:id FOR UPDATE',{'id':user_id}).fetchone()
        if not latest or latest['disabled'] or (expected_password is not None and not hmac.compare_digest(latest['password'],expected_password)):
            fail('账号凭据已更新，请重新登录',401)
        c.execute('UPDATE users SET previous_login_at=last_login_at,previous_login_ip=last_login_ip,last_login_at=:now,last_login_ip=:ip WHERE id=:id',{'now':time.time(),'ip':request.client.host[:64],'id':user_id})
        c.execute('DELETE FROM sessions WHERE expires < :p0', {'p0': time.time()})
        c.execute('INSERT INTO sessions VALUES(:p0,:p1,:p2)', {'p0': digest(token), 'p1': user_id, 'p2': time.time()+604800})
    response.set_cookie('piusgo_session',token,httponly=True,secure=PRODUCTION,samesite='lax',max_age=604800,path='/')


def email(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[^\s@]{1,64}@[^\s@.]+(?:\.[^\s@.]+)+',value) or len(value)>191:
        fail('请输入有效的邮箱地址')
    return value


def challenge(purpose, address, answer, duration=600):
    key = secrets.token_urlsafe(24)
    with db(True) as c:
        c.execute('DELETE FROM challenges WHERE expires < :p0', {'p0': time.time()})
        c.execute('INSERT INTO challenges(id,purpose,email,answer,expires) VALUES(:p0,:p1,:p2,:p3,:p4)', {'p0': key, 'p1': purpose, 'p2': address, 'p3': digest(answer.upper()), 'p4': time.time()+duration})
    return key


def consume_challenge(key, purpose, address, answer):
    valid = False
    with db(True) as c:
        row = c.execute('SELECT * FROM challenges WHERE id=:p0 FOR UPDATE', {'p0': key}).fetchone()
        if row and row['expires']>time.time() and row['attempts']<5:
            valid = row['purpose']==purpose and row['email']==address and hmac.compare_digest(row['answer'],digest(answer.strip().upper()))
            if valid:
                c.execute('DELETE FROM challenges WHERE id=:p0', {'p0': key})
            else:
                c.execute('UPDATE challenges SET attempts=attempts+1 WHERE id=:p0', {'p0': key})
    if not valid:
        fail('验证码不正确或已过期，请重新获取')


class Credentials(InputModel):
    account: str = Field(min_length=1,max_length=191)
    password: str = Field(min_length=8,max_length=128)
    code: str = Field(min_length=1,max_length=8)
    challenge_id: str = Field(min_length=1,max_length=100)
    confirmation: Optional[str] = Field(default=None,max_length=128)


class Registration(InputModel):
    account: str = Field(min_length=1,max_length=191)
    password: str = Field(min_length=8,max_length=128)
    confirmation: str = Field(min_length=8,max_length=128)


class EmailCode(InputModel):
    email: str = Field(max_length=191)
    purpose: Literal['reset']


@app.get('/api/config')
def config():
    return {'mock':MOCK,'emailMode':'mock' if MOCK else 'unconfigured','paymentMode':'mock' if MOCK else 'unconfigured'}


@app.get('/api/auth/captcha')
def captcha(request: Request):
    limit('captcha:'+request.client.host,120)
    answer = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(4))
    key = challenge('login','',answer,300)
    image = Image.new('RGB',(320,92),'#e7f3f4')
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(os.getenv('CAPTCHA_FONT','/System/Library/Fonts/Supplemental/Arial Bold.ttf'),58)
    except OSError:
        font = ImageFont.load_default(size=52)
    for i in range(7):
        draw.line([(secrets.randbelow(320),secrets.randbelow(92)),(secrets.randbelow(320),secrets.randbelow(92))],fill=secrets.choice(['#79babc','#7b9de4','#efc278']),width=2)
    for i, letter in enumerate(answer):
        draw.text((26+i*72,10+secrets.randbelow(12)),letter,fill=['#087f75','#172033','#2455cb','#087f75'][i],font=font)
    buff = io.BytesIO()
    image.save(buff,format='PNG')
    return {'id':key,'image':'data:image/png;base64,'+base64.b64encode(buff.getvalue()).decode()}


@app.post('/api/auth/code')
def send_code(body: EmailCode, request: Request):
    address = email(body.email)
    limit('email-ip:'+request.client.host,20)
    limit('email:'+address+':'+body.purpose,1,60)
    if not MOCK:
        fail('邮件服务尚未配置，请联系管理员',503)
    code = str(secrets.randbelow(900000)+100000)
    key = challenge(body.purpose,address,code)
    return {'id':key,'message':'若邮箱符合条件，验证码将发送至该邮箱','mock_code':code}


@app.post('/api/auth/register')
def register(body: Registration, request: Request, response: Response):
    limit('register:'+request.client.host,15)
    address = email(body.account)
    if body.password != body.confirmation:
        fail('两次输入的密码不一致')
    uid = secrets.token_hex(16)
    try:
        with db(True) as c:
            c.execute('INSERT INTO users(id,email,password,created) VALUES(:p0,:p1,:p2,:p3)', {'p0': uid, 'p1': address, 'p2': password_hash(body.password), 'p3': time.time()})
    except IntegrityError:
        fail('该邮箱已注册，请直接登录或找回密码',409)
    start_session(response,uid,request)
    return {'message':'注册成功'}


@app.post('/api/auth/login')
def login(body: Credentials, request: Request, response: Response):
    limit('login-ip:'+request.client.host,30)
    address = body.account.strip().lower()
    limit('login:'+address,10)
    consume_challenge(body.challenge_id,'login','',body.code)
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE email=:p0', {'p0': address}).fetchone()
    if not u or not password_matches(body.password,u['password']):
        fail('账号或密码不正确',401)
    start_session(response,u['id'],request,u['password'])
    return {'message':'登录成功'}


@app.post('/api/auth/reset')
def reset(body: Credentials, request: Request):
    limit('reset:'+request.client.host,10)
    address = email(body.account)
    if body.password != body.confirmation:
        fail('两次输入的密码不一致')
    consume_challenge(body.challenge_id,'reset',address,body.code)
    with db(True) as c:
        u = c.execute('SELECT id FROM users WHERE email=:p0', {'p0': address}).fetchone()
        if u:
            c.execute('UPDATE users SET password=:p0 WHERE id=:p1', {'p0': password_hash(body.password), 'p1': u['id']})
            c.execute('DELETE FROM sessions WHERE user_id=:p0', {'p0': u['id']})
    return {'message':'如邮箱已注册，密码已更新，请重新登录'}


@app.post('/api/auth/logout')
def logout(request: Request,response: Response):
    with db(True) as c:
        c.execute('DELETE FROM sessions WHERE token=:p0', {'p0': digest(request.cookies.get('piusgo_session',''))})
    response.delete_cookie('piusgo_session',path='/')
    return {'message':'已退出登录'}


@app.get('/api/me')
def me(u=Depends(member)):
    with db() as c:
        coupons = [dict(x) for x in c.execute('SELECT * FROM coupons WHERE user_id=:p0', {'p0': u['id']})]
        counts = c.execute("SELECT count(*) total,COALESCE(sum(CASE WHEN status='paid' THEN 1 ELSE 0 END),0) paid FROM orders WHERE user_id=:p0", {'p0': u['id']}).fetchone()
        ledger = [dict(x) for x in c.execute('SELECT * FROM ledger WHERE user_id=:p0 ORDER BY created DESC', {'p0': u['id']})]
    return {'id':u['id'],'role':u['role'],'email':u['email'],'balance':u['balance'],'created':u['created'],'display_name':u['display_name'],'phone':u['phone'],'last_login_at':u['last_login_at'],'last_login_ip':u['last_login_ip'],'previous_login_at':u['previous_login_at'],'previous_login_ip':u['previous_login_ip'],'coupons':coupons,'orders':dict(counts),'ledger':ledger,'mock':MOCK}


@app.get('/api/products')
def products():
    with db() as c:
        items = [dict(x) for x in c.execute("SELECT p.id,p.category,p.name,p.image,p.price,p.stock,p.badge,p.tags,p.active,p.product_type,p.coupon_eligible,(SELECT count(*) FROM variants v WHERE v.product_id=p.id AND v.active=1) variant_count,(SELECT count(*) FROM lottery l WHERE l.product_id=p.id AND l.campaign=CAST(p.id AS TEXT)||':'||:day) lottery_participants FROM products p WHERE p.active=1 ORDER BY p.id",{'day':time.strftime('%Y-%m-%d',time.gmtime())})]
    # Preserve public reference card order, not numerical identifiers.
    seed = json.loads((ROOT/'seed.json').read_text())
    order = {p['id']:i for i,p in enumerate(seed['products'])}
    items.sort(key=lambda p:order.get(p['id'],999))
    for item in items:
        item['tags'] = json.loads(item['tags'])
    return {'items':items,'categories':seed['categories'],'mock':MOCK,'lottery_ends_at':(int(time.time())//86400+1)*86400}


@app.get('/api/products/{pid}')
def product(pid: int):
    for p in products()['items']:
        if p['id']==pid:
            with db() as c:
                p['variants'] = [dict(v) for v in c.execute("SELECT * FROM variants WHERE product_id=:p0 AND active=1 ORDER BY price,CASE WHEN id LIKE '%-standard' THEN 0 ELSE 1 END,id", {'p0': pid})]
            return p
    fail('商品不存在',404)


class NewOrder(InputModel):
    product_id: StrictInt
    quantity: int = Field(strict=True,ge=1,le=10)
    account: Optional[str] = Field(default=None,max_length=191)
    variant_id: Optional[str] = Field(default=None,max_length=100)
    payment_method: Literal['mock','alipay','wechat','balance'] = 'mock'
    coupon_id: Optional[str] = Field(default=None,max_length=64)
    request_key: str = Field(min_length=16,max_length=100)


def own_order(c, oid, uid):
    row = c.execute('SELECT * FROM orders WHERE id=:p0 AND user_id=:p1 FOR UPDATE', {'p0': oid, 'p1': uid}).fetchone()
    if not row:
        fail('订单不存在',404)
    return row


@app.post('/api/orders')
def create_order(body:NewOrder,request:Request,response:Response,u=Depends(optional_member)):
    guest=guest_identity(request,response)
    limit('order:'+(u['id'] if u else request.client.host),30,60)
    address=u['email'] if u else email(body.account or '')
    return order_service.create_order(body,u,guest,address)


@app.get('/api/orders')
def orders(u=Depends(member),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    order_service.expire_orders()
    with db() as c:
        rows=c.execute('SELECT o.*,COALESCE(o.product_name,p.name) AS name,COALESCE(o.product_image,p.image) AS image,m.status AS email_status FROM orders o JOIN products p ON p.id=o.product_id LEFT JOIN mail_outbox m ON m.order_id=o.id WHERE o.user_id=:uid ORDER BY o.created DESC,o.id DESC LIMIT :size OFFSET :offset',{'uid':u['id'],'size':page_size,'offset':(page-1)*page_size})
        total=c.execute('SELECT count(*) AS n FROM orders WHERE user_id=:uid',{'uid':u['id']}).fetchone()['n']
        return {'items':[order_service.public_order(x) for x in rows],'total':total,'page':page,'page_size':page_size}


class OrderLookup(InputModel):
    order_id:str=Field(min_length=3,max_length=50)


@app.post('/api/orders/lookup')
def lookup_order(body:OrderLookup,request:Request,u=Depends(optional_member)):
    limit('lookup:'+request.client.host,120,60)
    order_service.expire_orders()
    with db() as c:
        o=order_service.get_order(c,body.order_id.strip().upper(),u,guest_identity(request),by_number=True)
        return order_service.public_order(o,not bool(u and o['user_id']==u['id']))


@app.get('/api/orders/{oid}/status')
def order_status(oid:str,request:Request,u=Depends(optional_member)):
    limit('status:'+request.client.host,240,60)
    order_service.expire_orders()
    with db() as c:
        o=order_service.get_order(c,oid,u,guest_identity(request),by_number=True)
        return order_service.public_order(o,not bool(u and o['user_id']==u['id']))


@app.post('/api/orders/{oid}/payment-session')
def payment_session(oid:str,request:Request,u=Depends(optional_member)):
    if not MOCK:fail('支付商户尚未配置',503)
    limit('payment-session:'+request.client.host,30,60)
    order_service.expire_orders()
    with db() as c:o=order_service.get_order(c,oid,u,guest_identity(request),True)
    if o['status']!='pending':fail('当前订单不能支付')
    stream=io.BytesIO()
    # Deliberately not an Alipay merchant URL: scanning cannot transfer funds.
    qrcode.make('piusgo-mock:'+oid).save(stream,format='PNG')
    return {'order_id':oid,'method':o['payment_method'],'amount':o['total'],'expires_at':o['created']+1800,'qr_image':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode(),'mock':True}


class Payment(InputModel):
    method:Literal['mock','alipay','wechat','balance']


@app.post('/api/orders/{oid}/pay')
def pay(oid:str,body:Payment,request:Request,u=Depends(optional_member)):
    if not MOCK:fail('支付与交付服务尚未接入',503)
    limit('pay:'+(u['id'] if u else request.client.host),30,60)
    return order_service.complete_mock_payment(oid,body.method,u,guest_identity(request),by_number=True)


@app.post('/api/orders/{oid}/cancel')
def cancel(oid:str,request:Request,u=Depends(optional_member)):
    with db(True) as c:
        o=order_service.get_order(c,oid,u,guest_identity(request),by_number=True,lock=True)
        if o['status']!='pending':fail('仅待付款订单可以取消')
        c.execute("UPDATE orders SET status='cancelled' WHERE id=:id",{'id':oid})
    return {'message':'订单已取消'}


@app.get('/api/orders/{oid}/email')
def order_email(oid:str,request:Request,u=Depends(optional_member)):
    with db() as c:
        order_service.get_order(c,oid,u,guest_identity(request),by_number=True)
        mail=c.execute('SELECT subject,body,status FROM mail_outbox WHERE order_id=:id',{'id':oid}).fetchone()
        if not mail:fail('暂未生成交付邮件',404)
        return dict(mail)


class Ticket(InputModel):
    title:str=Field(min_length=3,max_length=100)
    body:str=Field(min_length=10,max_length=3000)
    order_id:str=Field(min_length=3,max_length=50)
    priority:Literal['low','medium','high']='medium'


def ticket_scope(u,guest,alias=''):
    prefix=alias+'.' if alias else ''
    if u:return ('('+prefix+'user_id=:uid OR '+prefix+'guest_key=:guest)',{'uid':u['id'],'guest':guest or ''})
    return (prefix+'guest_key=:guest',{'guest':guest or ''})


@app.get('/api/tickets/eligible-orders')
def ticket_orders(request:Request,u=Depends(optional_member)):
    clause,scope=ticket_scope(u,guest_identity(request),'o')
    with db() as c:
        rows=c.execute("SELECT o.*,COALESCE(o.product_name,p.name) AS name,COALESCE(o.product_image,p.image) AS image FROM orders o JOIN products p ON p.id=o.product_id WHERE "+clause+" AND o.status='paid' AND NOT EXISTS(SELECT 1 FROM tickets t WHERE t.order_id=o.id) ORDER BY o.created DESC LIMIT 100",scope)
        return {'items':[order_service.public_order(x) for x in rows]}


@app.get('/api/tickets')
def tickets(request:Request,u=Depends(optional_member),q:str=Query('',max_length=100),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),status:Literal['all','open','waiting_user','resolved','closed']='all'):
    clause,scope=ticket_scope(u,guest_identity(request))
    query=q.strip().replace('!','!!').replace('%','!%').replace('_','!_')
    where=clause+" AND (title ILIKE :q ESCAPE '!' OR id ILIKE :q ESCAPE '!')"
    params={**scope,'q':'%'+query+'%','size':page_size,'offset':(page-1)*page_size}
    if status!='all':where+=' AND status=:status';params['status']=status
    with db() as c:
        items=[dict(x) for x in c.execute('SELECT id,order_id,title,body,status,priority,created,reply,replied_at FROM tickets WHERE '+where+' ORDER BY created DESC,id DESC LIMIT :size OFFSET :offset',params)]
        total=c.execute('SELECT count(*) AS n FROM tickets WHERE '+where,params).fetchone()['n']
        counts={r['status']:r['n'] for r in c.execute('SELECT status,count(*) AS n FROM tickets WHERE '+clause+' GROUP BY status',scope)}
        return {'items':items,'total':total,'page':page,'page_size':page_size,'counts':counts}


@app.post('/api/tickets')
def ticket(body:Ticket,request:Request,response:Response,u=Depends(optional_member)):
    guest=guest_identity(request,response)
    limit('ticket:'+(u['id'] if u else request.client.host),10)
    if len(body.title.strip())<3 or len(body.body.strip())<10:fail('请填写完整的问题标题和详情')
    with db(True) as c:
        o=order_service.get_order(c,body.order_id,u,guest,by_number=True,lock=True)
        if o['status']!='paid':fail('支付完成后才可创建售后工单')
        if c.execute('SELECT id FROM tickets WHERE order_id=:id',{'id':o['id']}).fetchone():fail('此订单已有工单，请在工单记录中跟进',409)
        tid='TK'+secrets.token_hex(12).upper()
        c.execute("INSERT INTO tickets(id,user_id,guest_key,order_id,title,body,status,priority,created) VALUES(:id,:uid,:guest,:oid,:title,:body,'open',:priority,:now)",{'id':tid,'uid':u['id'] if o['user_id'] else None,'guest':None if o['user_id'] else guest,'oid':o['id'],'title':body.title.strip(),'body':body.body.strip(),'priority':body.priority,'now':time.time()})
    return {'id':tid,'message':'工单已提交'}


@app.post('/api/coupons/newcomer')
def newcomer(u=Depends(member)):
    with db(True) as c:
        c.execute('INSERT INTO coupons VALUES(:p0,:p1,:p2,:p3,:p4,0) ON CONFLICT(user_id,kind) DO NOTHING', {'p0': secrets.token_hex(12), 'p1': u['id'], 'p2': 'newcomer', 'p3': 1000, 'p4': 10000})
    return {'message':'新人优惠券已到账：满 100 元减 10 元，每位用户限领一次'}


class ProfileUpdate(InputModel):
    display_name:str=Field(min_length=1,max_length=40)
    phone:str=Field(default='',max_length=24)


@app.post('/api/me/profile')
def update_profile(body:ProfileUpdate,u=Depends(member)):
    limit('profile:'+u['id'],10,60)
    name=body.display_name.strip();phone=body.phone.strip()
    if not name or any(ord(ch)<32 for ch in name):fail('请输入有效昵称')
    if phone and not re.fullmatch(r'\+?[0-9][0-9 -]{5,22}[0-9]',phone):fail('请输入有效联系电话')
    with db(True) as c:
        c.execute('UPDATE users SET display_name=:name,phone=:phone WHERE id=:id',{'name':name,'phone':phone,'id':u['id']})
        c.execute('INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:uid,\'profile_updated\',:uid,:now)',{'uid':u['id'],'now':time.time()})
    return {'message':'个人信息已保存'}


class PasswordChange(InputModel):
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=8,max_length=128)
    confirmation:str=Field(min_length=8,max_length=128)


@app.post('/api/me/password')
def change_password(body:PasswordChange,response:Response,u=Depends(member)):
    limit('password-change:'+u['id'],5,600)
    if body.new_password!=body.confirmation:fail('两次新密码输入不一致')
    if body.new_password==body.current_password:fail('新密码不能与当前密码相同')
    with db(True) as c:
        latest=c.execute('SELECT password FROM users WHERE id=:id FOR UPDATE',{'id':u['id']}).fetchone()
        if not latest or not password_matches(body.current_password,latest['password']):fail('当前密码不正确',400)
        c.execute('UPDATE users SET password=:password WHERE id=:id',{'password':password_hash(body.new_password),'id':u['id']})
        c.execute('DELETE FROM sessions WHERE user_id=:id',{'id':u['id']})
        c.execute('INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:uid,\'password_changed\',:uid,:now)',{'uid':u['id'],'now':time.time()})
    response.delete_cookie('piusgo_session',path='/')
    return {'message':'密码已修改，所有设备已退出，请重新登录'}


RECHARGE_TIERS=[{'minimum':10000,'bonus':500},{'minimum':30000,'bonus':2000},{'minimum':50000,'bonus':5000}]

@app.get('/api/recharges/options')
def recharge_options():
    return {'minimum':1000,'maximum':1000000,'presets':[1000,5000,10000,20000,50000,100000],'tiers':RECHARGE_TIERS,'methods':['alipay','wechat'],'mock':MOCK}


class Recharge(InputModel):
    amount:int=Field(strict=True,ge=1000,le=1000000)
    payment_method:Literal['mock','alipay','wechat']='mock'
    request_key:str=Field(min_length=16,max_length=100)


@app.post('/api/recharges')
def recharge(body:Recharge,u=Depends(member)):
    limit('recharge:'+u['id'],10,60)
    if not MOCK:fail('充值支付尚未接入',503)
    bonus=max((t['bonus'] for t in RECHARGE_TIERS if body.amount>=t['minimum']),default=0)
    with db(True) as c:
        c.lock('recharge:'+u['id']+':'+body.request_key)
        exists=c.execute('SELECT * FROM ledger WHERE user_id=:uid AND request_key=:key',{'uid':u['id'],'key':body.request_key}).fetchone()
        if exists:
            if exists['amount']!=body.amount or exists['payment_method']!=body.payment_method:fail('重复请求标识不能用于不同的充值内容',409)
            return dict(exists)
        rid=secrets.token_hex(12)
        c.execute('INSERT INTO ledger(id,user_id,amount,bonus,created,request_key,payment_method) VALUES(:id,:uid,:amount,:bonus,:now,:key,:method)',{'id':rid,'uid':u['id'],'amount':body.amount,'bonus':bonus,'now':time.time(),'key':body.request_key,'method':body.payment_method})
        c.execute('UPDATE users SET balance=balance+:amount WHERE id=:uid',{'amount':body.amount+bonus,'uid':u['id']})
        c.execute('INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:uid,\'mock_recharge\',:id,:now)',{'uid':u['id'],'id':rid,'now':time.time()})
    return {'id':rid,'message':'模拟充值成功','amount':body.amount,'bonus':bonus,'payment_method':body.payment_method}


@app.get('/api/lottery')
def lottery_info():
    return {'items':[p for p in products()['items'] if p['product_type']=='lottery'],'mock':MOCK}


@app.post('/api/lottery/enter')
def enter_lottery(u=Depends(member)):
    fail('旧抽奖入口已停用，请选择已上架的抽奖商品参与',410)


from backend.admin import install_admin
install_admin(app, member)

from backend.product_lottery import install_product_lottery
install_product_lottery(app, member, MOCK)
