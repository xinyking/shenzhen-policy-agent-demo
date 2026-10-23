"""Collection tests retained after removal of applicant rule modeling."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from app.evaluation.retrieval import evaluate_retrieval
from app.knowledge.ingestion import (OfficialFetcher, SafeRedirect, collect, current_documents,
    load_sources, parse_html, read_json, save_snapshot, search_documents, validate_url, write_json)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'id': 'test-source', 'url': 'https://hrss.sz.gov.cn/test.html', 'selector': '.news_cont_d_wrap',
          'kind': 'policy', 'topic': '虚构测试', 'related_ids': [], 'status': 'pending'}
RAW = (ROOT / 'tests/fixtures/article.html').read_bytes()


class DataFixture:
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temp.cleanup)
        self.root, self.store = Path(temp.name), Path(temp.name) / 'store'
        self.config = self.root / 'sources.json'
        write_json(self.config, {'schema_version': 1, 'allowed_hosts': ['hrss.sz.gov.cn'], 'sources': [SOURCE]})

    def snapshot(self, raw=RAW):
        return save_snapshot(self.store, SOURCE, raw, parse_html(raw, SOURCE), 'local_import')


class DataTests(DataFixture, unittest.TestCase):
    def test_source_config(self):
        self.assertEqual(load_sources(self.config)['sources'][0]['id'], 'test-source')
        payload = read_json(self.config)
        payload['sources'][0]['id'] = '../escape'
        write_json(self.config, payload)
        with self.assertRaises(ValueError):
            load_sources(self.config)

    def test_url_boundaries(self):
        for url in ('http://hrss.sz.gov.cn/a', 'https://hrss.sz.gov.cn.evil.com/a',
                    'https://user:pass@hrss.sz.gov.cn/a', 'https://hrss.sz.gov.cn:444/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url, ['hrss.sz.gov.cn'])

    def test_redirect_rejects_nonofficial_host(self):
        with self.assertRaises(ValueError):
            SafeRedirect(['hrss.sz.gov.cn']).redirect_request(None, None, 302, '', {}, 'https://example.com')

    def test_body_metadata_offsets_and_attachment(self):
        parsed = parse_html(RAW, SOURCE)
        self.assertEqual(parsed['title'], '虚构测试政策')
        self.assertNotIn('导航', parsed['text'])
        self.assertNotIn('脚本', parsed['text'])
        self.assertTrue(parsed['attachments'][0]['url'].endswith('test.pdf'))
        for span in parsed['spans']:
            self.assertEqual(parsed['text'][span['start']:span['end']], span['text'])

    def test_missing_body_rejected(self):
        with self.assertRaises(ValueError):
            parse_html(b'<html><body>error</body></html>', SOURCE)

    def test_same_content_no_new_version(self):
        self.assertTrue(self.snapshot()['changed'])
        self.assertFalse(self.snapshot()['changed'])
        self.assertEqual(len(list((self.store / 'documents/test-source').iterdir())), 1)

    def test_changed_content_retains_history(self):
        first = self.snapshot()
        second = self.snapshot(RAW.replace('硕士'.encode(), '博士'.encode()))
        self.assertNotEqual(first['version'], second['version'])
        self.assertEqual(len(list((self.store / 'documents/test-source').iterdir())), 2)

    def test_collect_failure_preserves_success(self):
        payload = read_json(self.config)
        payload['sources'].append({**SOURCE, 'id': 'other', 'url': 'https://hrss.sz.gov.cn/other.html'})
        write_json(self.config, payload)
        with patch('app.knowledge.ingestion.OfficialFetcher.fetch', side_effect=[(RAW, 'text/html', 'utf-8'), ValueError('blocked')]):
            results = collect(self.config, self.store)
        self.assertEqual([item['ok'] for item in results], [True, False])
        self.assertEqual(len(current_documents(self.store)), 1)

    def test_robots_disallow(self):
        fetcher = OfficialFetcher(['hrss.sz.gov.cn'])
        with patch.object(fetcher, 'request', return_value=(b'User-agent: *\nDisallow: /', 'text/plain', 'utf-8')):
            with self.assertRaises(ValueError):
                fetcher.fetch(SOURCE['url'])

    def test_robots_403_blocks_collection(self):
        fetcher = OfficialFetcher(['hrss.sz.gov.cn'])
        with patch.object(fetcher, 'request', side_effect=HTTPError(SOURCE['url'], 403, 'denied', {}, None)):
            with self.assertRaises(ValueError):
                fetcher.fetch(SOURCE['url'])

    def test_search_and_no_hit(self):
        self.snapshot()
        self.assertTrue(search_documents(self.store, '硕士工作城市深圳'))
        self.assertEqual(search_documents(self.store, '火星探测'), [])

    def test_archived_source_excluded(self):
        self.snapshot()
        config = read_json(self.config)
        config['sources'][0]['status'] = 'archived'
        write_json(self.config, config)
        collect(self.config, self.store)
        self.assertEqual(current_documents(self.store), [])

    def test_failed_refresh_excluded_for_answers(self):
        self.snapshot()
        with patch('app.knowledge.ingestion.OfficialFetcher.fetch', side_effect=ValueError('blocked')):
            collect(self.config, self.store)
        self.assertTrue(search_documents(self.store, '硕士'))
        self.assertEqual(search_documents(self.store, '硕士', successful_only=True), [])

    def test_snapshot_integrity(self):
        snapshot = self.snapshot()
        (self.store / 'documents/test-source' / snapshot['version'] / 'raw.html').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            current_documents(self.store)

    def test_evaluation_missing_sources(self):
        self.snapshot()
        path = self.root / 'eval.json'
        write_json(path, {'schema_version': 1, 'cases': [{'query': '硕士', 'expected_sources': ['test-source', 'missing']}]})
        result = evaluate_retrieval(self.store, path)
        self.assertEqual(result['mean_recall'], .5)
        self.assertEqual(result['cases'][0]['missing_from_store'], ['missing'])
