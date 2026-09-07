"""PostgreSQL-only connections and transaction-scoped locking."""
import os
import re
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, text


def database_url(name='DATABASE_URL'):
    value=os.getenv(name)
    if not value and os.getenv('APP_ENV','development')!='production':
        path=Path(__file__).resolve().parents[1]/'.env.postgres'
        if path.exists():
            for line in path.read_text().splitlines():
                if line.startswith(name+'='):value=line.partition('=')[2].strip()
    if not value or not value.startswith('postgresql+psycopg://'):
        raise RuntimeError('Configure DATABASE_URL with a postgresql+psycopg URL')
    return value


def migration_url():
    return database_url('MIGRATION_DATABASE_URL')


def make_engine(url=None, schema=None):
    schema=schema or os.getenv('DATABASE_SCHEMA','public')
    if not re.fullmatch('[a-z_][a-z0-9_]{0,62}',schema):raise ValueError('Invalid database schema')
    return create_engine(url or database_url(),pool_pre_ping=True,pool_size=5,max_overflow=5,pool_timeout=10,pool_recycle=1800,hide_parameters=True,connect_args={'connect_timeout':5,'options':f'-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=15000'})

engine=make_engine()

class Result:
    def __init__(self,result):self.result=result
    @property
    def rowcount(self):return self.result.rowcount
    def fetchone(self):return self.result.mappings().fetchone()
    def __iter__(self):return iter(self.result.mappings())

class Connection:
    def __init__(self,connection):self.connection=connection
    def execute(self,statement,params=None):
        return Result(self.connection.execute(text(statement),params or {}))
    def lock(self,key):
        self.connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':key})

@contextmanager
def db(write=False):
    # PostgreSQL transaction boundary; row/advisory locks are explicit at each write path.
    with engine.begin() as connection:
        yield Connection(connection)
