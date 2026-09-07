"""Guest/member order authorization and transactional mock fulfillment."""
import secrets
import time
from fastapi import HTTPException
from backend.database import db

GUIDE = ['在订单详情中查看并复制卡密。','按照商品的兑换说明使用卡密。当前为演示卡密，不能用于真实兑换。','如遇问题，使用本订单创建工单，并填写问题详情。']

def fail(message,status=400):raise HTTPException(status,message)

def get_order(c,oid,user=None,guest=None,by_number=False,lock=False):
    row=c.execute('SELECT o.*,COALESCE(o.product_name,p.name) AS name,COALESCE(o.product_image,p.image) AS image,m.status AS email_status FROM orders o JOIN products p ON p.id=o.product_id LEFT JOIN mail_outbox m ON m.order_id=o.id WHERE o.id=:id'+(' FOR UPDATE OF o' if lock else ''),{'id':oid}).fetchone()
    if not row:fail('订单不存在或无权访问',404)
    if row['user_id']:
        allowed=user is not None and row['user_id']==user['id']
    else:
        # New guest order IDs contain 192 random bits and are possession-based lookup secrets.
        allowed=(guest is not None and row['guest_key']==guest) or (by_number and len(oid)==50)
    if not allowed:fail('订单不存在或无权访问',404)
    return row

def public_order(row,mask=False):
    keys=['id','source','product_id','quantity','account','total','discount','status','delivery','created','paid_at','variant_id','variant_name','payment_method','name','image','email_status']
    result={k:row.get(k) for k in keys}
    if row['status']=='paid' and not result['email_status']:result['email_status']='not_requested'
    result['kind']='member' if row['user_id'] else 'guest'
    result['expires_at']=row['created']+1800
    result['usage_guide']=GUIDE if row['status']=='paid' else []
    if mask:
        local,_,domain=row['account'].partition('@');result['account']=local[:1]+'***@'+domain
    return result

def expire_orders():
    with db(True) as c:
        c.execute("UPDATE orders SET status='cancelled' WHERE status='pending' AND created<:cutoff",{'cutoff':time.time()-1800})

def create_order(body,user,guest,address):
    uid=user['id'] if user else None
    if not uid and (body.coupon_id or body.payment_method=='balance'):fail('优惠券和余额付款需要登录',401)
    scope=uid or 'guest:'+guest
    with db(True) as c:
        c.lock('order:'+scope+':'+body.request_key)
        existing=c.execute('SELECT * FROM orders WHERE '+('user_id=:owner' if uid else 'guest_key=:owner')+' AND request_key=:key',{'owner':uid or guest,'key':body.request_key}).fetchone()
        variant_id=body.variant_id or str(body.product_id)+'-standard'
        if existing:
            if (existing['product_id'],existing['variant_id'],existing['quantity'],existing['coupon_id'],existing['account'])!=(body.product_id,variant_id,body.quantity,body.coupon_id,address):fail('重复请求标识不能用于不同订单内容',409)
            if existing['status']=='pending' and existing['payment_method']!=body.payment_method:fail('重复请求标识不能用于不同支付方式',409)
            return public_order(get_order(c,existing['id'],user,guest))
        c.lock('catalog-product:'+str(body.product_id))
        product=c.execute('SELECT * FROM products WHERE id=:id',{'id':body.product_id}).fetchone()
        variant=c.execute('SELECT * FROM variants WHERE id=:id AND product_id=:pid AND active=1',{'id':variant_id,'pid':body.product_id}).fetchone()
        if not product or not product['active'] or not variant or min(product['stock'],variant['stock'])<body.quantity:fail('所选商品或规格库存不足')
        if product['product_type']=='lottery':fail('抽奖商品请从抽奖入口参与')
        total=variant['price']*body.quantity;discount=0
        if body.coupon_id:
            if not product['coupon_eligible']:fail('此商品不参与活动优惠券')
            coupon=c.execute('SELECT * FROM coupons WHERE id=:id AND user_id=:uid AND used=0',{'id':body.coupon_id,'uid':uid}).fetchone()
            if not coupon or total<coupon['minimum']:fail('优惠券不可用或未达到使用门槛')
            if coupon['kind'].startswith('lottery:') and product['id']!=188:fail('抽奖优惠券仅限指定商品')
            discount=min(total,coupon['amount'])
        oid='PG'+secrets.token_hex(24).upper()
        c.execute('INSERT INTO orders(id,user_id,guest_key,product_id,quantity,account,total,discount,coupon_id,status,created,request_key,variant_id,variant_name,payment_method,product_name,product_image) VALUES(:id,:uid,:guest,:pid,:quantity,:email,:total,:discount,:coupon,\'pending\',:created,:key,:variant,:name,:method,:product_name,:product_image)',{'id':oid,'uid':uid,'guest':None if uid else guest,'pid':product['id'],'quantity':body.quantity,'email':address,'total':total-discount,'discount':discount,'coupon':body.coupon_id,'created':time.time(),'key':body.request_key,'variant':variant_id,'name':variant['name'],'method':body.payment_method,'product_name':product['name'],'product_image':product['image']})
        return public_order(get_order(c,oid,user,guest))

