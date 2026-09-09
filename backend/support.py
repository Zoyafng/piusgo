"""Durable support messages. PostgreSQL is the source of truth, including SSE replay."""
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import secrets
import time
import warnings
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from backend.database import db

FILES=Path(os.getenv('SUPPORT_ATTACHMENT_DIR',str(Path(__file__).parent/'data'/'support-attachments')))
FAQS=[{'id':1,'category':'订单问题','question':'在哪里查询订单和卡密？','answer':'打开查订单页面。会员订单需登录本人账号；游客订单使用完整订单号查询，请妥善保管订单号。'}, {'id':2,'category':'充值问题','question':'付款后暂时没有收到邮件怎么办？','answer':'先查看订单状态和页面中的交付内容。邮件可能正在重试；不要重复付款，可关联订单联系人工客服。'}, {'id':3,'category':'售后问题','question':'客服离线时如何反馈问题？','answer':'可以直接留言，消息会保存；需要持续跟进时可为已付款订单创建工单。请勿发送账号密码。'}]

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class Message(Input):
    client_id:str=Field(pattern=r'^[A-Za-z0-9_-]{16,64}$')
    body:str=Field(default='',max_length=4000)
    attachment_id:str|None=Field(default=None,pattern=r'^[0-9a-f]{32}$')
class Read(Input):last_seq:int=Field(strict=True,ge=0)
class Contact(Input):email:str=Field(max_length=191)
class OrderLink(Input):order_id:str=Field(min_length=3,max_length=50)
class Upload(Input):
    conversation_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    data:str=Field(min_length=1,max_length=1400000)
class Presence(Input):online:bool
class Transfer(Input):agent_id:str=Field(pattern=r'^[0-9a-f]{32}$')
class Convert(Input):
    title:str=Field(min_length=3,max_length=100)
    body:str=Field(min_length=10,max_length=3000)
    priority:Literal['low','medium','high']='medium'
class FAQ(Input):
    category:str=Field(min_length=1,max_length=40)
    question:str=Field(min_length=3,max_length=200)
    answer:str=Field(min_length=1,max_length=2000)

def fail(message='会话不存在或无权访问',status=404):raise HTTPException(status,message)

