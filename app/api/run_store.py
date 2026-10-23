"""Bounded local task/event journal, separate from policy evidence and personal memory."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from app.utils.storage_budget import ensure_capacity

TERMINAL = {'completed', 'degraded', 'needs_input', 'failed', 'cancelled', 'interrupted'}


def now():
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            return  # Legacy /v1/ask stays read-only unless tasks are actually used.
        self.initialize()
        self.interrupt_stale()

    def initialize(self):
        if not self.path.exists():
            self.capacity(1_000_000)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                session TEXT, status TEXT NOT NULL, node TEXT, created TEXT, updated TEXT,
                result TEXT, error TEXT);
              CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(run_id,seq));
            ''')
    def interrupt_stale(self):
        # Coroutine stacks cannot survive process restart. Never silently claim resumption.
        with self.connect() as db:
            stale = [r[0] for r in db.execute("SELECT id FROM runs WHERE status NOT IN ('completed','degraded','needs_input','failed','cancelled','interrupted')")]
        for run_id in stale:
            self.finish(run_id, 'interrupted', error='后端已重启，任务中断；请重新提问。')

    def capacity(self, size):
        ensure_capacity(self.path.resolve().parent.parent.parent, size)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def by_request(self, request_id):
        if not self.path.exists():
            return None
        with self.connect() as db:
            row = db.execute('SELECT id,fingerprint FROM runs WHERE request_id=?', (request_id,)).fetchone()
            return dict(row) if row else None

    def create(self, run_id, request_id, fingerprint, session):
        if not self.path.exists():
            self.initialize()
        self.capacity(200_000)
        at = now()
        with self.connect() as db:
            # Keep the latest 100 terminal runs, without touching policy or user schedule records.
            old = db.execute("SELECT id FROM runs WHERE status IN ('completed','degraded','needs_input','failed','cancelled','interrupted') ORDER BY created DESC LIMIT -1 OFFSET 99").fetchall()
            for row in old:
                db.execute('DELETE FROM events WHERE run_id=?', (row[0],))
                db.execute('DELETE FROM runs WHERE id=?', (row[0],))
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,NULL,NULL)',
                       (run_id, request_id, fingerprint, session, 'queued', None, at, at))

    @staticmethod
    def append_in(db, run_id, event, status=None):
        seq = db.execute('SELECT coalesce(max(seq),0)+1 FROM events WHERE run_id=?', (run_id,)).fetchone()[0]
        at = now()
        payload = {**event, 'run_id': run_id, 'seq': seq, 'at': at}
        db.execute('INSERT INTO events VALUES (?,?,?)', (run_id, seq, json.dumps(payload, ensure_ascii=False)))
        if event['type'] == 'node_started':
            db.execute('UPDATE runs SET node=?,updated=? WHERE id=?', (event['node'], at, run_id))
        if status:
            db.execute('UPDATE runs SET status=?,updated=? WHERE id=?', (status, at, run_id))

    def emit(self, run_id, event, status=None):
        with self.connect() as db:
            self.append_in(db, run_id, event, status)

    def finish(self, run_id, status, result=None, error=None):
        raw = json.dumps(result, ensure_ascii=False) if result is not None else None
        # Keep enough reserved room for a small terminal record even if the result cannot fit.
        if raw:
            self.capacity(len(raw.encode('utf-8')) + 100_000)
        with self.connect() as db:
            db.execute('UPDATE runs SET status=?,node=NULL,updated=?,result=?,error=? WHERE id=?',
                       (status, now(), raw, error, run_id))
            self.append_in(db, run_id, {'type': 'run_finished', 'node': None,
                                       'data': {'status': status}}, status)

    def get(self, run_id):
        if not self.path.exists():
            return None
        with self.connect() as db:
            row = db.execute('SELECT id,status,node,created,updated,result,error FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            raw = item.pop('result')
            item['run_id'] = item.pop('id')
            item['result_available'] = raw is not None
            return item

    def result(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT result FROM runs WHERE id=?', (run_id,)).fetchone()
            return json.loads(row[0]) if row and row[0] else None

    def events(self, run_id, after=0):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM events WHERE run_id=? AND seq>? ORDER BY seq', (run_id, after))]
