"""Runtime DML permissions; schema migrations use a separate owner role."""
import re
from sqlalchemy import text
from backend.database import make_engine, migration_url

def grant_runtime(connection,schema='public'):
    if not re.fullmatch('[a-z_][a-z0-9_]{0,62}',schema):raise ValueError('Invalid schema')
    # Identifiers are validated and the role is a fixed application role, never request input.
    connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO piusgo_app'))
    connection.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" TO piusgo_app'))
    connection.execute(text(f'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO piusgo_app'))
    connection.execute(text(f'REVOKE UPDATE,DELETE ON "{schema}".audit_events FROM piusgo_app'))
    if schema=='public':
        connection.execute(text('REVOKE ALL ON public.alembic_version FROM piusgo_app'))
    connection.execute(text(f'REVOKE CREATE ON SCHEMA "{schema}" FROM PUBLIC,piusgo_app'))

if __name__=='__main__':
    engine=make_engine(url=migration_url())
    with engine.begin() as c:grant_runtime(c)
    print('Runtime database permissions applied')