def install_support(app,optional_member,guest_identity,limit,email_validator):
    router=APIRouter(prefix='/api/support')
    def principal(request,response=None):
        user=optional_member(request)
        guest=guest_identity(request,response)
        if user:return {'user':user,'uid':user['id'],'guest':None,'key':'u:'+user['id']}
        if guest:return {'user':None,'uid':None,'guest':guest,'key':'g:'+guest}
        fail('请先打开客服窗口',401)
    def staff(p):
        if not p['user'] or p['user']['role'] not in ('admin','support'):fail('没有客服接待权限',403)
    def access(c,cid,p,actor='customer',lock=False):
        row=c.execute('SELECT * FROM support_conversations WHERE id=:id'+(' FOR UPDATE' if lock else ''),{'id':cid}).fetchone()
        if not row:fail()
        if actor=='agent':
            staff(p)
            if p['user']['role']!='admin' and row['assigned_to']!=p['uid']:fail()
        elif (row['user_id']!=p['uid'] if p['uid'] else row['user_id'] is not None or row['guest_key']!=p['guest']):fail()
        return row
    def reader(p,actor):return 'customer' if actor=='customer' else 'agent:'+p['uid']
    def audit(c,p,event,cid):
        c.execute('INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:uid,:event,:id,:now)',{'uid':p['uid'],'event':'support_'+event,'id':cid,'now':time.time()})
    def online(c):
        return bool(c.execute("SELECT count(*) AS n FROM support_presence p JOIN users u ON u.id=p.user_id WHERE p.expires>:now AND u.disabled=0 AND u.role IN ('admin','support')",{'now':time.time()}).fetchone()['n'])
    def summary(c,row,p,actor):
        other='agent:%' if actor=='customer' else 'customer'
        read=c.execute('SELECT COALESCE(MAX(last_seq),0) AS n FROM support_reads WHERE conversation_id=:id AND reader_key LIKE :key',{'id':row['id'],'key':other}).fetchone()['n']
        result={k:row[k] for k in ['id','status','last_seq','contact_email','order_id','ticket_id','created','updated','assigned_to']}
        result['other_read_seq']=read;result['online']=online(c)
        mine=c.execute('SELECT last_seq FROM support_reads WHERE conversation_id=:id AND reader_key=:key',{'id':row['id'],'key':reader(p,actor)}).fetchone()
        result['my_read_seq']=mine['last_seq'] if mine else 0
        result['unread']=c.execute('SELECT count(*) AS n FROM support_messages WHERE conversation_id=:id AND sender_role=:role AND seq>:seq',{'id':row['id'],'role':'agent' if actor=='customer' else 'customer','seq':result['my_read_seq']}).fetchone()['n']
        result['order']=None
        if row['order_id']:
            o=c.execute('SELECT id,product_name,variant_name,total,status,created FROM orders WHERE id=:id',{'id':row['order_id']}).fetchone()
            result['order']=dict(o) if o else None
        if actor=='agent':
            result['customer_name']='游客'
            if row['user_id']:
                u=c.execute('SELECT display_name,email FROM users WHERE id=:id',{'id':row['user_id']}).fetchone()
                result['customer_name']=u['display_name'] or '会员用户'
                result['contact_email']=u['email']
            elif row['order_id']:
                # Guest order IDs are lookup secrets; agents get a tail reference only.
                result['order_id']='…'+row['order_id'][-12:]
                if result['order']:result['order']['id']=result['order_id']
        return result
    def snapshot(request,cid,actor,after=None,before=None):
        p=principal(request)
        with db() as c:
            row=access(c,cid,p,actor)
            if after is not None and after>row['last_seq']:fail('消息游标超出会话范围',400)
            params={'id':cid}
            if after is not None:
                clause=' AND seq>:cursor';params['cursor']=after;order='ASC'
            else:
                clause=' AND seq<:cursor' if before is not None else '';order='DESC'
                if before is not None:params['cursor']=before
            messages=[dict(x) for x in c.execute('SELECT id,seq,sender_role,body,attachment_id,created,client_id FROM support_messages WHERE conversation_id=:id'+clause+' ORDER BY seq '+order+' LIMIT 100',params)]
            if order=='DESC':messages.reverse()
            return {'conversation':summary(c,row,p,actor),'messages':messages,'cursor':messages[-1]['seq'] if messages else (after or row['last_seq']),'has_older':bool(messages and messages[0]['seq']>1)}

    @router.get('/identity')
    def identity(request:Request):
        try:p=principal(request)
        except HTTPException:return {'identity':None}
        return {'identity':hashlib.sha256(p['key'].encode()).hexdigest()}

    @router.get('/unread')
    def unread(request:Request):
        try:p=principal(request)
        except HTTPException:return {'unread':0}
        limit('support-badge:'+p['key'],30,60)
        with db() as c:
            row=c.execute('SELECT id FROM support_conversations WHERE '+('user_id=:owner' if p['uid'] else 'guest_key=:owner'),{'owner':p['uid'] or p['guest']}).fetchone()
            if not row:return {'unread':0}
            value=c.execute("SELECT count(*) AS n FROM support_messages WHERE conversation_id=:id AND sender_role='agent' AND seq>COALESCE((SELECT last_seq FROM support_reads WHERE conversation_id=:id AND reader_key='customer'),0)",{'id':row['id']}).fetchone()['n']
        return {'unread':value}

    @router.post('/session')
    def session(request:Request,response:Response):
        p=principal(request,response);limit('support-start-ip:'+request.client.host,30,600)
        with db(True) as c:
            c.lock('support-owner:'+p['key'])
            row=c.execute('SELECT * FROM support_conversations WHERE '+('user_id=:owner' if p['uid'] else 'guest_key=:owner'),{'owner':p['uid'] or p['guest']}).fetchone()
            if not row:
                cid=secrets.token_hex(16);now=time.time()
                c.execute("INSERT INTO support_conversations(id,user_id,guest_key,created,updated) VALUES(:id,:uid,:guest,:now,:now)",{'id':cid,'uid':p['uid'],'guest':p['guest'],'now':now})
                row=access(c,cid,p)
            return {'identity':hashlib.sha256(p['key'].encode()).hexdigest(),'conversation':summary(c,row,p,'customer'),'member':bool(p['uid']),'email':p['user']['email'] if p['user'] else row['contact_email']}

    @router.get('/faqs')
    def faqs():
        with db() as c:items=[dict(x) for x in c.execute('SELECT * FROM support_faqs ORDER BY id')]
        merged={item['id']:item for item in FAQS}
        merged.update({item['id']:item for item in items})
        return {'items':[merged[key] for key in sorted(merged)]}

    @router.post('/faqs/{fid}')
    def edit_faq(fid:int,body:FAQ,request:Request):
        p=principal(request);staff(p)
        if p['user']['role']!='admin':fail('仅管理员可以编辑常见问题',403)
        if not 1<=fid<=20:fail('问题编号不正确',400)
        with db(True) as c:
            c.execute('INSERT INTO support_faqs(id,category,question,answer) VALUES(:id,:category,:question,:answer) ON CONFLICT(id) DO UPDATE SET category=EXCLUDED.category,question=EXCLUDED.question,answer=EXCLUDED.answer',dict(body.model_dump(),id=fid))
            audit(c,p,'faq_updated',str(fid))
        return {'message':'常见问题已保存'}

    @router.get('/conversations/{cid}/messages')
    def messages(cid:str,request:Request,actor:Literal['customer','agent']='customer',after:int|None=Query(None,ge=0),before:int|None=Query(None,ge=1)):
        p=principal(request);limit('support-read:'+p['key'],180,60)
        if after is not None and before is not None:fail('不能同时指定两个游标',400)
        packet=snapshot(request,cid,actor,after,before)
        if actor=='agent' and after is None and before is None:
            with db(True) as c:audit(c,p,'viewed',cid)
        return packet

    @router.post('/conversations/{cid}/messages')
    def send(cid:str,body:Message,request:Request,actor:Literal['customer','agent']='customer'):
        p=principal(request);limit('support-send:'+p['key'],30,60);limit('support-send-ip:'+request.client.host,120,60)
        content=body.body.strip()
        if not content and not body.attachment_id:fail('消息不能为空',400)
        if any(ord(ch)<32 and ch not in '\n\r\t' for ch in content):fail('消息包含不支持的控制字符',400)
        key=reader(p,actor)
        with db(True) as c:
            row=access(c,cid,p,actor,True)
            existing=c.execute('SELECT id,seq,sender_role,body,attachment_id,created,client_id FROM support_messages WHERE conversation_id=:id AND sender_key=:sender AND client_id=:client',{'id':cid,'sender':key,'client':body.client_id}).fetchone()
            if existing:
                if existing['body']!=content or existing['attachment_id']!=body.attachment_id:fail('重试标识不能用于不同消息',409)
                return dict(existing)
            if actor=='agent' and (row['status']=='closed' or row['assigned_to']!=p['uid']):fail('请先接待该会话',409)
            if body.attachment_id:
                attachment=c.execute('SELECT id FROM support_attachments WHERE id=:aid AND conversation_id=:cid AND sender_key=:sender',{'aid':body.attachment_id,'cid':cid,'sender':key}).fetchone()
                used=c.execute('SELECT id FROM support_messages WHERE attachment_id=:aid',{'aid':body.attachment_id}).fetchone()
                if not attachment or used:fail('附件不存在或不可用于此消息',400)
            now=time.time();seq=row['last_seq']+1;mid=secrets.token_hex(16)
            if actor=='customer' and row['status']=='closed':
                c.execute("UPDATE support_conversations SET status='waiting',assigned_to=NULL WHERE id=:id",{'id':cid})
            c.execute('INSERT INTO support_messages(id,conversation_id,seq,sender_key,sender_role,body,client_id,attachment_id,created) VALUES(:id,:cid,:seq,:key,:role,:body,:client,:aid,:now)',{'id':mid,'cid':cid,'seq':seq,'key':key,'role':actor,'body':content,'client':body.client_id,'aid':body.attachment_id,'now':now})
            c.execute('UPDATE support_conversations SET last_seq=:seq,updated=:now WHERE id=:id',{'seq':seq,'now':now,'id':cid})
            return {'id':mid,'seq':seq,'sender_role':actor,'body':content,'attachment_id':body.attachment_id,'created':now,'client_id':body.client_id}

    @router.post('/conversations/{cid}/read')
    def mark_read(cid:str,body:Read,request:Request,actor:Literal['customer','agent']='customer'):
        p=principal(request);limit('support-mark:'+p['key'],120,60)
        with db(True) as c:
            row=access(c,cid,p,actor)
            if body.last_seq>row['last_seq']:fail('已读位置超出会话范围',400)
            c.execute('INSERT INTO support_reads(conversation_id,reader_key,last_seq) VALUES(:id,:reader,:seq) ON CONFLICT(conversation_id,reader_key) DO UPDATE SET last_seq=GREATEST(support_reads.last_seq,EXCLUDED.last_seq)',{'id':cid,'reader':reader(p,actor),'seq':body.last_seq})
        return {'message':'已更新'}

    @router.post('/conversations/{cid}/contact')
    def contact(cid:str,body:Contact,request:Request):
        p=principal(request)
        if p['uid']:fail('会员联系邮箱从登录账号读取',400)
        address=email_validator(body.email);limit('support-contact:'+p['key'],10,600)
        with db(True) as c:
            access(c,cid,p,lock=True)
            c.execute('UPDATE support_conversations SET contact_email=:email WHERE id=:id',{'email':address,'id':cid})
        return {'message':'联系方式已更新，不会改变订单或会话归属'}

    @router.get('/orders')
    def eligible_orders(request:Request):
        p=principal(request)
        with db() as c:
            rows=c.execute('SELECT id,product_name,variant_name,total,status FROM orders WHERE '+('user_id=:owner' if p['uid'] else 'guest_key=:owner')+' ORDER BY created DESC LIMIT 30',{'owner':p['uid'] or p['guest']})
            return {'items':[dict(r) for r in rows]}

    @router.post('/conversations/{cid}/order')
    def bind_order(cid:str,body:OrderLink,request:Request):
        p=principal(request)
        with db(True) as c:
            access(c,cid,p,lock=True)
            o=c.execute('SELECT id FROM orders WHERE id=:id AND '+('user_id=:owner' if p['uid'] else 'guest_key=:owner'),{'id':body.order_id,'owner':p['uid'] or p['guest']}).fetchone()
            if not o:fail('不能关联不属于当前身份的订单',404)
            c.execute('UPDATE support_conversations SET order_id=:oid,updated=:now WHERE id=:id',{'oid':o['id'],'id':cid,'now':time.time()})
            audit(c,p,'order_linked',cid)
        return {'message':'订单已关联'}

    @router.post('/attachments')
    def upload(body:Upload,request:Request,actor:Literal['customer','agent']='customer'):
        p=principal(request);limit('support-upload:'+p['key'],6,60);limit('support-upload-ip:'+request.client.host,30,3600)
        with db() as c:
            row=access(c,body.conversation_id,p,actor)
            if actor=='agent' and (row['assigned_to']!=p['uid'] or row['status']=='closed'):fail('请先接待该会话',409)
        try:
            raw=base64.b64decode(body.data,validate=True)
            if len(raw)>1000000:raise ValueError()
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ('PNG','JPEG','WEBP') or image.width*image.height>12000000 or max(image.size)>4096:raise ValueError()
                    image.load();clean=ImageOps.exif_transpose(image).convert('RGB');out=io.BytesIO();clean.save(out,'WEBP',quality=85)
            content=out.getvalue()
            if len(content)>2000000:raise ValueError()
        except Exception:fail('仅支持 1 MB 以内的 PNG/JPEG/WebP 图片，边长不超过 4096',400)
        aid=secrets.token_hex(16);path=FILES/(aid+'.webp')
        FILES.mkdir(parents=True,exist_ok=True);FILES.chmod(0o700)
        try:
            with db(True) as c:
                row=access(c,body.conversation_id,p,actor,True)
                if actor=='agent' and (row['assigned_to']!=p['uid'] or row['status']=='closed'):fail('请先接待该会话',409)
                size=c.execute('SELECT COALESCE(SUM(size),0) AS n FROM support_attachments WHERE conversation_id=:id',{'id':body.conversation_id}).fetchone()['n']
                if size+len(content)>20000000:fail('该会话附件已达到 20 MB 上限',400)
                with open(path,'xb') as f:f.write(content)
                path.chmod(0o600)
                c.execute('INSERT INTO support_attachments(id,conversation_id,sender_key,size,sha256,created) VALUES(:id,:cid,:key,:size,:hash,:now)',{'id':aid,'cid':body.conversation_id,'key':reader(p,actor),'size':len(content),'hash':hashlib.sha256(content).hexdigest(),'now':time.time()})
        except Exception:
            path.unlink(missing_ok=True);raise
        return {'id':aid}

    @router.get('/attachments/{aid}')
    def attachment(aid:str,request:Request,actor:Literal['customer','agent']='customer'):
        if not re.fullmatch('[0-9a-f]{32}',aid):fail()
        p=principal(request)
        with db() as c:
            row=c.execute('SELECT conversation_id FROM support_attachments WHERE id=:id',{'id':aid}).fetchone()
            if not row:fail()
            access(c,row['conversation_id'],p,actor)
            owner=c.execute('SELECT sender_key FROM support_attachments WHERE id=:id',{'id':aid}).fetchone()['sender_key']
            linked=c.execute('SELECT id FROM support_messages WHERE attachment_id=:id',{'id':aid}).fetchone()
            if not linked and owner!=reader(p,actor):fail()
        path=FILES/(aid+'.webp')
        if not path.is_file():fail()
        return FileResponse(path,media_type='image/webp',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/agent/session')
    def agent_session(request:Request):
        p=principal(request);staff(p)
        return {'id':p['uid'],'name':p['user']['display_name'] or '客服','role':p['user']['role']}

    @router.post('/agent/presence')
    def presence(body:Presence,request:Request):
        p=principal(request);staff(p);limit('support-presence:'+p['key'],10,60)
        with db(True) as c:
            c.execute('INSERT INTO support_presence(user_id,expires) VALUES(:id,:expires) ON CONFLICT(user_id) DO UPDATE SET expires=EXCLUDED.expires',{'id':p['uid'],'expires':time.time()+45 if body.online else 0})
        return {'online':body.online}

    @router.get('/agent/agents')
    def agents(request:Request):
        p=principal(request);staff(p)
        with db() as c:
            return {'items':[dict(r) for r in c.execute("SELECT u.id,u.display_name FROM users u JOIN support_presence p ON p.user_id=u.id WHERE p.expires>:now AND u.disabled=0 AND u.role IN ('support','admin') ORDER BY u.id",{'now':time.time()})]}

    @router.get('/agent/conversations')
    def queue(request:Request,status:Literal['waiting','active','closed']='waiting',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=50)):
        p=principal(request);staff(p);limit('support-queue:'+p['key'],60,60)
        clause='status=:status AND last_seq>0';params={'status':status,'uid':p['uid'],'key':reader(p,'agent'),'size':page_size,'offset':(page-1)*page_size}
        if p['user']['role']!='admin':clause+=" AND (assigned_to=:uid OR (assigned_to IS NULL AND status='waiting'))"
        with db() as c:
            rows=c.execute("SELECT id,status,assigned_to,created,updated,last_seq,(SELECT count(*) FROM support_messages m WHERE m.conversation_id=s.id AND m.sender_role='customer' AND m.seq>COALESCE((SELECT last_seq FROM support_reads r WHERE r.conversation_id=s.id AND r.reader_key=:key),0)) AS unread FROM support_conversations s WHERE "+clause+' ORDER BY updated DESC,id DESC LIMIT :size OFFSET :offset',params)
            total=c.execute('SELECT count(*) AS n FROM support_conversations WHERE '+clause,params).fetchone()['n']
            return {'items':[dict(r) for r in rows],'total':total}

    @router.post('/agent/conversations/{cid}/claim')
    def claim(cid:str,request:Request):
        p=principal(request);staff(p)
        with db(True) as c:
            row=c.execute('SELECT * FROM support_conversations WHERE id=:id FOR UPDATE',{'id':cid}).fetchone()
            if not row:fail()
            if row['assigned_to'] and row['assigned_to']!=p['uid']:fail('该会话已由其他客服接待',409)
            c.execute("UPDATE support_conversations SET assigned_to=:uid,status='active',updated=:now WHERE id=:id",{'id':cid,'uid':p['uid'],'now':time.time()})
            audit(c,p,'claimed',cid)
        return {'message':'已接待会话'}

    @router.post('/agent/conversations/{cid}/transfer')
    def transfer(cid:str,body:Transfer,request:Request):
        p=principal(request);staff(p)
        with db(True) as c:
            row=access(c,cid,p,'agent',True)
            if row['status']=='closed':fail('会话已结束',409)
            target=c.execute("SELECT id FROM users WHERE id=:id AND disabled=0 AND role IN ('support','admin')",{'id':body.agent_id}).fetchone()
            if not target:fail('目标客服不可用',400)
            c.execute("UPDATE support_conversations SET assigned_to=:uid,status='active',updated=:now WHERE id=:id",{'id':cid,'uid':body.agent_id,'now':time.time()})
            audit(c,p,'transferred',cid)
        return {'message':'已转接'}

    @router.post('/agent/conversations/{cid}/close')
    def close(cid:str,request:Request):
        p=principal(request);staff(p)
        with db(True) as c:
            access(c,cid,p,'agent',True)
            c.execute("UPDATE support_conversations SET status='closed',updated=:now WHERE id=:id",{'id':cid,'now':time.time()});audit(c,p,'closed',cid)
        return {'message':'会话已结束'}

    @router.post('/agent/conversations/{cid}/ticket')
    def convert(cid:str,body:Convert,request:Request):
        p=principal(request);staff(p)
        if len(body.title.strip())<3 or len(body.body.strip())<10:fail('请填写完整的工单摘要',400)
        with db(True) as c:
            row=access(c,cid,p,'agent',True)
            if row['ticket_id']:return {'id':row['ticket_id']}
            order=c.execute('SELECT * FROM orders WHERE id=:id FOR UPDATE',{'id':row['order_id']}).fetchone()
            if not order or order['status']!='paid':fail('请先关联已付款订单',400)
            existing=c.execute('SELECT * FROM tickets WHERE order_id=:id',{'id':order['id']}).fetchone()
            if existing:
                if existing['user_id']!=row['user_id'] or existing['guest_key']!=row['guest_key']:fail('该订单已有其他身份提交的工单',409)
                tid=existing['id']
            else:
                tid='TK'+secrets.token_hex(12).upper()
                c.execute("INSERT INTO tickets(id,user_id,guest_key,order_id,title,body,status,priority,created) VALUES(:id,:uid,:guest,:oid,:title,:body,'open',:priority,:now)",{'id':tid,'uid':row['user_id'],'guest':row['guest_key'],'oid':order['id'],'title':body.title.strip(),'body':body.body.strip(),'priority':body.priority,'now':time.time()})
            c.execute('UPDATE support_conversations SET ticket_id=:ticket WHERE id=:id',{'ticket':tid,'id':cid});audit(c,p,'ticket_linked',cid)
        return {'id':tid}

    @router.get('/conversations/{cid}/events')
    async def events(cid:str,request:Request,actor:Literal['customer','agent']='customer',after:int=Query(0,ge=0)):
        p=await asyncio.to_thread(principal,request)
        await asyncio.to_thread(snapshot,request,cid,actor,after)
        header=request.headers.get('last-event-id')
        if header is not None:
            if not header.isdigit() or len(header)>10:fail('重连游标无效',400)
            after=int(header)
            await asyncio.to_thread(snapshot,request,cid,actor,after)
        lease=secrets.token_hex(16);key=p['key']+':'+actor
        def acquire():
            with db(True) as c:
                c.lock('support-stream-limit');c.execute('DELETE FROM support_streams WHERE expires<:now',{'now':time.time()})
                total=c.execute('SELECT count(*) AS n FROM support_streams').fetchone()['n']
                own=c.execute('SELECT count(*) AS n FROM support_streams WHERE reader_key=:key',{'key':key}).fetchone()['n']
                if total>=60 or own>=2:fail('实时连接已达上限，请稍后重试',429)
                c.execute('INSERT INTO support_streams VALUES(:id,:key,:expires)',{'id':lease,'key':key,'expires':time.time()+35})
        await asyncio.to_thread(acquire)
        if actor=='agent':
            def log_stream():
                with db(True) as c:audit(c,p,'stream_opened',cid)
            await asyncio.to_thread(log_stream)
        def release():
            with db(True) as c:c.execute('DELETE FROM support_streams WHERE id=:id',{'id':lease})
        async def generate():
            cursor=after;deadline=time.monotonic()+20
            try:
                yield 'retry: 3000\n\n'
                while time.monotonic()<deadline and not await request.is_disconnected():
                    try:
                        packet=await asyncio.to_thread(snapshot,request,cid,actor,cursor)
                    except HTTPException:
                        yield 'event: revoked\ndata: {}\n\n';return
                    for m in packet['messages']:
                        cursor=m['seq'];yield 'id: '+str(cursor)+'\nevent: message\ndata: '+json.dumps(m,ensure_ascii=False)+'\n\n'
                    yield 'event: state\ndata: '+json.dumps(packet['conversation'],ensure_ascii=False)+'\n\n'
                    await asyncio.sleep(2)
            finally:
                await asyncio.shield(asyncio.to_thread(release))
        return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    app.include_router(router)
