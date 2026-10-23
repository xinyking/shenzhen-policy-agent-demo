"""Source-backed company directory; recognition records are not job offers."""
import hashlib
import re
import sqlite3
import unicodedata
from contextlib import closing
from pypdf import PdfReader

def normalize_name(name):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', name))

def pdf_rows(path):
    rows = []
    for page_number, page in enumerate(PdfReader(path).pages, 1):
        for line in (page.extract_text() or '').splitlines():
            match = re.match(r'^\s*(\d+)\s+(.+?(?:公司|企业|事务所|中心|实验室))\s*(?:[A-Z0-9]{18})?\s*$', line)
            if match:
                rows.append({'ordinal': int(match[1]), 'name': match[2].strip(), 'page': page_number})
    if not rows or [row['ordinal'] for row in rows] != list(range(1, len(rows)+1)):
        raise ValueError('名单序号不连续或无法提取；拒绝不完整入库')
    return rows

def save_directory(path, batches):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE IF NOT EXISTS companies(
          id TEXT PRIMARY KEY,name TEXT NOT NULL,normalized_name TEXT UNIQUE NOT NULL,
          city TEXT NOT NULL,recruitment_status TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS company_recognitions(
          company_id TEXT NOT NULL,source_id TEXT NOT NULL,category TEXT NOT NULL,
          cohort TEXT NOT NULL,status TEXT NOT NULL,source_url TEXT NOT NULL,
          published_at TEXT NOT NULL,attachment_sha256 TEXT NOT NULL,page INTEGER NOT NULL,
          ordinal INTEGER NOT NULL,PRIMARY KEY(company_id,source_id));''')
        for batch in batches:
            db.execute('DELETE FROM company_recognitions WHERE source_id=?', (batch['id'],))
            for row in batch['rows']:
                key = normalize_name(row['name'])
                cid = hashlib.sha256(key.encode()).hexdigest()[:24]
                db.execute('INSERT OR IGNORE INTO companies VALUES(?,?,?,?,?)',
                    (cid, row['name'], key, '深圳', '未核实招聘'))
                db.execute('INSERT INTO company_recognitions VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (cid,batch['id'],batch['category'],batch['cohort'],batch['status'],
                     batch['url'],batch['published_at'],batch['sha256'],row['page'],row['ordinal']))
        return {'companies':db.execute('SELECT count(*) FROM companies').fetchone()[0],
                'recognitions':db.execute('SELECT count(*) FROM company_recognitions').fetchone()[0]}
