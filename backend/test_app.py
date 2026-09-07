"""HTTP integration tests against an isolated SQLite database, never user data."""
import concurrent.futures
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import psycopg
from sqlalchemy import text
from sqlalchemy.schema import CreateSchema, DropSchema
from backend.database import database_url, migration_url, make_engine
from backend.schema import metadata
from backend.seed import seed
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


class Client:
    def __init__(self, base):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self, path, body=None, origin='http://localhost:3000'):
        headers={'Origin':origin,'X-Requested-With':'PiusGo','Content-Type':'application/json'}
        request=urllib.request.Request(self.base+'/api'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(request,timeout=15) as response:
                return response.status,json.load(response)
        except urllib.error.HTTPError as e:
            raw=e.read().decode()
            try:return e.code,json.loads(raw)
            except ValueError:return e.code,raw


class CommerceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='piusgo-tests-')
        cls.schema='piusgo_test_'+secrets.token_hex(8)
        cls.admin=make_engine(url=migration_url())
        with cls.admin.begin() as c:c.execute(CreateSchema(cls.schema))
        cls.engine=make_engine(url=migration_url(),schema=cls.schema)
        with cls.engine.begin() as c:
            metadata.create_all(c)
            seed(c)
            from backend.grants import grant_runtime
            grant_runtime(c,cls.schema)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        cls.base=f'http://127.0.0.1:{port}'
        env={**os.environ,'DATABASE_URL':database_url(),'DATABASE_SCHEMA':cls.schema,'MOCK_MODE':'true','APP_ENV':'test','MAIL_MODE':'mock','MAIL_OUTBOX_DIR':str(Path(cls.temp.name)/'mail')}
        cls.log=open(Path(cls.temp.name)/'server.log','w+')
        cls.server=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',str(port)],cwd=Path(__file__).resolve().parents[1],env=env,stdout=cls.log,stderr=cls.log)
        for _ in range(100):
            try:
                if Client(cls.base).call('/config')[0]==200:return
            except OSError:time.sleep(.05)
        cls.server.terminate();cls.server.wait(timeout=5);cls.log.seek(0)
        raise RuntimeError(cls.log.read())

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate();cls.server.wait(timeout=10);cls.log.close();cls.temp.cleanup()
        cls.engine.dispose()
        with cls.admin.begin() as c:c.execute(DropSchema(cls.schema,cascade=True))
        cls.admin.dispose()

    def _fixture_db(self):
        return psycopg.connect(migration_url().replace('postgresql+psycopg://','postgresql://',1),options='-c search_path='+self.schema)

    def setUp(self):
        with self._fixture_db() as c:c.execute('DELETE FROM rates')

    def user(self):
        client=Client(self.base)
        address=secrets.token_hex(6)+'@example.test'
        status,data=client.call('/auth/register',{'account':address,'password':'Test-only-pass-42','confirmation':'Test-only-pass-42'})
        self.assertEqual(status,200,data)
        return client,address

    def order(self,client,pid=194,quantity=1,coupon=None):
        body={'product_id':pid,'quantity':quantity,'account':'delivery@example.test','coupon_id':coupon,'request_key':secrets.token_hex(16)}
        status,result=client.call('/orders',body)
        self.assertEqual(status,200,result)
        return result,body

    def captcha(self):
        key=secrets.token_hex(20)
        with self._fixture_db() as c:
            c.execute('INSERT INTO challenges(id,purpose,email,answer,expires) VALUES(%s,%s,%s,%s,%s)',(key,'login','',hashlib.sha256(b'QA7K').hexdigest(),time.time()+300))
        return key

    def test_registration_password_hash_and_logout(self):
        client,address=self.user()
        status,me=client.call('/me');self.assertEqual(status,200);self.assertEqual(me['email'],address)
        with self._fixture_db() as c:stored=c.execute('SELECT password FROM users WHERE email=%s',(address,)).fetchone()[0]
        self.assertNotIn('Test-only-pass-42',stored)
        self.assertTrue(any(c.name=='piusgo_session' and 'HttpOnly' in c._rest for c in client.cookies))
        self.assertEqual(client.call('/auth/logout',{})[0],200)
        self.assertEqual(client.call('/me')[0],401)

    def test_captcha_login_and_single_use(self):
        client,address=self.user();client.call('/auth/logout',{})
        status,png=client.call('/auth/captcha');self.assertEqual(status,200);self.assertTrue(png['image'].startswith('data:image/png;base64,'));self.assertNotIn('answer',png)
        key=self.captcha();body={'account':address,'password':'Test-only-pass-42','challenge_id':key,'code':'WRNG'}
        self.assertEqual(client.call('/auth/login',body)[0],400)
        body['code']='QA7K';self.assertEqual(client.call('/auth/login',body)[0],200)
        client.call('/auth/logout',{});self.assertEqual(client.call('/auth/login',body)[0],400)

    def test_reset_revokes_existing_sessions(self):
        client,address=self.user();status,code=client.call('/auth/code',{'email':address,'purpose':'reset'});self.assertEqual(status,200)
        status,data=client.call('/auth/reset',{'account':address,'password':'New-test-pass-84','confirmation':'New-test-pass-84','challenge_id':code['id'],'code':code['mock_code']})
        self.assertEqual(status,200,data);self.assertEqual(client.call('/me')[0],401)
        self.assertEqual(client.call('/auth/login',{'account':address,'password':'New-test-pass-84','challenge_id':self.captcha(),'code':'QA7K'})[0],200)

    def test_email_code_wrong_attempts_and_throttle(self):
        client=Client(self.base);address='rate-'+secrets.token_hex(5)+'@example.test'
        _,code=client.call('/auth/code',{'email':address,'purpose':'reset'})
        self.assertEqual(client.call('/auth/code',{'email':address,'purpose':'reset'})[0],429)
        payload={'account':address,'password':'Test-only-pass-42','confirmation':'Test-only-pass-42','challenge_id':code['id'],'code':'000000'}
        for _ in range(5):self.assertEqual(client.call('/auth/reset',payload)[0],400)
        payload['code']=code['mock_code'];self.assertEqual(client.call('/auth/reset',payload)[0],400)

    def test_registration_validation_without_verification_code(self):
        client,address=self.user()
        body={'account':address,'password':'Test-only-pass-42','confirmation':'Different-pass-42'}
        self.assertEqual(client.call('/auth/register',body)[0],400)
        body['confirmation']=body['password']
        self.assertEqual(client.call('/auth/register',body)[0],409)
        body['account']='invalid-email'
        self.assertEqual(client.call('/auth/register',body)[0],400)
        body['account']='valid@example.test';del body['confirmation']
        self.assertEqual(client.call('/auth/register',body)[0],422)

    def test_server_price_and_payment_idempotency(self):
        client,_=self.user();before=client.call('/products/194')[1]['stock']
        o,body=self.order(client);self.assertEqual(o['total'],220)
        body['total']=1
        self.assertEqual(client.call('/orders',body)[0],422)
        del body['total']
        status,repeated=client.call('/orders',body);self.assertEqual(status,200);self.assertEqual(repeated['id'],o['id']);self.assertEqual(repeated['total'],220)
        first=client.call('/orders/'+o['id']+'/pay',{'method':'mock'})
        second=client.call('/orders/'+o['id']+'/pay',{'method':'mock'})
        self.assertEqual(first[0],200);self.assertEqual(second[0],200);self.assertEqual(first[1]['delivery'],second[1]['delivery'])
        self.assertEqual(client.call('/products/194')[1]['stock'],before-1)
        self.assertEqual(len(client.call('/orders')[1]['items']),1)

    def test_cross_user_orders_and_tickets_are_blocked(self):
        alice,_=self.user();bob,_=self.user();o,_=self.order(alice)
        self.assertEqual(bob.call('/orders')[1]['items'],[])
        self.assertEqual(bob.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],404)
        self.assertEqual(bob.call('/orders/'+o['id']+'/cancel',{})[0],404)
        body={'title':'订单售后问题','body':'这是一条完整的测试工单内容。','order_id':o['id']}
        self.assertEqual(bob.call('/tickets',body)[0],404)
        self.assertEqual(alice.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
        self.assertEqual(alice.call('/tickets',body)[0],200)
        self.assertEqual(len(alice.call('/tickets')[1]['items']),1);self.assertEqual(bob.call('/tickets')[1]['items'],[])

    def test_coupon_single_claim_and_single_redemption(self):
        client,_=self.user()
        for _ in range(2):self.assertEqual(client.call('/coupons/newcomer',{})[0],200)
        coupons=client.call('/me')[1]['coupons'];self.assertEqual(len(coupons),1)
        first,_=self.order(client,188,coupon=coupons[0]['id']);second,_=self.order(client,188,coupon=coupons[0]['id'])
        self.assertEqual(first['total'],13800)
        self.assertEqual(client.call('/orders/'+first['id']+'/pay',{'method':'mock'})[0],200)
        self.assertEqual(client.call('/orders/'+second['id']+'/pay',{'method':'mock'})[0],400)

    def test_recharge_idempotency_and_balance_payment(self):
        client,_=self.user();o,_=self.order(client)
        self.assertEqual(client.call('/orders/'+o['id']+'/pay',{'method':'balance'})[0],400)
        body={'amount':10000,'request_key':secrets.token_hex(16)}
        for _ in range(2):self.assertEqual(client.call('/recharges',body)[0],200)
        self.assertEqual(client.call('/me')[1]['balance'],10500)
        self.assertEqual(client.call('/orders/'+o['id']+'/pay',{'method':'balance'})[0],200)
        self.assertEqual(client.call('/me')[1]['balance'],10280)
        self.assertEqual(client.call('/recharges',{'amount':-100,'request_key':secrets.token_hex(16)})[0],422)

    def test_lottery_single_entry_and_product_scope(self):
        client,_=self.user()
        first=client.call('/lottery/enter',{})[1];second=client.call('/lottery/enter',{})[1]
        self.assertEqual(first['won'],second['won']);self.assertTrue(second['already_entered'])
        uid=client.call('/me')[1]['id'];cid=secrets.token_hex(12)
        with self._fixture_db() as c:
            c.execute('INSERT INTO coupons VALUES(%s,%s,%s,%s,%s,0)',(cid,uid,'lottery:test',14800,14800))
        status,_=client.call('/orders',{'product_id':194,'quantity':1,'account':'a@example.test','coupon_id':cid,'request_key':secrets.token_hex(16)})
        self.assertEqual(status,400)
        target,_=self.order(client,188,coupon=cid);self.assertEqual(target['total'],0)

    def test_csrf_and_invalid_order_input(self):
        client,_=self.user()
        self.assertEqual(client.call('/recharges',{'amount':10000,'request_key':secrets.token_hex(16)},origin='https://evil.example')[0],403)
        for quantity in [0,-1,1.5,True,11]:
            self.assertEqual(client.call('/orders',{'product_id':194,'quantity':quantity,'account':'a@example.test','request_key':secrets.token_hex(16)})[0],422)

    def test_concurrent_payments_decrement_stock_once(self):
        client,_=self.user();o,_=self.order(client);before=client.call('/products/194')[1]['stock']
        cookie='; '.join(f'{x.name}={x.value}' for x in client.cookies)
        def pay():
            request=urllib.request.Request(self.base+'/api/orders/'+o['id']+'/pay',data=b'{"method":"mock"}',headers={'Cookie':cookie,'Origin':'http://localhost:3000','X-Requested-With':'PiusGo','Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=15) as r:return json.load(r)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:pay(),range(4)))
        self.assertEqual(len({r['delivery'] for r in results}),1)
        self.assertEqual(client.call('/products/194')[1]['stock'],before-1)

    def test_variant_price_email_and_payment_selection(self):
        client,address=self.user()
        variants=client.call('/products/188')[1]['variants']
        self.assertEqual({v['name'] for v in variants},{'菲区官方充值','苹果官方充值'})
        body={'product_id':188,'variant_id':'188-apple','quantity':2,'account':'attacker@example.test','payment_method':'wechat','request_key':secrets.token_hex(16)}
        status,o=client.call('/orders',body);self.assertEqual(status,200,o)
        self.assertEqual(o['total'],31600);self.assertEqual(o['account'],address);self.assertEqual(o['payment_method'],'wechat')
        self.assertEqual(o['variant_name'],'苹果官方充值')
        before=next(v['stock'] for v in variants if v['id']=='188-apple')
        self.assertEqual(client.call('/orders/'+o['id']+'/pay',{'method':'wechat'})[0],200)
        after=client.call('/products/188')[1]['variants']
        self.assertEqual(next(v['stock'] for v in after if v['id']=='188-apple'),before-2)
        body['variant_id']='194-standard';body['request_key']=secrets.token_hex(16)
        self.assertEqual(client.call('/orders',body)[0],400)

    def test_conflicting_idempotency_key(self):
        client,_=self.user();o,body=self.order(client)
        body['quantity']=2
        self.assertEqual(client.call('/orders',body)[0],409)
        r={'amount':10000,'request_key':secrets.token_hex(16)}
        self.assertEqual(client.call('/recharges',r)[0],200)
        r['amount']=30000
        self.assertEqual(client.call('/recharges',r)[0],409)

    def test_variant_stock_failure_rolls_back_coupon_and_balance(self):
        client,_=self.user();client.call('/coupons/newcomer',{})
        cid=client.call('/me')[1]['coupons'][0]['id']
        o,_=self.order(client,188,coupon=cid)
        client.call('/recharges',{'amount':30000,'request_key':secrets.token_hex(16)})
        balance=client.call('/me')[1]['balance']
        with self._fixture_db() as c:
            old=c.execute("SELECT stock FROM variants WHERE id='188-standard'").fetchone()[0]
            c.execute("UPDATE variants SET stock=0 WHERE id='188-standard'")
        try:
            self.assertEqual(client.call('/orders/'+o['id']+'/pay',{'method':'balance'})[0],400)
            me=client.call('/me')[1]
            self.assertEqual(me['balance'],balance);self.assertEqual(me['coupons'][0]['used'],0)
        finally:
            with self._fixture_db() as c:c.execute("UPDATE variants SET stock=%s WHERE id='188-standard'",(old,))

    def test_request_limit_and_validation_redaction(self):
        client=Client(self.base)
        self.assertEqual(client.call('/auth/register',{'account':'x'*70000})[0],413)
        secret='private-password'
        status,result=client.call('/auth/register',{'account':'a@example.test','password':secret,'confirmation':secret,'admin':True})
        self.assertEqual(status,422);self.assertNotIn(secret,json.dumps(result))

    def test_expired_order_is_not_payable(self):
        client,_=self.user();o,_=self.order(client)
        with self._fixture_db() as c:c.execute('UPDATE orders SET created=%s WHERE id=%s',(time.time()-1801,o['id']))
        self.assertEqual(client.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],400)

    def test_postgres_database_constraints_and_runtime_role(self):
        client,_=self.user();uid=client.call('/me')[1]['id']
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self._fixture_db() as c:c.execute('UPDATE users SET balance=-1 WHERE id=%s',(uid,))
        with psycopg.connect(database_url().replace('postgresql+psycopg://','postgresql://',1),options='-c search_path='+self.schema) as c:
            flags=c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
            self.assertEqual(flags,(False,False,False))
            self.assertEqual(c.execute("SELECT has_table_privilege(current_user,'audit_events','DELETE')").fetchone()[0],False)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(database_url().replace('postgresql+psycopg://','postgresql://',1),options='-c search_path='+self.schema) as c:
                c.execute('CREATE TABLE forbidden_table(id integer)')

    def test_concurrent_distinct_orders_cannot_oversell(self):
        client,_=self.user();a,_=self.order(client);b,_=self.order(client)
        with self._fixture_db() as c:
            old=c.execute("SELECT stock FROM variants WHERE id='194-standard'").fetchone()[0]
            product_stock=c.execute('SELECT stock FROM products WHERE id=194').fetchone()[0]
            c.execute("UPDATE variants SET stock=1 WHERE id='194-standard'")
        cookie='; '.join(f'{x.name}={x.value}' for x in client.cookies)
        def pay(oid):
            request=urllib.request.Request(self.base+'/api/orders/'+oid+'/pay',data=b'{"method":"mock"}',headers={'Cookie':cookie,'Origin':'http://localhost:3000','X-Requested-With':'PiusGo','Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(request,timeout=15) as r:return r.status
            except urllib.error.HTTPError as e:return e.code
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(pay,[a['id'],b['id']]))
            self.assertEqual(sorted(statuses),[200,400])
            with self._fixture_db() as c:self.assertEqual(c.execute("SELECT stock FROM variants WHERE id='194-standard'").fetchone()[0],0)
        finally:
            with self._fixture_db() as c:
                c.execute("UPDATE variants SET stock=%s WHERE id='194-standard'",(old,))
                c.execute('UPDATE products SET stock=%s WHERE id=194',(product_stock,))

    def test_concurrent_order_creation_idempotency(self):
        client,_=self.user();cookie='; '.join(f'{x.name}={x.value}' for x in client.cookies)
        body=json.dumps({'product_id':194,'quantity':1,'request_key':secrets.token_hex(16)}).encode()
        def create(_):
            request=urllib.request.Request(self.base+'/api/orders',data=body,headers={'Cookie':cookie,'Origin':'http://localhost:3000','X-Requested-With':'PiusGo','Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=15) as r:return json.load(r)['id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:ids=list(pool.map(create,range(4)))
        self.assertEqual(len(set(ids)),1)

    def guest_order(self,client=None,quantity=1):
        client=client or Client(self.base)
        self.assertEqual(client.call('/checkout/session')[0],200)
        body={'product_id':194,'quantity':quantity,'account':secrets.token_hex(4)+'@example.test','payment_method':'alipay','request_key':secrets.token_hex(16)}
        status,o=client.call('/orders',body);self.assertEqual(status,200,o)
        return client,o,body

    def test_guest_order_lookup_and_member_isolation(self):
        guest,o,body=self.guest_order()
        self.assertEqual(o['kind'],'guest');self.assertEqual(len(o['id']),50)
        self.assertEqual(guest.call('/orders')[0],401)
        other=Client(self.base)
        status,found=other.call('/orders/lookup',{'order_id':o['id']})
        self.assertEqual(status,200);self.assertIn('***',found['account']);self.assertNotIn('guest_key',found)
        self.assertEqual(other.call('/orders/lookup',{'order_id':'PG'+'0'*48})[0],404)
        member,_=self.user();private,_=self.order(member)
        self.assertEqual(guest.call('/orders/lookup',{'order_id':private['id']})[0],404)
        self.assertEqual(guest.call('/orders/'+private['id']+'/status')[0],404)
        body['request_key']=secrets.token_hex(16);body['payment_method']='balance'
        self.assertEqual(guest.call('/orders',body)[0],401)
        body['payment_method']='alipay';body['account']='not-email'
        self.assertEqual(guest.call('/orders',body)[0],400)

    def run_mail_worker(self,mode='mock'):
        env={**os.environ,'DATABASE_URL':database_url(),'DATABASE_SCHEMA':self.schema,'APP_ENV':'test','MAIL_MODE':mode,'MAIL_OUTBOX_DIR':str(Path(self.temp.name)/'mail')}
        completed=subprocess.run([sys.executable,'-c','from backend.mail_delivery import send_one;\nwhile send_one(): pass'],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,timeout=30)
        self.assertEqual(completed.returncode,0,completed.stderr.decode()[-1000:])

    def test_guest_payment_polling_and_mail_delivery_once(self):
        guest,o,_=self.guest_order(quantity=3)
        status,session=guest.call('/orders/'+o['id']+'/payment-session',{})
        self.assertEqual(status,200);self.assertTrue(session['mock']);self.assertTrue(session['qr_image'].startswith('data:image/png;base64,'))
        for _ in range(2):self.assertEqual(guest.call('/orders/'+o['id']+'/pay',{'method':'alipay'})[0],200)
        status,paid=guest.call('/orders/'+o['id']+'/status')
        self.assertEqual(status,200);self.assertEqual(paid['status'],'paid');self.assertEqual(len(paid['delivery'].splitlines()),3)
        self.assertTrue(paid['usage_guide'])
        with self._fixture_db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM mail_outbox WHERE order_id=%s',(o['id'],)).fetchone()[0],1)
            mid=c.execute('SELECT id FROM mail_outbox WHERE order_id=%s',(o['id'],)).fetchone()[0]
        self.run_mail_worker()
        self.assertEqual(guest.call('/orders/'+o['id']+'/status')[1]['email_status'],'mock_delivered')
        path=Path(self.temp.name)/'mail'/(mid+'.eml');self.assertTrue(path.exists())
        before=path.stat().st_mtime_ns;self.run_mail_worker();self.assertEqual(path.stat().st_mtime_ns,before)
        self.assertEqual(guest.call('/orders/'+o['id']+'/email')[0],200)

    def test_mail_retry_does_not_roll_back_payment(self):
        self.run_mail_worker()
        guest,o,_=self.guest_order();guest.call('/orders/'+o['id']+'/pay',{'method':'alipay'})
        self.run_mail_worker('invalid-test-mode')
        with self._fixture_db() as c:
            row=c.execute('SELECT status,attempts FROM mail_outbox WHERE order_id=%s',(o['id'],)).fetchone()
            self.assertEqual(row,('queued',1))
            c.execute('UPDATE mail_outbox SET next_attempt=0 WHERE order_id=%s',(o['id'],))
        self.assertEqual(guest.call('/orders/'+o['id']+'/status')[1]['status'],'paid')
        self.run_mail_worker();self.assertEqual(guest.call('/orders/'+o['id']+'/status')[1]['email_status'],'mock_delivered')

    def test_tickets_search_pagination_priority_and_guest_scope(self):
        guest=Client(self.base);ids=[]
        for i in range(5):
            guest,o,_=self.guest_order(guest);guest.call('/orders/'+o['id']+'/pay',{'method':'alipay'})
            body={'order_id':o['id'],'priority':'high' if i==0 else 'medium','title':'搜索测试标题'+str(i),'body':'这是用于验证工单搜索和分页的完整问题描述。'}
            status,t=guest.call('/tickets',body);self.assertEqual(status,200,t);ids.append(t['id'])
            if i==0:self.assertEqual(guest.call('/tickets',body)[0],409)
        status,page=guest.call('/tickets?page=2&page_size=2');self.assertEqual(status,200)
        self.assertEqual(page['total'],5);self.assertEqual(len(page['items']),2)
        self.assertEqual(guest.call('/tickets?q='+ids[0])[1]['total'],1)
        self.assertEqual(guest.call('/tickets?q='+ids[0])[1]['items'][0]['priority'],'high')
        self.assertEqual(guest.call('/tickets?q=%25')[1]['total'],0)
        self.assertEqual(Client(self.base).call('/tickets')[1]['total'],0)
        self.assertEqual(guest.call('/tickets?page_size=10000')[0],422)
        _,pending,_=self.guest_order(guest)
        self.assertEqual(guest.call('/tickets',{'order_id':pending['id'],'title':'未支付订单售后','body':'这是未支付订单的工单测试内容。','priority':'low'})[0],400)

    def test_guest_ticket_survives_login_in_same_browser(self):
        guest,o,_=self.guest_order();guest.call('/orders/'+o['id']+'/pay',{'method':'alipay'})
        status,t=guest.call('/tickets',{'order_id':o['id'],'title':'游客订单问题','body':'这是验证游客登录后工单仍可见的问题描述。','priority':'low'})
        self.assertEqual(status,200)
        password='Only-test-password-52'
        self.assertEqual(guest.call('/auth/register',{'account':secrets.token_hex(6)+'@example.test','password':password,'confirmation':password})[0],200)
        self.assertEqual(guest.call('/tickets')[1]['items'][0]['id'],t['id'])
        self.assertEqual(guest.call('/orders')[1]['items'],[])

    def test_expired_guest_order_lookup_closes_order(self):
        guest,o,_=self.guest_order()
        with self._fixture_db() as c:c.execute('UPDATE orders SET created=%s WHERE id=%s',(time.time()-1900,o['id']))
        self.assertEqual(guest.call('/orders/lookup',{'order_id':o['id']})[1]['status'],'cancelled')
        self.assertEqual(guest.call('/orders/'+o['id']+'/payment-session',{})[0],400)
        self.assertEqual(guest.call('/orders/'+o['id']+'/pay',{'method':'alipay'})[0],400)

    def test_cancelled_order_cannot_be_paid(self):
        client,_=self.user();o,_=self.order(client)
        self.assertEqual(client.call('/orders/'+o['id']+'/cancel',{})[0],200)
        self.assertEqual(client.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],400)


if __name__=='__main__':unittest.main()
