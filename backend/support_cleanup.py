"""Remove abandoned, unreferenced uploads only; never delete sent attachments or messages."""
import time
from backend.database import db
from backend.support import FILES

def cleanup():
    cutoff=time.time()-86400
    with db() as c:
        candidates=list(c.execute('SELECT a.id,a.conversation_id FROM support_attachments a WHERE a.created<:cutoff AND NOT EXISTS(SELECT 1 FROM support_messages m WHERE m.attachment_id=a.id) LIMIT 500',{'cutoff':cutoff}))
    removed=0
    for a in candidates:
        with db(True) as c:
            # Same lock order as send: conversation first, then attachment lookup.
            c.execute('SELECT id FROM support_conversations WHERE id=:id FOR UPDATE',{'id':a['conversation_id']}).fetchone()
            result=c.execute('DELETE FROM support_attachments a WHERE a.id=:id AND a.created<:cutoff AND NOT EXISTS(SELECT 1 FROM support_messages m WHERE m.attachment_id=a.id)',{'id':a['id'],'cutoff':cutoff})
        if result.rowcount:
            (FILES/(a['id']+'.webp')).unlink(missing_ok=True);removed+=1
    # A crash between file creation and DB commit can leave an untracked private file.
    if FILES.exists():
        with db() as c:
            for file in FILES.glob('*.webp'):
                if file.stat().st_mtime<cutoff and not c.execute('SELECT id FROM support_attachments WHERE id=:id',{'id':file.stem}).fetchone():file.unlink();removed+=1
    return removed

if __name__=='__main__':print('Removed abandoned upload files:',cleanup())
