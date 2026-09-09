"""Explicit local operator command; never creates an account or a shared password."""
import argparse
import time
from backend.database import db

def main():
    parser=argparse.ArgumentParser(description='Grant or revoke admin access for an existing registered account')
    parser.add_argument('email')
    parser.add_argument('--revoke',action='store_true')
    parser.add_argument('--role',choices=['admin','support'],default='admin')
    args=parser.parse_args()
    with db(True) as c:
        c.lock('admin-access')
        row=c.execute('SELECT id,disabled FROM users WHERE email=:email FOR UPDATE',{'email':args.email.strip().lower()}).fetchone()
        if not row: parser.exit(1,'Account not found. Register this account first.\n')
        if row['disabled'] and not args.revoke: parser.exit(1,'Account is disabled. Enable it first.\n')
        role='member' if args.revoke else args.role
        c.execute('UPDATE users SET role=:role WHERE id=:id',{'role':role,'id':row['id']})
        c.execute('DELETE FROM sessions WHERE user_id=:id',{'id':row['id']})
        c.execute('INSERT INTO audit_events(user_id,event,object_id,created) VALUES(:id,:event,:id,:now)',{'id':row['id'],'event':'operator_role_'+role,'now':time.time()})
    print('Access updated. Sign in again to use the new permissions.')

if __name__=='__main__': main()
