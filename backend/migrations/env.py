from alembic import context
from backend.database import make_engine, migration_url
engine=make_engine(url=migration_url())
from backend.schema import metadata

if context.is_offline_mode():
    context.configure(url=migration_url(),target_metadata=metadata,literal_binds=True)
    with context.begin_transaction():context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
