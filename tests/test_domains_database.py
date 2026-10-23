import sqlite3
import unittest
from contextlib import closing
from app.knowledge.domains import route
from app.knowledge.database import build_database, database_spans
from app.knowledge.ingestion import current_documents, search_documents
from test_ingestion import DataFixture

class DomainDatabaseTests(DataFixture, unittest.TestCase):
    def test_multi_domain_and_unknown(self):
        self.assertEqual({d['id'] for d in route('毕业来深就业，租房和医保怎么办')['domains']},
                         {'employment','housing','insurance'})
        self.assertTrue(route('火星天气')['unknown'])

    def test_livelihood_routes(self):
        for question, expected in [('居住证如何签注','residency'),('少儿医保缴费','insurance'),('高龄津贴怎么办','care'),('公积金租房提取','housing')]:
            self.assertIn(expected,{d['id'] for d in route(question)['domains']})

    def test_sqlite_index_and_tamper_fallback(self):
        self.snapshot()
        docs = current_documents(self.store)
        self.assertEqual(build_database(self.store, docs)['documents'], 1)
        self.assertIsNotNone(database_spans(self.store, docs))
        with closing(sqlite3.connect(self.store / 'policies.sqlite3')) as db, db:
            db.execute("UPDATE paragraphs SET text='tampered'")
        self.assertIsNone(database_spans(self.store, docs))
        self.assertNotIn('tampered', str(search_documents(self.store, '硕士深圳')))

    def test_hybrid_scores_are_not_probabilities(self):
        self.snapshot()
        hits = search_documents(self.store, '硕士深圳')
        self.assertTrue(hits)
        self.assertEqual(hits[0]['retrieval_method'], 'lexical_bm25_rrf')
        self.assertIn('bm25_score', hits[0])
