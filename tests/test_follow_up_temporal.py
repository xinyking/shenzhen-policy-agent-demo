import unittest
from unittest.mock import patch
from app.knowledge.hybrid import temporal_weight, rerank
from app.agent.follow_up import build_follow_up
from app.agent.answer import validate_answer
from app.agent.context import ContextBuilder
from app.agent.state import RuntimeState


class FollowUpTemporalTests(unittest.TestCase):
    def test_cohort_year_beats_publication_and_explicit_history_is_preserved(self):
        annual = {'title': '2026届毕业生补贴通知', 'published_at': '2025-10-11'}
        old = {'title': '就业补贴办法', 'published_at': '2023-12-29'}
        with patch('app.knowledge.hybrid.current_time', return_value='2026-10-18T00:00:00+08:00'):
            self.assertGreater(temporal_weight(annual, '就业补贴'), temporal_weight(old, '就业补贴'))
        self.assertGreater(temporal_weight(old, '2023年就业补贴'), temporal_weight(annual, '2023年就业补贴'))
        self.assertEqual(temporal_weight({'title':'办法','published_at':None}, '2026年补贴'), 1)

    def test_ranking_uses_annual_title_without_deleting_old_rules(self):
        common = {'text': '毕业就业补贴', 'score': 3}
        hits = [{**common,'id':'a','title':'就业补贴清单','published_at':'2021-09-17'},
                {**common,'id':'b','title':'2026届毕业就业补贴','published_at':'2025-10-11'}]
        ranked = rerank(hits, '2026年毕业就业补贴')
        self.assertEqual(ranked[0]['id'], 'b')
        self.assertEqual(len(ranked), 2)

    def test_follow_up_skips_known_fields_and_sensitive_model_question(self):
        route = {'domains':[{'id':'employment'}]}
        value = build_follow_up(route, '毕业时间：毕业2年内；就业状态：已入职', '请提供身份证号')
        self.assertEqual(value['fields'], ['深圳社保'])
        self.assertNotIn('身份证', value['question'])
        mismatch = build_follow_up(route, '毕业时间：毕业2年内；就业状态：已入职', '你按单位还是个人身份缴纳？')
        self.assertIn('深圳社保', mismatch['question'])
        self.assertIsNone(build_follow_up(route, '毕业时间：在读；就业状态：待业；深圳社保：未缴纳'))

    def test_optional_model_follow_up_does_not_require_fake_citation(self):
        answer = {'claims':[{'text':'原文','evidence_ids':['E1']}], 'missing_information':[], 'follow_up':'你目前的就业状态是？'}
        self.assertEqual(validate_answer(answer,[{'evidence_id':'E1'}])['follow_up'], answer['follow_up'])

    def test_review_keeps_full_original_but_drops_unneeded_transport_metadata(self):
        state = RuntimeState('问题','')
        state.result={'sources':[{'evidence_id':'E1','title':'2026通知','text':'原文'*100,'published_at':'2025-10-11', 'attachments':['large-url'], 'source_url':'https://www.sz.gov.cn/a'}]}
        result=ContextBuilder().compact_review(state,[{'text':'答复','evidence_ids':['E1']}])
        self.assertEqual(result['cited_evidence'][0]['text'],'原文'*100)
        self.assertNotIn('attachments',result['cited_evidence'][0])
