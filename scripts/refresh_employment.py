"""Bounded official employment discovery; archive old records without deleting evidence."""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from app.knowledge.ingestion import (OfficialFetcher, current_documents, parse_html,
    read_json, save_snapshot, write_json)
from app.knowledge.database import build_database
from app.utils.storage_budget import StorageLimit, directory_bytes

TERMS = re.compile('就业|招聘|招募|求职|见习|实习|职业培训|职业技能|失业|稳岗|校园|双选')

def age_status(published, cutoff, as_of):
    match = re.match(r'\d{4}-\d{2}-\d{2}', published or '')
    if not match:
        return 'unknown_date'
    try:
        value = date.fromisoformat(match.group())
    except ValueError:
        return 'unknown_date'
    return 'old' if value < cutoff else 'future' if value > as_of else 'recent'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=70)
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error('limit must be 1..200')
    root = Path(__file__).resolve().parents[1]
    store = root / 'data/store'
    catalog = read_json(root / 'data/sources/employment-catalog.json')
    cutoff, as_of = map(date.fromisoformat, (catalog['cutoff'], catalog['as_of']))
    config_path = root / 'data/sources/shenzhen.json'
    config = read_json(config_path)
    manifest = read_json(store / 'manifest.json')
    archived = []
    for doc in current_documents(store):
        if age_status(doc.get('published_at'), cutoff, as_of) == 'old':
            entry = manifest['documents'][doc['source_id']]
            entry.update(status='archived', archive_reason='published_before_'+cutoff.isoformat())
            archived.append({'id': doc['source_id'], 'date': doc['published_at'], 'title': doc['title']})
    archived_ids = {item['id'] for item in archived}
    for source in config['sources']:
        if source['id'] in archived_ids:
            source['status'] = 'archived'
    write_json(store / 'manifest.json', manifest)
    write_json(config_path, config)
    hosts = sorted(set(config['allowed_hosts']) | {'gzw.sz.gov.cn'})
    fetcher = OfficialFetcher(hosts, tls_curve='prime256v1')
    urls = dict.fromkeys(catalog['seed_urls'])
    report = {'cutoff': str(cutoff), 'as_of': str(as_of), 'archived': archived,
              'directories': [], 'results': [], 'coverage': 'bounded HTML discovery, not exhaustive'}
    for directory in catalog['directories']:
        try:
            raw, kind, charset = fetcher.fetch(directory['url'])
            if kind not in ('text/html', 'application/xhtml+xml'):
                raise ValueError('not HTML')
            soup = BeautifulSoup(raw, 'html.parser', from_encoding=charset)
            count = 0
            for anchor in soup.select('a[href]'):
                url = urljoin(directory['url'], anchor['href']).split('#')[0]
                if (urlparse(url).hostname in hosts and re.search(r'/post_\d+\.html$', url)
                        and TERMS.search(anchor.get_text(' ', strip=True))):
                    urls.setdefault(url, None)
                    count += 1
            report['directories'].append({**directory, 'ok': True, 'links': count})
        except (OSError, ValueError) as error:
            report['directories'].append({**directory, 'ok': False, 'error': type(error).__name__})
    existing = {source['url']: source for source in config['sources']}
    for url in list(urls)[:args.limit]:
        try:
            raw, kind, charset = fetcher.fetch(url)
            if kind not in ('text/html', 'application/xhtml+xml'):
                raise ValueError('not HTML')
            soup = BeautifulSoup(raw, 'html.parser', from_encoding=charset)
            selector = next((sel for sel in ('.news_cont_d_wrap', '.article-content', '.TRS_Editor', '.viewTRS', '#zoom', '.content') if soup.select_one(sel)), None)
            if not selector:
                raise ValueError('no article selector')
            sid = 'employment-' + re.search(r'post_(\d+)', url).group(1)
            source = dict(existing.get(url) or {'id': sid, 'related_ids': []}, url=url,
                selector=selector, kind='employment_information', topic='就业招聘与公共就业服务', status='pending')
            parsed = parse_html(raw, source, charset)
            status = age_status(parsed['published_at'], cutoff, as_of)
            if status != 'recent':
                report['results'].append({'url': url, 'status': status})
                continue
            if not TERMS.search(parsed['title']):
                report['results'].append({'url': url, 'status': 'unrelated'})
                continue
            title = parsed['title']
            source['kind'] = ('recruitment_result' if re.search('公示|成绩|录用|入围|拟聘', title)
                else 'recruitment_event' if re.search('招聘会|双选|招聘活动', title)
                else 'recruitment_notice' if re.search('招聘|招募', title)
                else 'employment_guide')
            result = save_snapshot(store, source, raw, parsed)
            existing[url] = source
            report['results'].append({'url': url, 'status': 'saved', 'title': title, **result})
        except StorageLimit:
            report['stopped'] = '10GB storage limit'
            break
        except (OSError, ValueError) as error:
            report['results'].append({'url': url, 'status': 'failed', 'error': type(error).__name__})
        write_json(root / 'outputs/employment-refresh-report.json', report)
    config['allowed_hosts'] = hosts
    config['sources'] = list(existing.values())
    write_json(config_path, config)
    report['database'] = build_database(store, current_documents(store))
    report['project_bytes'] = directory_bytes(root)
    write_json(root / 'outputs/employment-refresh-report.json', report)
    print(json.dumps({'archived': len(archived), 'saved': sum(r['status']=='saved' for r in report['results']), **report['database'], 'bytes': report['project_bytes']}, ensure_ascii=True))

if __name__ == '__main__':
    main()
