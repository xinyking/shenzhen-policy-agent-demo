import unittest
from app.knowledge.evidence import evidence_payload, current_time
from datetime import datetime

class PolicyContextTests(unittest.TestCase):
    def test_compact_review_deduplicates_originals_and_keeps_per_claim_citations(self):
        import json
        from app.agent.context import ContextBuilder
        from app.agent.state import RuntimeState
        state = RuntimeState('测试问题', '')
        original = {'title': '虚构测试', 'text': '原文' * 1000, 'source_url': 'https://www.sz.gov.cn/test',
                    'published_at': None, 'last_checked_at': 'test'}
        state.result = {'sources': [{**original, 'evidence_id': key} for key in ('E1', 'E2', 'E3')]}
        claims = [{'text': '测试要点', 'evidence_ids': ['E1']} for _ in range(4)] + [{'text': '第二主题', 'evidence_ids': ['E2']}]
        builder = ContextBuilder()
        compact = builder.compact_review(state, claims)
        self.assertEqual([e['evidence_id'] for e in compact['cited_evidence']], ['E1', 'E2'])
        self.assertEqual(compact['claims'][0]['evidence_ids'], ['E1'])
        self.assertEqual(compact['claims'][-1]['evidence_ids'], ['E2'])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(builder.review(state, claims))) / 2)

    def test_unparsed_attachment_is_not_presented_as_full_text(self):
        evidence={'evidence_id':'E1','title':'测试通知','text':'仅通知正文',
                  'source_url':'https://www.sz.gov.cn/notice.html','published_at':None,
                  'last_checked_at':'test','attachments':[{'title':'申请指南.doc','url':'https://www.sz.gov.cn/guide.doc'}]}
        payload=evidence_payload([evidence])[0]
        self.assertFalse(payload['attachment_text_available'])
        self.assertEqual(payload['attachments'],evidence['attachments'])
        self.assertEqual(payload['text'],'仅通知正文')

    def test_time_has_explicit_hong_kong_offset(self):
        self.assertEqual(datetime.fromisoformat(current_time()).utcoffset().total_seconds(),28800)
