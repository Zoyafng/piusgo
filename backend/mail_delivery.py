"""Transactional outbox worker; mock delivery never sends external email."""
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from backend.database import db

def send_one():
    now=time.time()
    with db(True) as c:
        c.execute("UPDATE mail_outbox SET status='queued',locked_at=NULL WHERE status='sending' AND locked_at<:cutoff",{'cutoff':now-120})
        row=c.execute("SELECT * FROM mail_outbox WHERE status='queued' AND next_attempt<=:now ORDER BY created FOR UPDATE SKIP LOCKED LIMIT 1",{'now':now}).fetchone()
        if not row:return False
        job=dict(row)
        c.execute("UPDATE mail_outbox SET status='sending',locked_at=:now,attempts=attempts+1 WHERE id=:id",{'now':now,'id':job['id']})
    try:
        mode=os.getenv('MAIL_MODE','mock')
        if mode not in ('mock','smtp'):raise RuntimeError('Unsupported mail mode')
        if os.getenv('APP_ENV')=='production' and mode!='smtp':raise RuntimeError('SMTP required in production')
        message=EmailMessage()
        message['From']=os.getenv('SMTP_FROM','noreply@piusgo.local')
        message['To']=job['recipient'];message['Subject']=job['subject']
        message['Message-ID']='<'+job['id']+'@piusgo.local>'
        message.set_content(job['body'])
        if mode=='mock':
            folder=Path(os.getenv('MAIL_OUTBOX_DIR',str(Path(__file__).parent/'data'/'mail')))
            folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
            temp=folder/(job['id']+'.'+secrets.token_hex(4)+'.tmp')
            with open(temp,'xb') as f:f.write(message.as_bytes())
            temp.chmod(0o600);temp.replace(folder/(job['id']+'.eml'))
            status='mock_delivered'
        else:
            host=os.environ['SMTP_HOST'];user=os.environ['SMTP_USER'];password=os.environ['SMTP_PASSWORD']
            if not os.getenv('SMTP_FROM'):raise RuntimeError('SMTP_FROM required')
            port=int(os.getenv('SMTP_PORT','465'));context=ssl.create_default_context()
            if port==465:
                client=smtplib.SMTP_SSL(host,port,timeout=20,context=context)
            else:
                client=smtplib.SMTP(host,port,timeout=20);client.starttls(context=context)
            with client:
                client.login(user,password);client.send_message(message)
            status='sent'
        with db(True) as c:
            c.execute('UPDATE mail_outbox SET status=:status,sent_at=:now,locked_at=NULL,error=NULL WHERE id=:id',{'status':status,'now':time.time(),'id':job['id']})
    except Exception as error:
        attempts=job['attempts']+1
        with db(True) as c:
            c.execute('UPDATE mail_outbox SET status=:status,next_attempt=:next,locked_at=NULL,error=:error WHERE id=:id',{'status':'failed' if attempts>=5 else 'queued','next':time.time()+min(3600,30*2**attempts),'error':type(error).__name__,'id':job['id']})
    return True

if __name__=='__main__':
    from backend.order_service import expire_orders
    while True:
        try:
            expire_orders()
            if not send_one():time.sleep(2)
        except KeyboardInterrupt:break
        except Exception:time.sleep(5)
