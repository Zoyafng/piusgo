"""Generate new server-only credentials; never overwrite an existing env file."""
from pathlib import Path
import os
import secrets

root = Path(__file__).resolve().parent
content = (root / '.env.example').read_text()
content = content.replace('POSTGRES_PASSWORD=\n', 'POSTGRES_PASSWORD=' + secrets.token_hex(32) + '\n')
content = content.replace('APP_DB_PASSWORD=\n', 'APP_DB_PASSWORD=' + secrets.token_hex(32) + '\n')
fd = os.open(root / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(content)
print('Created deploy/.env with private access defaults; credentials were not printed.')
