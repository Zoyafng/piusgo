"""Free daily product draws with atomic inventory, winning order and delivery."""
import secrets
import time
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend.database import db
from backend.order_service import deliver_mock_order

class DrawInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    variant_id:str=Field(min_length=1,max_length=100)

def day_key(pid):return str(pid)+':'+time.strftime('%Y-%m-%d',time.gmtime())
def fail(message,status=400):raise HTTPException(status,message)
def draw_result(row,already):
    return {'won':bool(row['won']),'already_entered':already,'variant_id':row['variant_id'],'order_id':row['order_id']}

def install_product_lottery(app,member,mock):
    @app.get('/api/products/{pid}/lottery')
    def info(pid:int):
        with db() as c:
            p=c.execute("SELECT id,name,image,price,stock FROM products WHERE id=:id AND active=1 AND product_type='lottery'",{'id':pid}).fetchone()
            if not p:fail('抽奖商品不存在或已下架',404)
            count=c.execute('SELECT count(*) n FROM lottery WHERE product_id=:id AND campaign=:campaign',{'id':pid,'campaign':day_key(pid)}).fetchone()['n']
        return {'product_id':pid,'participants':count,'probability':0.1,'ends_at':(int(time.time())//86400+1)*86400,'mock':mock}

    @app.post('/api/products/{pid}/lottery')
    def enter(pid:int,body:DrawInput,u=Depends(member)):
        if not mock:fail('抽奖交付尚未开放',503)
        campaign=day_key(pid)
        with db(True) as c:
            c.lock('lottery:'+u['id']+':'+campaign)
            old=c.execute('SELECT won,variant_id,order_id FROM lottery WHERE user_id=:uid AND campaign=:campaign',{'uid':u['id'],'campaign':campaign}).fetchone()
            if old:return draw_result(old,True)
            c.lock('catalog-product:'+str(pid))
            p=c.execute("SELECT * FROM products WHERE id=:id AND active=1 AND product_type='lottery' FOR UPDATE",{'id':pid}).fetchone()
            if not p:fail('抽奖商品不存在或已下架',404)
            v=c.execute('SELECT * FROM variants WHERE id=:id AND product_id=:pid AND active=1 FOR UPDATE',{'id':body.variant_id,'pid':pid}).fetchone()
            if not v:fail('请选择此商品的有效规格')
            if min(p['stock'],v['stock'])<1:fail('所选规格奖品已发完，请选择其他规格',409)
            won=secrets.randbelow(10)==0;oid=None;now=time.time()
            if won:
                oid='PG'+secrets.token_hex(24).upper()
                c.execute('UPDATE variants SET stock=stock-1 WHERE id=:id',{'id':v['id']})
                c.execute('UPDATE products SET stock=stock-1 WHERE id=:id',{'id':pid})
                c.execute("INSERT INTO orders(id,user_id,product_id,quantity,account,total,discount,status,created,request_key,variant_id,variant_name,payment_method,source) VALUES(:id,:uid,:pid,1,:email,0,:discount,'pending',:now,:key,:vid,:name,'mock','lottery')",{'id':oid,'uid':u['id'],'pid':pid,'email':u['email'],'discount':v['price'],'now':now,'key':'lottery:'+campaign,'vid':v['id'],'name':v['name']})
                deliver_mock_order(c,{'name':p['name'],'variant_name':v['name'],'quantity':1,'account':u['email'],'user_id':u['id']},oid,'mock')
            c.execute('INSERT INTO lottery(user_id,campaign,won,created,product_id,variant_id,order_id) VALUES(:uid,:campaign,:won,:now,:pid,:vid,:oid)',{'uid':u['id'],'campaign':campaign,'won':int(won),'now':now,'pid':pid,'vid':v['id'],'oid':oid})
            c.execute("INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:uid,'product_lottery_entered',:id,:now)",{'uid':u['id'],'id':str(pid),'now':now})
        return draw_result({'won':won,'variant_id':v['id'],'order_id':oid},False)
