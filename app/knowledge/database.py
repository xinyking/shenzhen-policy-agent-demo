"""Rebuildable SQLite policy catalog, separate from personal data and credentials."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from app.knowledge.domains import document_domains
from app.utils.storage_budget import ensure_capacity

def build_database(store, documents):
    path = Path(store) / 'policies.sqlite3'
    ensure_capacity(Path(store).resolve().parent.parent,
        sum(len(d['text'].encode('utf-8')) for d in documents) * 8 + 1_000_000)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE IF NOT EXISTS documents (
            source_id TEXT PRIMARY KEY,title TEXT,url TEXT,version TEXT,published_at TEXT,
            domains TEXT,kind TEXT,text TEXT);
            CREATE TABLE IF NOT EXISTS paragraphs (
            id TEXT PRIMARY KEY,source_id TEXT,start INTEGER,end INTEGER,locator TEXT,text TEXT);
            CREATE INDEX IF NOT EXISTS paragraphs_source ON paragraphs(source_id);
        ''')
        db.execute('DELETE FROM paragraphs'); db.execute('DELETE FROM documents')
        for doc in documents:
            db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)',
                (doc['source_id'],doc['title'],doc['source_url'],doc['version'],doc['published_at'],
                 json.dumps(document_domains(doc)),doc['kind'],doc['text']))
            db.executemany('INSERT INTO paragraphs VALUES (?,?,?,?,?,?)',
                [(s['id'],doc['source_id'],s['start'],s['end'],s['locator'],s['text']) for s in doc['spans']])
    return {'path': str(path), 'documents': len(documents),
            'paragraphs': sum(len(d['spans']) for d in documents)}

def database_spans(store, documents):
    path = Path(store) / 'policies.sqlite3'
    if not path.exists(): return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            versions = dict(db.execute('SELECT source_id,version FROM documents'))
            if versions != {d['source_id']:d['version'] for d in documents}: return None
            rows = db.execute('SELECT id,source_id,start,end,locator,text FROM paragraphs').fetchall()
        result = {}
        expected = {s['id']:s for d in documents for s in d['spans']}
        if len(rows) != len(expected): return None
        for sid, source, start, end, locator, text in rows:
            span = dict(id=sid,start=start,end=end,locator=locator,text=text)
            if expected.get(sid) != span: return None
            result.setdefault(source,[]).append(span)
        return result
    except sqlite3.Error:
        return None
