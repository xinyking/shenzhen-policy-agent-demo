"""Explicitly saved local profile, opt-in session messages and application records."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from app.utils.storage_budget import ensure_capacity

class PersonalMemory:
    def __init__(self, path): self.path = Path(path)

    @contextmanager
    def connect(self, write=False):
        if write or not self.path.exists():
            ensure_capacity(self.path.resolve().parent.parent.parent, 1_000_000)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            db.executescript('''CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY, text TEXT);
              CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, session TEXT, question TEXT, answer TEXT);
              CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, payload TEXT, updated_at TEXT);''')
            with db: yield db
        finally: db.close()

    def snapshot(self):
        if not self.path.exists(): return {'profile':'','applications':[],'message_count':0}
        with self.connect() as db:
            row = db.execute('SELECT text FROM profile WHERE id=1').fetchone()
            return {'profile':row[0] if row else '',
                    'applications':[json.loads(r[0]) for r in db.execute('SELECT payload FROM applications ORDER BY updated_at DESC')],
                    'message_count':db.execute('SELECT count(*) FROM messages').fetchone()[0]}

    def save_profile(self, text):
        with self.connect(write=True) as db: db.execute('INSERT OR REPLACE INTO profile VALUES(1,?)',(text,))

    def context(self, session):
        if not self.path.exists(): return ''
        with self.connect() as db:
            profile = db.execute('SELECT text FROM profile WHERE id=1').fetchone()
            rows = db.execute('SELECT question,answer FROM messages WHERE session=? ORDER BY id DESC LIMIT 2',(session or '',)).fetchall()
            apps = [json.loads(r[0]) for r in db.execute('SELECT payload FROM applications ORDER BY updated_at DESC LIMIT 5')]
        if not (profile and profile[0]) and not rows and not apps: return ''
        return ('用户确认保存的信息（摘要）：'+(profile[0][:200] if profile else '')+'\n最近5条申请记录（非全部，用户自述非官方审核）：'+
                ';'.join(a['title'][:25]+':'+a.get('applicationStatus','preparing') for a in apps)+'\n'+
                '\n'.join('历史问题：'+q[:60]+'；历史答复非政策证据：'+a[:100] for q,a in reversed(rows)))[:800]

    def remember(self, session, question, answer):
        if not session: return
        with self.connect(write=True) as db:
            db.execute('INSERT INTO messages(session,question,answer) VALUES(?,?,?)',(session,question,answer[:3000]))
            db.execute('DELETE FROM messages WHERE session=? AND id NOT IN (SELECT id FROM messages WHERE session=? ORDER BY id DESC LIMIT 20)',(session,session))

    def save_application(self, payload):
        with self.connect(write=True) as db:
            db.execute('INSERT OR REPLACE INTO applications VALUES(?,?,?)',
                       (payload['id'],json.dumps(payload,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))

    def delete_application(self, id):
        if self.path.exists():
            with self.connect() as db: db.execute('DELETE FROM applications WHERE id=?',(id,))

    def clear(self):
        if self.path.exists():
            with self.connect() as db:
                for table in ('profile','messages','applications'): db.execute('DELETE FROM '+table)
