"""Reproducible local PostgreSQL setup with separate owner and runtime credentials."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]

def setup():
    if os.getenv('APP_ENV')=='production':raise RuntimeError('Local setup cannot run in production')
    envfile=ROOT/'.env.postgres'
    if not envfile.exists():
        password=secrets.token_hex(24)
        owner='postgresql+psycopg://piusgo:'+password+'@127.0.0.1:55432/piusgo'
        envfile.write_text('POSTGRES_USER=piusgo\nPOSTGRES_PASSWORD='+password+'\nPOSTGRES_DB=piusgo\nDATABASE_URL='+owner+'\nMIGRATION_DATABASE_URL='+owner+'\n')
        envfile.chmod(0o600)
    values=dict(line.split('=',1) for line in envfile.read_text().splitlines() if '=' in line and not line.startswith('#'))
    subprocess.run(['docker','compose','--project-name','piusgo','--env-file',str(envfile),'up','-d','postgres'],cwd=ROOT,check=True)
    owner=values['MIGRATION_DATABASE_URL']
    for attempt in range(30):
        try:
            connection=psycopg.connect(owner.replace('postgresql+psycopg://','postgresql://',1),connect_timeout=2)
            break
        except psycopg.OperationalError:
            if attempt==29:raise RuntimeError('Local PostgreSQL did not become ready') from None
            time.sleep(1)
    with connection as c:
        exists=c.execute("SELECT 1 FROM pg_roles WHERE rolname='piusgo_app'").fetchone()
        if not exists:
            password=secrets.token_hex(24)
            c.execute(sql.SQL('CREATE ROLE piusgo_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Literal(password)))
            values['DATABASE_URL']='postgresql+psycopg://piusgo_app:'+password+'@127.0.0.1:55432/piusgo'
        elif '://piusgo_app:' not in values['DATABASE_URL']:
            raise RuntimeError('Existing application role requires its saved credentials; refusing rotation')
    envfile.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));envfile.chmod(0o600)
    for command in [['alembic','upgrade','head'],['backend.grants'],['backend.seed']]:
        subprocess.run([sys.executable,'-m',*command],cwd=ROOT,check=True)
    print('PostgreSQL is ready on 127.0.0.1:55432; credentials remain in .env.postgres')

if __name__=='__main__':setup()
