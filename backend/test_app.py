"""HTTP integration tests against an isolated PostgreSQL database, never user data."""
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
        env={**os.environ,'DATABASE_URL':database_url(),'DATABASE_SCHEMA':cls.schema,'MOCK_MODE':'true','APP_ENV':'test','MAIL_MODE':'mock','MAIL_OUTBOX_DIR':str(Path(cls.temp.name)/'mail'),'SUPPORT_ATTACHMENT_DIR':str(Path(cls.temp.name)/'support-images'),'PRODUCT_IMAGE_DIR':str(Path(cls.temp.name)/'product-images')}
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
        self.assertEqual(client.call('/lottery/enter',{})[0],410)
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

    def test_profile_update_and_privilege_boundaries(self):
        client,address=self.user()
        self.assertEqual(client.call('/me/profile',{'display_name':'测试昵称','phone':'+86 13800138000'})[0],200)
        me=client.call('/me')[1]
        self.assertEqual(me['display_name'],'测试昵称');self.assertEqual(me['email'],address)
        self.assertEqual(client.call('/me/profile',{'display_name':'非法邮箱修改','email':'other@example.test'})[0],422)
        self.assertEqual(client.call('/me/profile',{'display_name':'   ','phone':''})[0],400)
        other,_=self.user();self.assertNotEqual(other.call('/me')[1]['display_name'],'测试昵称')
        self.assertEqual(Client(self.base).call('/me/profile',{'display_name':'匿名修改'})[0],401)

    def test_change_password_requires_current_password_and_revokes_sessions(self):
        client,address=self.user();other=Client(self.base)
        self.assertEqual(other.call('/auth/login',{'account':address,'password':'Test-only-pass-42','challenge_id':self.captcha(),'code':'QA7K'})[0],200)
        body={'current_password':'wrong-password','new_password':'New-account-pass-93','confirmation':'New-account-pass-93'}
        self.assertEqual(client.call('/me/password',body)[0],400)
        body['current_password']='Test-only-pass-42';body['confirmation']='mismatch-pass-83'
        self.assertEqual(client.call('/me/password',body)[0],400)
        body['confirmation']=body['new_password']
        self.assertEqual(client.call('/me/password',body)[0],200)
        self.assertEqual(client.call('/me')[0],401);self.assertEqual(other.call('/me')[0],401)
        self.assertEqual(client.call('/auth/login',{'account':address,'password':'Test-only-pass-42','challenge_id':self.captcha(),'code':'QA7K'})[0],401)
        self.assertEqual(client.call('/auth/login',{'account':address,'password':body['new_password'],'challenge_id':self.captcha(),'code':'QA7K'})[0],200)

    def test_login_metadata_and_custom_recharge_method(self):
        client,address=self.user();first=client.call('/me')[1]
        self.assertIsNotNone(first['last_login_at']);self.assertEqual(first['last_login_ip'],'127.0.0.1')
        client.call('/auth/logout',{})
        client.call('/auth/login',{'account':address,'password':'Test-only-pass-42','challenge_id':self.captcha(),'code':'QA7K'})
        current=client.call('/me')[1];self.assertEqual(current['previous_login_at'],first['last_login_at'])
        body={'amount':12345,'payment_method':'wechat','request_key':secrets.token_hex(16)}
        status,r=client.call('/recharges',body);self.assertEqual(status,200,r)
        self.assertEqual(r['bonus'],500);self.assertEqual(r['payment_method'],'wechat')
        self.assertEqual(client.call('/me')[1]['balance'],12845)
        self.assertEqual(client.call('/recharges',body)[0],200);self.assertEqual(client.call('/me')[1]['balance'],12845)
        body['payment_method']='alipay';self.assertEqual(client.call('/recharges',body)[0],409)
        for value in [999,1000001,123.45,True]:
            self.assertEqual(client.call('/recharges',{'amount':value,'request_key':secrets.token_hex(16)})[0],422)
        self.assertEqual(client.call('/recharges',{'amount':10000,'bonus':999999,'request_key':secrets.token_hex(16)})[0],422)

    def test_cancelled_order_cannot_be_paid(self):
        client,_=self.user();o,_=self.order(client)
        self.assertEqual(client.call('/orders/'+o['id']+'/cancel',{})[0],200)
        self.assertEqual(client.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],400)

    def admin_user(self):
        client,address=self.user()
        with self._fixture_db() as c:c.execute("UPDATE users SET role='admin' WHERE email=%s",(address,))
        return client,address

    def test_admin_authorization_and_no_secret_leaks(self):
        ordinary,_=self.user();admin,_=self.admin_user()
        for resource in ['users','orders','products','tickets','coupons','ledger','lottery','mail','audit','pages']:
            path='/admin/list/'+resource
            self.assertEqual(Client(self.base).call(path)[0],401)
            self.assertEqual(ordinary.call(path)[0],403)
            status,data=admin.call(path);self.assertEqual(status,200,data)
            for row in data['items']:
                self.assertFalse({'password','token','guest_key','request_key','delivery'} & row.keys())
        for path in ['/admin/overview','/admin/detail/users/fake','/admin/catalog-options']:
            self.assertEqual(ordinary.call(path)[0],403)
        for path in ['/admin/users/fake','/admin/products/188','/admin/tickets/fake','/admin/pages/tutorial','/admin/actions/orders/fake']:
            self.assertEqual(ordinary.call(path,{})[0],403)
        self.assertEqual(admin.call('/admin/list/users',origin='https://evil.example')[0],200)
        self.assertEqual(admin.call('/admin/users/fake',{},origin='https://evil.example')[0],403)
        self.assertEqual(ordinary.call('/auth/register',{'account':'no@example.test','password':'Test-only-pass-42','confirmation':'Test-only-pass-42','role':'admin'})[0],422)

    def test_admin_disable_user_and_self_protection(self):
        admin,_=self.admin_user();user,_=self.user();uid=user.call('/me')[1]['id']
        body={'display_name':'后台编辑','phone':'12345','disabled':1}
        self.assertEqual(admin.call('/admin/users/'+uid,body)[0],200)
        self.assertEqual(user.call('/me')[0],401)
        status,detail=admin.call('/admin/detail/users/'+uid)
        self.assertEqual(status,200);self.assertEqual(detail['display_name'],'后台编辑');self.assertNotIn('password',detail)
        aid=admin.call('/admin/session')[1]['id']
        self.assertEqual(admin.call('/admin/users/'+aid,body)[0],400)
        body['disabled']=0
        self.assertEqual(admin.call('/admin/users/'+uid,body)[0],200)
        self.assertEqual(user.call('/me')[0],401)  # enabling does not revive revoked sessions
        self.assertEqual(admin.call('/admin/users/'+uid,dict(body,role='admin'))[0],422)

    def test_admin_product_sync_and_stale_stock_protection(self):
        admin,_=self.admin_user();user,_=self.user()
        status,p=admin.call('/admin/detail/products/194');self.assertEqual(status,200)
        keys=['version','name','category','image','badge','tags','active','variants','product_type','coupon_eligible','price','stock']
        body={k:p[k] for k in keys}
        try:
            body['active']=0
            self.assertEqual(admin.call('/admin/products/194',body)[0],200)
            self.assertEqual(user.call('/products/194')[0],404)
            self.assertEqual(user.call('/orders',{'product_id':194,'quantity':1,'account':'x@example.test','request_key':secrets.token_hex(16)})[0],400)
            body={k:admin.call('/admin/detail/products/194')[1][k] for k in keys}
            body['active']=1;body['variants'][0]['price']=4321;body['price']=4321
            self.assertEqual(admin.call('/admin/products/194',body)[0],200)
            self.assertEqual(user.call('/products/194')[1]['price'],4321)
            fresh=admin.call('/admin/detail/products/194')[1];stale={k:fresh[k] for k in keys}
            o,_=self.order(user)
            self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
            self.assertEqual(admin.call('/admin/products/194',stale)[0],409)
            self.assertEqual(user.call('/products/194')[1]['stock'],fresh['stock']-1)
        finally:
            with self._fixture_db() as c:
                c.execute('UPDATE products SET active=1,price=%s,stock=%s WHERE id=194',(p['price'],p['stock']))
                for v in p['variants']:c.execute('UPDATE variants SET price=%s,stock=%s WHERE id=%s',(v['price'],v['stock'],v['id']))

    def test_admin_ticket_reply_visible_only_to_owner(self):
        admin,_=self.admin_user();user,_=self.user();other,_=self.user();o,_=self.order(user)
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
        status,t=user.call('/tickets',{'order_id':o['id'],'title':'后台售后测试','body':'需要帮助处理这个订单的使用问题','priority':'medium'})
        self.assertEqual(status,200,t)
        tid=user.call('/tickets')[1]['items'][0]['id']
        self.assertEqual(admin.call('/admin/tickets/'+tid,{'status':'resolved','priority':'high','reply':'已为您处理，请查看订单。'})[0],200)
        reply=user.call('/tickets')[1]['items'][0]
        self.assertEqual(reply['reply'],'已为您处理，请查看订单。');self.assertEqual(reply['status'],'resolved');self.assertIsNotNone(reply['replied_at'])
        self.assertNotIn(tid,[x['id'] for x in other.call('/tickets')[1]['items']])

    def test_admin_order_actions_and_audit(self):
        admin,_=self.admin_user();user,_=self.user();o,_=self.order(user)
        path='/admin/actions/orders/'+o['id']
        self.assertEqual(admin.call(path,{'action':'cancel'})[0],200)
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],400)
        self.assertEqual(admin.call(path,{'action':'cancel'})[0],409)
        self.assertEqual(admin.call(path,{'action':'retry'})[0],400)
        logs=admin.call('/admin/list/audit?q='+o['id'])[1]['items']
        self.assertTrue(any(x['event']=='admin_orders_cancel' for x in logs))
        self.assertEqual(admin.call('/admin/list/orders?page_size=1')[1]['page_size'],1)
        self.assertEqual(admin.call('/admin/list/orders?page=0')[0],422)
        self.assertEqual(admin.call('/admin/list/users?q=%27%20OR%201=1--')[1]['total'],0)

    def test_admin_overview_totals_and_trend(self):
        admin,_=self.admin_user();user,_=self.user()
        status,before=admin.call('/admin/overview');self.assertEqual(status,200,before)
        o,_=self.order(user)
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
        status,after=admin.call('/admin/overview');self.assertEqual(status,200,after)
        self.assertEqual(after['counts']['revenue'],before['counts']['revenue']+o['total'])
        self.assertEqual(after['counts']['orders'],before['counts']['orders']+1)
        self.assertEqual(after['counts']['paid'],before['counts']['paid']+1)
        self.assertTrue(after['trend']);self.assertIn('day',after['trend'][-1])

    def test_admin_pages_sync(self):
        admin,_=self.admin_user()
        old=admin.call('/pages/announcements')[1]
        try:
            body={'title':'测试公告','intro':'后台发布的说明','sections':[{'title':'更新','text':'这里是新的公告正文。'}]}
            self.assertEqual(admin.call('/admin/pages/announcements',body)[0],200)
            public=Client(self.base).call('/pages/announcements')[1]
            self.assertEqual(public['title'],'测试公告');self.assertEqual(public['sections'],body['sections'])
            self.assertEqual(admin.call('/admin/pages/announcements',dict(body,sections=[]))[0],422)
        finally:
            admin.call('/admin/pages/announcements',{k:old[k] for k in ['title','intro','sections']})

    def test_admin_mail_retry_and_coupon_revocation(self):
        admin,_=self.admin_user();user,_=self.user();o,_=self.order(user)
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
        with self._fixture_db() as c:
            c.execute("UPDATE mail_outbox SET status='failed',attempts=5 WHERE order_id=%s",(o['id'],))
            mid=c.execute('SELECT id FROM mail_outbox WHERE order_id=%s',(o['id'],)).fetchone()[0]
            uid=user.call('/me')[1]['id'];cid=secrets.token_hex(16)
            c.execute("INSERT INTO coupons(id,user_id,kind,amount,minimum) VALUES(%s,%s,%s,100,100)",(cid,uid,'admin-test-'+cid))
        self.assertEqual(admin.call('/admin/actions/mail/'+mid,{'action':'retry'})[0],200)
        self.assertEqual(admin.call('/admin/detail/mail/'+mid)[1]['status'],'queued')
        self.assertEqual(admin.call('/admin/actions/mail/'+mid,{'action':'retry'})[0],409)
        self.assertEqual(admin.call('/admin/actions/coupons/'+cid,{'action':'revoke'})[0],200)
        self.assertEqual(admin.call('/admin/actions/coupons/'+cid,{'action':'revoke'})[0],409)


    def new_catalog_product(self,admin,**changes):
        body={'request_key':secrets.token_hex(16),'product_type':'promotion','name':'上架测试 '+secrets.token_hex(4),'category':admin.call('/admin/catalog-options')[1]['categories'][0]['id'],'image':'/assets/brand-0.png','badge':'新品','tags':['自动发货'],'active':1,'coupon_eligible':1,'price':10000,'stock':5,'variants':[{'name':'月卡','price':10000,'stock':2},{'name':'年卡','price':20000,'stock':3}]}
        body.update(changes)
        status,result=admin.call('/admin/products',body);self.assertEqual(status,200,result)
        return result['id'],body

    def catalog_edit(self,admin,pid):
        p=admin.call('/admin/detail/products/'+str(pid))[1]
        return {k:p[k] for k in ['version','product_type','name','category','image','badge','tags','active','coupon_eligible','price','stock','variants']}

    def test_publish_product_and_real_checkout(self):
        admin,_=self.admin_user();user,_=self.user();pid,body=self.new_catalog_product(admin)
        self.assertEqual(user.call('/admin/products',body)[0],403)
        self.assertEqual(Client(self.base).call('/admin/products',body)[0],401)
        self.assertEqual(admin.call('/admin/products',body)[1]['id'],pid)
        self.assertEqual(admin.call('/admin/products',dict(body,name='Different'))[0],409)
        p=user.call('/products/'+str(pid))[1]
        self.assertIn(pid,[x['id'] for x in user.call('/products')[1]['items']])
        self.assertEqual(p['price'],10000);self.assertEqual(p['stock'],5);self.assertEqual(p['variant_count'],2)
        self.assertNotIn('creation_key',p);self.assertNotIn('creation_hash',p)
        status,o=user.call('/orders',{'product_id':pid,'variant_id':p['variants'][1]['id'],'quantity':1,'account':'x@example.test','request_key':secrets.token_hex(16)})
        self.assertEqual(status,200,o);self.assertEqual(o['total'],20000)
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
        self.assertEqual(user.call('/products/'+str(pid))[1]['stock'],4)
        edit=self.catalog_edit(admin,pid);edit['active']=0
        self.assertEqual(admin.call('/admin/products/'+str(pid),edit)[0],200)
        self.assertEqual(user.call('/products/'+str(pid))[0],404)
        self.assertNotIn(pid,[x['id'] for x in user.call('/products')[1]['items']])

    def test_catalog_coupon_switch_and_spec_removal(self):
        admin,_=self.admin_user();user,_=self.user();pid,_=self.new_catalog_product(admin,coupon_eligible=0)
        uid=user.call('/me')[1]['id'];cid=secrets.token_hex(16)
        with self._fixture_db() as c:c.execute("INSERT INTO coupons(id,user_id,kind,amount,minimum) VALUES(%s,%s,%s,1000,10000)",(cid,uid,'test-'+cid))
        p=user.call('/products/'+str(pid))[1]
        order={'product_id':pid,'variant_id':p['variants'][0]['id'],'quantity':1,'coupon_id':cid,'request_key':secrets.token_hex(16)}
        self.assertEqual(user.call('/orders',order)[0],400)
        edit=self.catalog_edit(admin,pid);edit['coupon_eligible']=1
        self.assertEqual(admin.call('/admin/products/'+str(pid),edit)[0],200)
        status,o=user.call('/orders',order);self.assertEqual(status,200,o);self.assertEqual(o['total'],9000)
        edit=self.catalog_edit(admin,pid)
        edit['variants']=[v for v in edit['variants'] if v['id']!=order['variant_id']];edit['stock']=sum(v['stock'] for v in edit['variants']);edit['price']=min(v['price'] for v in edit['variants'])
        self.assertEqual(admin.call('/admin/products/'+str(pid),edit)[0],409)
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
        edit=self.catalog_edit(admin,pid);edit['variants']=[v for v in edit['variants'] if v['id']!=order['variant_id']]
        edit['variants'].append({'name':'季卡','price':15000,'stock':4});edit['stock']=sum(v['stock'] for v in edit['variants']);edit['price']=min(v['price'] for v in edit['variants'])
        self.assertEqual(admin.call('/admin/products/'+str(pid),edit)[0],200)
        p=user.call('/products/'+str(pid))[1]
        self.assertEqual(p['stock'],7);self.assertEqual(p['price'],15000)
        self.assertEqual(len(p['variants']),2)
        self.assertEqual(user.call('/orders/'+o['id']+'/status')[1]['variant_name'],'月卡')
        order['coupon_id']=None;order['request_key']=secrets.token_hex(16)
        self.assertEqual(user.call('/orders',order)[0],400)

    def test_catalog_input_validation_and_images(self):
        import base64,io
        from PIL import Image
        admin,_=self.admin_user();user,_=self.user();_,body=self.new_catalog_product(admin)
        for changes in [{'price':9999},{'stock':999},{'product_type':'unknown'},{'variants':[]},{'variants':[body['variants'][0],body['variants'][0]]},{'coupon_eligible':2},{'image':'javascript:alert(1)'},{'tags':['same','same']}]:
            status,_=admin.call('/admin/products',dict(body,request_key=secrets.token_hex(16),**changes));self.assertIn(status,[400,422])
        data=io.BytesIO();Image.new('RGB',(30,30),'green').save(data,format='PNG')
        upload={'content':base64.b64encode(data.getvalue()).decode()}
        self.assertEqual(user.call('/admin/product-image',upload)[0],403)
        status,result=admin.call('/admin/product-image',upload);self.assertEqual(status,200,result)
        with urllib.request.urlopen(self.base+result['url']) as r:self.assertEqual(r.headers['Content-Type'],'image/webp')
        self.assertEqual(admin.call('/admin/product-image',{'content':'not base64'})[0],400)
        self.assertEqual(admin.call('/admin/product-image',{'content':base64.b64encode(b'<svg/>').decode()})[0],400)

    def deterministic_draw(self):
        from contextlib import contextmanager
        from fastapi import FastAPI
        from backend.product_lottery import install_product_lottery
        from backend.database import Connection
        runtime=make_engine(url=database_url(),schema=self.schema)
        @contextmanager
        def isolated_db(write=False):
            with runtime.begin() as connection:yield Connection(connection)
        app=FastAPI();install_product_lottery(app,lambda:None,True)
        endpoint=next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/products/{pid}/lottery' and 'POST' in r.methods)
        return endpoint,isolated_db,runtime

    def test_product_lottery_winner_idempotency_and_privacy(self):
        from unittest.mock import patch
        from backend.product_lottery import DrawInput
        admin,_=self.admin_user();user,_=self.user();other,_=self.user();pid,_=self.new_catalog_product(admin,product_type='lottery')
        p=user.call('/products/'+str(pid))[1];vid=p['variants'][1]['id'];u=user.call('/me')[1]
        self.assertEqual(Client(self.base).call('/products/'+str(pid)+'/lottery',{'variant_id':vid})[0],401)
        self.assertEqual(user.call('/orders',{'product_id':pid,'variant_id':vid,'quantity':1,'request_key':secrets.token_hex(16)})[0],400)
        endpoint,isolated,runtime=self.deterministic_draw()
        try:
            with patch('backend.product_lottery.db',isolated),patch('backend.product_lottery.secrets.randbelow',return_value=0):
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    results=list(pool.map(lambda _:endpoint(pid,DrawInput(variant_id=vid),u),range(4)))
            self.assertEqual(len({r['order_id'] for r in results}),1)
            oid=results[0]['order_id'];self.assertIsNotNone(oid)
            self.assertEqual(sum(not r['already_entered'] for r in results),1)
            status,o=user.call('/orders/'+oid+'/status');self.assertEqual(status,200,o)
            self.assertEqual(o['total'],0);self.assertEqual(o['status'],'paid');self.assertEqual(o['source'],'lottery');self.assertEqual(o['variant_id'],vid);self.assertTrue(o['delivery'])
            self.assertEqual(other.call('/orders/'+oid+'/status')[0],404)
            self.assertEqual(user.call('/products/'+str(pid))[1]['stock'],4)
            self.assertEqual(user.call('/products/'+str(pid)+'/lottery')[1]['participants'],1)
            with self._fixture_db() as c:self.assertEqual(c.execute('SELECT count(*) FROM mail_outbox WHERE order_id=%s',(oid,)).fetchone()[0],1)
            self.assertEqual(user.call('/me')[1]['balance'],0)
        finally:runtime.dispose()

    def test_product_lottery_last_stock_and_delivery_rollback(self):
        from unittest.mock import patch
        from backend.product_lottery import DrawInput
        from fastapi import HTTPException
        admin,_=self.admin_user();a,_=self.user();b,_=self.user()
        pid,_=self.new_catalog_product(admin,product_type='lottery',price=100,stock=1,variants=[{'name':'最后一件','price':100,'stock':1}])
        vid=a.call('/products/'+str(pid))[1]['variants'][0]['id'];ua=a.call('/me')[1];ub=b.call('/me')[1]
        endpoint,isolated,runtime=self.deterministic_draw()
        try:
            with patch('backend.product_lottery.db',isolated),patch('backend.product_lottery.secrets.randbelow',return_value=0),patch('backend.product_lottery.deliver_mock_order',side_effect=RuntimeError('simulated delivery failure')):
                with self.assertRaises(RuntimeError):endpoint(pid,DrawInput(variant_id=vid),ua)
            self.assertEqual(a.call('/products/'+str(pid))[1]['stock'],1)
            self.assertEqual(a.call('/products/'+str(pid)+'/lottery')[1]['participants'],0)
            with patch('backend.product_lottery.db',isolated),patch('backend.product_lottery.secrets.randbelow',return_value=0):
                def attempt(u):
                    try:return endpoint(pid,DrawInput(variant_id=vid),u)
                    except HTTPException as e:return e.status_code
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[ua,ub]))
            self.assertEqual(sum(isinstance(r,dict) and r['won'] for r in results),1);self.assertIn(409,results)
            self.assertEqual(a.call('/products/'+str(pid))[1]['stock'],0)
        finally:runtime.dispose()

    def test_product_lottery_loss_and_unavailable_product(self):
        from unittest.mock import patch
        from backend.product_lottery import DrawInput
        admin,_=self.admin_user();user,_=self.user();pid,_=self.new_catalog_product(admin,product_type='lottery')
        p=user.call('/products/'+str(pid))[1];u=user.call('/me')[1]
        endpoint,isolated,runtime=self.deterministic_draw()
        try:
            with patch('backend.product_lottery.db',isolated),patch('backend.product_lottery.secrets.randbelow',return_value=9):r=endpoint(pid,DrawInput(variant_id=p['variants'][0]['id']),u)
            self.assertFalse(r['won']);self.assertIsNone(r['order_id']);self.assertEqual(user.call('/products/'+str(pid))[1]['stock'],5)
            repeated=user.call('/products/'+str(pid)+'/lottery',{'variant_id':p['variants'][1]['id']})[1]
            self.assertTrue(repeated['already_entered']);self.assertFalse(repeated['won']);self.assertEqual(repeated['variant_id'],p['variants'][0]['id'])
            edit=self.catalog_edit(admin,pid);edit['active']=0;self.assertEqual(admin.call('/admin/products/'+str(pid),edit)[0],200)
            other,_=self.user();self.assertEqual(other.call('/products/'+str(pid)+'/lottery',{'variant_id':p['variants'][0]['id']})[0],404)
        finally:runtime.dispose()


    def test_order_product_snapshot_survives_catalog_rename(self):
        admin,_=self.admin_user();user,_=self.user();pid,body=self.new_catalog_product(admin)
        o,_=self.order(user,pid)
        edit=self.catalog_edit(admin,pid);edit['name']='修改后的商品名称';edit['image']='/assets/brand-1.jpg'
        self.assertEqual(admin.call('/admin/products/'+str(pid),edit)[0],200)
        status,paid=user.call('/orders/'+o['id']+'/pay',{'method':'mock'});self.assertEqual(status,200,paid)
        self.assertEqual(paid['name'],body['name']);self.assertEqual(paid['image'],body['image'])
        listed=next(x for x in user.call('/orders')[1]['items'] if x['id']==o['id'])
        self.assertEqual(listed['name'],body['name'])
        self.assertIn(body['name'],user.call('/orders/'+o['id']+'/email')[1]['body'])

    def test_expired_order_does_not_block_variant_removal(self):
        admin,_=self.admin_user();user,_=self.user();pid,_=self.new_catalog_product(admin)
        o,_=self.order(user,pid)
        with self._fixture_db() as c:c.execute('UPDATE orders SET created=%s WHERE id=%s',(time.time()-1900,o['id']))
        edit=self.catalog_edit(admin,pid);edit['variants']=[v for v in edit['variants'] if v['id']!=o['variant_id']]
        edit['price']=min(v['price'] for v in edit['variants']);edit['stock']=sum(v['stock'] for v in edit['variants'])
        status,result=admin.call('/admin/products/'+str(pid),edit);self.assertEqual(status,200,result)
        with self._fixture_db() as c:self.assertEqual(c.execute('SELECT status FROM orders WHERE id=%s',(o['id'],)).fetchone()[0],'cancelled')

    def test_order_request_key_cannot_change_payment_method(self):
        user,_=self.user();o,body=self.order(user)
        self.assertEqual(user.call('/orders',dict(body,payment_method='alipay'))[0],409)
        self.assertEqual(user.call('/orders',body)[1]['id'],o['id'])
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'alipay'})[0],200)
        self.assertEqual(user.call('/orders',body)[1]['id'],o['id'])

    def test_mail_stale_worker_cannot_overwrite_new_claim(self):
        from unittest.mock import patch
        from backend.mail_delivery import send_one
        user,_=self.user();o,_=self.order(user)
        self.assertEqual(user.call('/orders/'+o['id']+'/pay',{'method':'mock'})[0],200)
        _,isolated,runtime=self.deterministic_draw()
        test=self
        try:
            for failure in [False,True]:
                with self.subTest(failure=failure):
                    with self._fixture_db() as c:
                        c.execute("UPDATE mail_outbox SET status='queued',attempts=0,next_attempt=0,created=-100,locked_at=NULL WHERE order_id=%s",(o['id'],))
                    class NewerWorker:
                        def __init__(self,*args,**kwargs):pass
                        def __enter__(self):return self
                        def __exit__(self,*args):return False
                        def login(self,*args):pass
                        def send_message(self,message):
                            with test._fixture_db() as c:
                                c.execute("UPDATE mail_outbox SET status='sending',attempts=2,locked_at=9999999999,error='new-claim' WHERE order_id=%s",(o['id'],))
                            if failure:raise RuntimeError('old worker failed late')
                    env={'MAIL_MODE':'smtp','SMTP_HOST':'invalid.test','SMTP_USER':'fixture','SMTP_PASSWORD':'fixture','SMTP_FROM':'fixture@example.test','SMTP_PORT':'465'}
                    with patch('backend.mail_delivery.db',isolated),patch.dict(os.environ,env),patch('backend.mail_delivery.smtplib.SMTP_SSL',NewerWorker):self.assertTrue(send_one())
                    with self._fixture_db() as c:
                        row=c.execute('SELECT status,attempts,locked_at,error FROM mail_outbox WHERE order_id=%s',(o['id'],)).fetchone()
                    self.assertEqual(row,('sending',2,9999999999.0,'new-claim'))
        finally:runtime.dispose()

    def test_lottery_order_key_does_not_collide_with_client_key(self):
        from unittest.mock import patch
        from backend.product_lottery import DrawInput,day_key
        admin,_=self.admin_user();user,_=self.user();pid,_=self.new_catalog_product(admin,product_type='lottery')
        p=user.call('/products/'+str(pid))[1];u=user.call('/me')[1]
        status,_=user.call('/orders',{'product_id':194,'quantity':1,'request_key':'lottery:'+day_key(pid)})
        self.assertEqual(status,200)
        endpoint,isolated,runtime=self.deterministic_draw()
        try:
            with patch('backend.product_lottery.db',isolated),patch('backend.product_lottery.secrets.randbelow',return_value=0):r=endpoint(pid,DrawInput(variant_id=p['variants'][0]['id']),u)
            self.assertTrue(r['won']);self.assertIsNotNone(r['order_id'])
        finally:runtime.dispose()


    def test_production_endpoints_fail_closed_without_payment_integration(self):
        user,_=self.user();order,_=self.order(user)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'DATABASE_URL':database_url(),'DATABASE_SCHEMA':self.schema,'APP_ENV':'production','MOCK_MODE':'false','WEB_ORIGIN':'http://localhost:3000'}
        with tempfile.TemporaryFile(mode='w+') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=log)
            try:
                prod=Client(f'http://127.0.0.1:{port}')
                prod.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(user.cookies))
                for _ in range(100):
                    try:
                        if prod.call('/config')[0]==200:break
                    except OSError:time.sleep(.05)
                else:self.fail('Production test server did not start')
                self.assertFalse(prod.call('/config')[1]['mock'])
                self.assertEqual(prod.call('/me')[0],200)
                self.assertEqual(prod.call('/orders/'+order['id']+'/pay',{'method':'mock'})[0],503)
                self.assertEqual(prod.call('/orders/'+order['id']+'/payment-session',{})[0],503)
                self.assertEqual(prod.call('/recharges',{'amount':10000,'request_key':secrets.token_hex(16)})[0],503)
                self.assertEqual(prod.call('/products/194/lottery',{'variant_id':'194-standard'})[0],503)
                self.assertEqual(prod.call('/me')[1]['balance'],0)
                self.assertEqual(user.call('/orders/'+order['id']+'/status')[1]['status'],'pending')
            finally:server.terminate();server.wait(timeout=10)
        bad=subprocess.run([sys.executable,'-c','import backend.app'],env={**env,'MOCK_MODE':'true'},capture_output=True,text=True,timeout=15)
        self.assertNotEqual(bad.returncode,0)
        self.assertIn('MOCK_MODE must be disabled',bad.stderr)


    def support_agent(self):
        client,address=self.user();uid=client.call('/me')[1]['id']
        with self._fixture_db() as c:c.execute("UPDATE users SET role='support' WHERE id=%s",(uid,))
        return client,uid

    def support_start(self,client=None):
        client=client or Client(self.base)
        status,data=client.call('/support/session',{})
        self.assertEqual(status,200,data)
        return client,data['conversation']['id']

    def support_send(self,client,cid,body='你好，这是测试消息',actor='customer',key=None,attachment=None):
        return client.call('/support/conversations/'+cid+'/messages?actor='+actor,{'body':body,'client_id':key or secrets.token_hex(16),'attachment_id':attachment})

    def test_support_identity_and_email_cannot_grant_access(self):
        a,aid=self.support_start();b,bid=self.support_start()
        self.assertNotEqual(aid,bid)
        self.assertEqual(a.call('/support/conversations/'+bid+'/messages')[0],404)
        for client,cid in [(a,aid),(b,bid)]:self.assertEqual(client.call('/support/conversations/'+cid+'/contact',{'email':'same@example.test'})[0],200)
        self.assertEqual(a.call('/support/conversations/'+bid+'/messages')[0],404)
        member,address=self.user();member,mid=self.support_start(member)
        self.assertEqual(a.call('/support/conversations/'+mid+'/messages')[0],404)
        self.assertEqual(a.call('/support/conversations/'+aid+'/messages?actor=agent')[0],403)
        self.assertEqual(member.call('/support/conversations/'+mid+'/contact',{'email':'other@example.test'})[0],400)
        self.assertEqual(a.call('/support/agent/session')[0],403)

    def test_support_concurrent_message_idempotency_and_validation(self):
        client,cid=self.support_start();key=secrets.token_hex(16)
        cookie='; '.join(f'{x.name}={x.value}' for x in client.cookies)
        body=json.dumps({'body':'同一条消息','client_id':key}).encode()
        def send(_):
            req=urllib.request.Request(self.base+'/api/support/conversations/'+cid+'/messages',data=body,headers={'Cookie':cookie,'Origin':'http://localhost:3000','X-Requested-With':'PiusGo','Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(send,range(4)))
        self.assertEqual(len({r['id'] for r in results}),1);self.assertEqual(results[0]['seq'],1)
        self.assertEqual(self.support_send(client,cid,'不同的消息',key=key)[0],409)
        self.assertEqual(self.support_send(client,cid,'   ')[0],400)
        self.assertEqual(self.support_send(client,cid,'a'*4001)[0],422)
        self.assertEqual(client.call('/support/conversations/'+cid+'/read',{'last_seq':999})[0],400)
        self.assertEqual(client.call('/support/conversations/'+cid+'/messages?after=999')[0],400)
        payload={'body':'伪造客服','client_id':secrets.token_hex(16),'sender_role':'agent'}
        self.assertEqual(client.call('/support/conversations/'+cid+'/messages',payload)[0],422)

    def test_support_claim_transfer_and_no_admin_access(self):
        customer,cid=self.support_start();a,auid=self.support_agent();b,buid=self.support_agent()
        def claim(client):return client.call('/support/agent/conversations/'+cid+'/claim',{})[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(claim,[a,b]))
        self.assertEqual(sorted(codes),[200,409])
        winner,loser=(a,b) if codes[0]==200 else (b,a)
        target=buid if winner is a else auid
        self.assertEqual(winner.call('/admin/list/users')[0],403)
        self.assertEqual(loser.call('/support/conversations/'+cid+'/messages?actor=agent')[0],404)
        self.assertEqual(self.support_send(winner,cid,'客服回复',actor='agent')[0],200)
        self.assertEqual(winner.call('/support/agent/conversations/'+cid+'/transfer',{'agent_id':target})[0],200)
        self.assertEqual(self.support_send(winner,cid,'旧客服不能回复',actor='agent')[0],404)
        self.assertEqual(self.support_send(loser,cid,'接手后回复',actor='agent')[0],200)
        self.assertEqual(loser.call('/support/agent/conversations/'+cid+'/close',{})[0],200)
        self.assertEqual(self.support_send(loser,cid,'结束后不能回复',actor='agent')[0],409)
        self.assertEqual(self.support_send(customer,cid,'需要继续咨询')[0],200)
        state=customer.call('/support/conversations/'+cid+'/messages')[1]['conversation']
        self.assertEqual(state['status'],'waiting');self.assertIsNone(state['assigned_to'])

    def test_support_read_receipts_and_presence_expiry(self):
        client,cid=self.support_start();agent,uid=self.support_agent()
        with self._fixture_db() as c:c.execute('UPDATE support_presence SET expires=0')
        self.assertFalse(client.call('/support/conversations/'+cid+'/messages')[1]['conversation']['online'])
        self.assertEqual(agent.call('/support/agent/presence',{'online':True})[0],200)
        self.assertTrue(client.call('/support/conversations/'+cid+'/messages')[1]['conversation']['online'])
        agent.call('/support/agent/conversations/'+cid+'/claim',{})
        for i in range(2):self.assertEqual(self.support_send(client,cid,'问题'+str(i))[0],200)
        for seq in [2,1]:self.assertEqual(agent.call('/support/conversations/'+cid+'/read?actor=agent',{'last_seq':seq})[0],200)
        self.assertEqual(client.call('/support/conversations/'+cid+'/messages')[1]['conversation']['other_read_seq'],2)
        self.support_send(agent,cid,'收到，我来处理',actor='agent')
        self.assertEqual(client.call('/support/conversations/'+cid+'/messages')[1]['conversation']['unread'],1)
        self.assertEqual(client.call('/support/unread')[1]['unread'],1)
        client.call('/support/conversations/'+cid+'/read',{'last_seq':3})
        self.assertEqual(client.call('/support/conversations/'+cid+'/messages')[1]['conversation']['unread'],0)
        self.assertEqual(client.call('/support/unread')[1]['unread'],0)
        with self._fixture_db() as c:c.execute('UPDATE support_presence SET expires=0 WHERE user_id=%s',(uid,))
        self.assertFalse(client.call('/support/conversations/'+cid+'/messages')[1]['conversation']['online'])

    def test_support_private_attachments_and_validation(self):
        import io,base64
        from PIL import Image
        client,cid=self.support_start();other,other_id=self.support_start()
        raw=io.BytesIO();Image.new('RGB',(10,10),'white').save(raw,'PNG');encoded=base64.b64encode(raw.getvalue()).decode()
        status,uploaded=client.call('/support/attachments',{'conversation_id':cid,'data':encoded})
        self.assertEqual(status,200,uploaded);aid=uploaded['id']
        with client.opener.open(self.base+'/api/support/attachments/'+aid) as r:
            self.assertEqual(r.headers['Content-Type'],'image/webp');Image.open(io.BytesIO(r.read())).verify()
        self.assertEqual(other.call('/support/attachments/'+aid)[0],404)
        self.assertEqual(self.support_send(other,other_id,attachment=aid)[0],400)
        self.assertEqual(self.support_send(client,cid,attachment=aid)[0],200)
        self.assertEqual(self.support_send(client,cid,attachment=aid)[0],400)
        bad=base64.b64encode(b'<svg onload="alert(1)"></svg>').decode()
        self.assertEqual(client.call('/support/attachments',{'conversation_id':cid,'data':bad})[0],400)
        self.assertEqual(client.call('/support/attachments',{'conversation_id':other_id,'data':encoded})[0],404)
        self.assertEqual(client.call('/support/attachments',{'conversation_id':cid,'data':'a'*1400001})[0],422)

    def test_support_order_binding_and_ticket_conversion(self):
        user,_=self.user();user,cid=self.support_start(user);other,_=self.user();private,_=self.order(other)
        self.assertEqual(user.call('/support/conversations/'+cid+'/order',{'order_id':private['id']})[0],404)
        own,_=self.order(user);self.assertEqual(user.call('/support/conversations/'+cid+'/order',{'order_id':own['id']})[0],200)
        agent,_=self.support_agent();agent.call('/support/agent/conversations/'+cid+'/claim',{})
        body={'title':'在线咨询转售后','body':'请协助处理此订单的问题，这是客服整理的具体情况。','priority':'high'}
        self.assertEqual(agent.call('/support/agent/conversations/'+cid+'/ticket',body)[0],400)
        user.call('/orders/'+own['id']+'/pay',{'method':'mock'})
        status,ticket=agent.call('/support/agent/conversations/'+cid+'/ticket',body);self.assertEqual(status,200,ticket)
        self.assertEqual(agent.call('/support/agent/conversations/'+cid+'/ticket',body)[1]['id'],ticket['id'])
        self.assertEqual(user.call('/tickets')[1]['items'][0]['id'],ticket['id'])
        order=agent.call('/support/conversations/'+cid+'/messages?actor=agent')[1]['conversation']['order']
        self.assertNotIn('delivery',order);self.assertNotIn('account',order);self.assertNotIn('balance',order)
        self.assertEqual(other.call('/tickets')[1]['total'],0)

    def test_support_sse_replay_and_logout_revocation(self):
        user,_=self.user();user,cid=self.support_start(user)
        self.support_send(user,cid,'first');self.support_send(user,cid,'second')
        request=urllib.request.Request(self.base+'/api/support/conversations/'+cid+'/events?after=0',headers={'Last-Event-ID':'1'})
        with user.opener.open(request,timeout=10) as stream:
            lines=[]
            for _ in range(8):
                line=stream.readline().decode();lines.append(line)
                if line.startswith('data:') and 'second' in line:break
            self.assertIn('id: 2', ''.join(lines));self.assertNotIn('first',''.join(lines))
            user.call('/auth/logout',{})
            revoked=False
            for _ in range(15):
                line=stream.readline().decode()
                if 'event: revoked' in line:revoked=True;break
                if not line:break
            self.assertTrue(revoked)

    def test_support_sse_connection_limit(self):
        client,cid=self.support_start();streams=[]
        try:
            for _ in range(2):
                stream=client.opener.open(self.base+'/api/support/conversations/'+cid+'/events',timeout=10);streams.append(stream);stream.readline()
            self.assertEqual(client.call('/support/conversations/'+cid+'/events')[0],429)
        finally:
            for stream in streams:stream.close()

    def test_support_replay_from_another_backend_process(self):
        client,cid=self.support_start();self.support_send(client,cid,'跨进程恢复消息')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'DATABASE_URL':database_url(),'DATABASE_SCHEMA':self.schema,'APP_ENV':'test','MOCK_MODE':'true','SUPPORT_ATTACHMENT_DIR':str(Path(self.temp.name)/'support-images')}
        with tempfile.TemporaryFile(mode='w+') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=log)
            try:
                fresh=Client(f'http://127.0.0.1:{port}');fresh.opener=client.opener
                for _ in range(100):
                    try:
                        if fresh.call('/config')[0]==200:break
                    except OSError:time.sleep(.05)
                self.assertEqual(fresh.call('/support/conversations/'+cid+'/messages')[1]['messages'][0]['body'],'跨进程恢复消息')
            finally:server.terminate();server.wait(timeout=10)

    def test_support_faq_edit_is_admin_only(self):
        agent,_=self.support_agent();admin,_=self.admin_user()
        body={'category':'订单问题','question':'如何查看状态？','answer':'请前往订单查询页面。'}
        self.assertEqual(agent.call('/support/faqs/1',body)[0],403)
        self.assertEqual(admin.call('/support/faqs/1',body)[0],200)
        self.assertEqual(Client(self.base).call('/support/faqs')[1]['items'][0]['answer'],body['answer'])

    def test_support_login_switch_cannot_reuse_guest_conversation(self):
        guest,gid=self.support_start();self.support_send(guest,gid,'游客私有消息')
        password='Support-test-pass-42'
        status,_=guest.call('/auth/register',{'account':secrets.token_hex(6)+'@example.test','password':password,'confirmation':password})
        self.assertEqual(status,200)
        _,member_id=self.support_start(guest);self.assertNotEqual(member_id,gid)
        self.assertEqual(guest.call('/support/conversations/'+gid+'/messages')[0],404)
        self.support_send(guest,member_id,'会员私有消息')
        guest.call('/auth/logout',{})
        self.assertEqual(guest.call('/support/conversations/'+member_id+'/messages')[0],404)
        self.assertEqual(guest.call('/support/conversations/'+gid+'/messages')[1]['messages'][0]['body'],'游客私有消息')

    def test_support_messages_append_only_for_runtime(self):
        with psycopg.connect(database_url().replace('postgresql+psycopg://','postgresql://',1),options='-c search_path='+self.schema) as c:
            self.assertFalse(c.execute("SELECT has_table_privilege(current_user,'support_messages','UPDATE')").fetchone()[0])
            self.assertFalse(c.execute("SELECT has_table_privilege(current_user,'support_messages','DELETE')").fetchone()[0])

    def test_support_agent_cannot_receive_guest_order_lookup_secret(self):
        guest,order,_=self.guest_order();guest,cid=self.support_start(guest)
        self.assertEqual(guest.call('/support/conversations/'+cid+'/order',{'order_id':order['id']})[0],200)
        agent,_=self.support_agent();agent.call('/support/agent/conversations/'+cid+'/claim',{})
        packet=agent.call('/support/conversations/'+cid+'/messages?actor=agent')[1]
        self.assertNotIn(order['id'],json.dumps(packet))
        self.assertTrue(packet['conversation']['order']['id'].endswith(order['id'][-12:]))
        own=guest.call('/support/conversations/'+cid+'/messages')[1]
        self.assertEqual(own['conversation']['order_id'],order['id'])

if __name__=='__main__':unittest.main()