def complete_mock_payment(oid,method,user,guest,by_number=False):
    with db(True) as c:
        snapshot=c.execute('SELECT product_id FROM orders WHERE id=:id',{'id':oid}).fetchone()
        if snapshot:c.lock('catalog-product:'+str(snapshot['product_id']))
        o=get_order(c,oid,user,guest,by_number,True)
        if o['status']=='paid':return public_order(o,by_number)
        if o['status']!='pending':fail('当前订单不能支付')
        if time.time()-o['created']>=1800:fail('订单已超时，请重新下单')
        if method=='balance' and (not user or not o['user_id']):fail('游客订单不能使用账户余额',401)
        if o['coupon_id']:
            r=c.execute('UPDATE coupons SET used=1 WHERE id=:id AND user_id=:uid AND used=0',{'id':o['coupon_id'],'uid':o['user_id']})
            if r.rowcount!=1:fail('优惠券已被使用，请重新下单')
        if method=='balance':
            r=c.execute('UPDATE users SET balance=balance-:total WHERE id=:id AND balance>=:total',{'total':o['total'],'id':o['user_id']})
            if r.rowcount!=1:fail('余额不足，请充值或更换支付方式')
        for table,ident in [('variants',o['variant_id']),('products',o['product_id'])]:
            r=c.execute(f'UPDATE {table} SET stock=stock-:quantity WHERE id=:id AND stock>=:quantity',{'quantity':o['quantity'],'id':ident})
            if r.rowcount!=1:fail('所选规格库存不足，请重新选择商品')
        deliver_mock_order(c,o,oid,method)
        return public_order(get_order(c,oid,user,guest,by_number),by_number)


def deliver_mock_order(c,o,oid,method):
    delivery='\n'.join('MOCK-'+secrets.token_hex(12).upper()+'（演示卡密，不可兑换）' for _ in range(o['quantity']))
    now=time.time()
    c.execute("UPDATE orders SET status='paid',paid_at=:now,delivery=:delivery,payment_method=:method WHERE id=:id",{'id':oid,'now':now,'delivery':delivery,'method':method})
    subject='AI模享订单交付 '+oid
    content=f"商品：{o['name']}\n规格：{o['variant_name']}\n订单号：{oid}\n\n卡密：\n{delivery}\n\n使用指南：\n"+'\n'.join(GUIDE)
    c.execute("INSERT INTO mail_outbox(id,order_id,recipient,subject,body,status,next_attempt,created) VALUES(:id,:oid,:recipient,:subject,:body,'queued',:now,:now) ON CONFLICT(order_id) DO NOTHING",{'id':secrets.token_hex(16),'oid':oid,'recipient':o['account'],'subject':subject,'body':content,'now':now})
    c.execute('INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:uid,:event,:oid,:now)',{'uid':o['user_id'],'event':'mock_order_paid','oid':oid,'now':now})
