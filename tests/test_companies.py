from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from app.knowledge.companies import normalize_name, save_directory


class CompanyDirectoryTests(unittest.TestCase):
    def test_normalization_preserves_identity_without_fuzzy_merging(self):
        self.assertEqual(normalize_name('示例（深圳） 有限公司'), normalize_name('示例(深圳)有限公司'))
        self.assertNotEqual(normalize_name('示例有限公司'), normalize_name('示例股份有限公司'))

    def test_reimport_is_idempotent_and_keeps_publication_states(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            path=Path(directory)/'policies.sqlite3'
            base=dict(id='first',category='市级专精特新',cohort='2025',status='认定公示',
                url='https://www.sz.gov.cn/test.pdf',published_at='2026-01-09',sha256='test',
                rows=[dict(name='示例（深圳）有限公司',page=1,ordinal=1)])
            second={**base,'id':'second','status':'取消认定名单涉及',
                    'rows':[dict(name='示例(深圳)有限公司',page=3,ordinal=2)]}
            for _ in range(2):
                self.assertEqual(save_directory(path,[base,second]),{'companies':1,'recognitions':2})
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT recruitment_status FROM companies').fetchone()[0],'未核实招聘')
                self.assertEqual(set(r[0] for r in db.execute('SELECT status FROM company_recognitions')),
                                 {'认定公示','取消认定名单涉及'})
