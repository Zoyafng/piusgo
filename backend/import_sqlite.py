"""One-time copy into an EMPTY migrated PostgreSQL database. Source is read-only."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from sqlalchemy import select, func, text
from backend.database import make_engine, migration_url
engine=make_engine(url=migration_url())
from backend.schema import metadata

def migrate(source):
    source=Path(source).resolve()
    connection=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    report={}
    source_tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    try:
        with engine.begin() as target:
            target.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('piusgo:initial-import',0))"))
            for table in metadata.sorted_tables:
                if target.scalar(select(func.count()).select_from(table)):
                    raise RuntimeError('Target is not empty; import refused without overwriting data')
            for table in metadata.sorted_tables:
                records=[dict(r) for r in connection.execute('SELECT * FROM "'+table.name+'"')] if table.name in source_tables else []
                for record in records:
                    if table.name in ('orders','tickets'):record.setdefault('guest_key',None)
                    if table.name=='orders':record.setdefault('paid_at',None)
                    if table.name=='tickets':record.setdefault('priority','medium')
                if records:target.execute(table.insert(),records)
                copied=[dict(r) for r in target.execute(select(table)).mappings()]
                # Validate every field (including credentials, balances and order snapshots) without logging values.
                def fingerprint(rows):
                    normalized=sorted(json.dumps(r,sort_keys=True,ensure_ascii=False) for r in rows)
                    return hashlib.sha256('\n'.join(normalized).encode()).hexdigest()
                if fingerprint(records)!=fingerprint(copied):raise RuntimeError('Migration verification mismatch: '+table.name)
                report[table.name]=len(records)
            target.execute(text("SELECT setval(pg_get_serial_sequence('audit_events','id'),GREATEST(COALESCE((SELECT MAX(id) FROM audit_events),0),1),(SELECT COUNT(*)>0 FROM audit_events))"))
    finally:connection.close()
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');args=parser.parse_args()
    print(json.dumps(migrate(args.source),ensure_ascii=False,indent=2))
