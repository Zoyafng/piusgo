"""Provision the restricted application role, then migrate without demo seeds."""
import os
import subprocess
import sys
import psycopg
from psycopg import sql

url = os.environ['MIGRATION_DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
with psycopg.connect(url) as connection:
    connection.execute('SELECT pg_advisory_xact_lock(70697573676)')
    if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='piusgo_app'").fetchone():
        connection.execute(sql.SQL('CREATE ROLE piusgo_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Literal(os.environ['APP_DB_PASSWORD'])))
# A changed application password is not silently rotated: verify before applying DDL.
with psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)):
    pass
subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
subprocess.run([sys.executable, '-m', 'backend.grants'], check=True)
