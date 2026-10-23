"""Scripted-model tests for actual tool actions, repairs and stopping conditions."""
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.agent.legacy.agent import Budget, BudgetExceeded, validate_decision, review_claims
from app.api.server import create_app
from app.knowledge.ingestion import read_json, write_json
from app.agent.llm import ModelError
from app.services.policy_service import Settings
from app.tools.policy_tools import PolicyTools, ToolError
from test_ingestion import DataFixture


def decision(action='answer', arguments=None, gaps=None):
    return {'action': action, 'arguments': arguments or {}, 'gaps': gaps or []}


def answer(text='测试文件要求硕士并在深圳工作。', citation='E1'):
    return {'claims': [{'text': text, 'evidence_ids': [citation]}], 'missing_information': []}


def review(supported=True):
    return {'verdicts': [{'claim_index': 0, 'supported': supported, 'reason': '引用原文核对结果'}]}


class AgenticTests(DataFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.snapshot()
        self.factory = Mock()
        self.model = self.factory.return_value
        self.client = self.enterContext(TestClient(create_app(Settings(self.store), self.factory)))

    def ask(self, question='硕士工作城市深圳'):
        return self.client.post('/v1/ask', json={'question': question, 'mode': 'agent'})

    def test_direct_answer_is_reviewed(self):
        self.model.generate.side_effect = [decision(), answer(), review()]
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['mode_used'], 'agent')
        self.assertEqual(result['verification']['status'], 'supported')
        self.assertEqual(result['budget']['model_calls'], 3)
        self.assertEqual([e['event'] for e in result['agent_trace']], ['tool', 'decision', 'generate', 'review'])

    def test_empty_seed_can_be_corrected_by_query_rewrite(self):
        self.model.generate.side_effect = [decision('search_documents', {'query': '硕士工作城市深圳'}, ['需检索人员条件']),
                                           decision(), answer(), review()]
        result = self.ask('本科补贴').json()
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['agent_trace'][0]['results_count'], 0)
        self.assertEqual(result['budget']['correction_rounds'], 1)
        self.assertEqual(result['budget']['model_calls'], 4)
        self.assertEqual(result['agent_trace'][2]['arguments']['query'], '硕士工作城市深圳')

    def test_reads_named_section_and_reviewer_gets_only_cited_evidence(self):
        self.model.generate.side_effect = [decision('read_source', {'source_id': 'test-source', 'section': '测试材料'}),
                                           decision(), answer('测试材料是学历及就业证明。', 'E2'), review()]
        result = self.ask('硕士').json()
        self.assertEqual(result['mode_used'], 'agent')
        self.assertEqual(result['agent_trace'][2]['tool'], 'read_source')
        self.assertIn('材料', result['sources'][0]['text'])
        payload = self.model.generate.call_args_list[-1].args[1]
        self.assertEqual(len(payload['claims'][0]['cited_evidence']), 1)
        self.assertEqual(payload['claims'][0]['cited_evidence'][0]['evidence_id'], 'E2')

    def test_two_correction_rounds_then_stops(self):
        self.model.generate.side_effect = [decision('read_source', {'source_id': 'test-source', 'section': section}, ['还缺材料'])
                                           for section in ('测试材料', '申请人为', '测试附件')]
        result = self.ask().json()
        self.assertEqual(result['budget']['correction_rounds'], 2)
        self.assertEqual(result['budget']['model_calls'], 3)
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertEqual(result['agent_trace'][-1]['reason'], 'tool_budget')

    def test_duplicate_tool_action_is_not_executed_again(self):
        self.model.generate.side_effect = [decision('search_documents', {'query': '硕士工作城市深圳'}), decision(), answer(), review()]
        result = self.ask().json()
        self.assertTrue(any(e['event'] == 'tool_error' for e in result['agent_trace']))
        self.assertEqual(len([e for e in result['agent_trace'] if e['event'] == 'tool']), 1)

    def test_clarification_does_not_generate_answer(self):
        self.model.generate.side_effect = [decision('clarify', {'question': '你具体咨询哪项补贴？'})]
        result = self.ask().json()
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['budget']['model_calls'], 1)

    def test_unsupported_claim_is_removed(self):
        self.model.generate.side_effect = [decision(), answer('可以领取99999999元。'), review(False)]
        result = self.ask().json()
        self.assertNotIn('99999999', result['answer'])
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['verification']['removed'], 1)
        self.assertEqual(result['status'], 'insufficient_evidence')

    def test_partial_repair_keeps_supported_claim_only(self):
        generated = answer()
        generated['claims'].append({'text': '错误的额外金额999999。', 'evidence_ids': ['E1']})
        verdicts = {'verdicts': [{'claim_index': i, 'supported': i == 0, 'reason': '核对结果'} for i in range(2)]}
        self.model.generate.side_effect = [decision(), generated, verdicts]
        result = self.ask().json()
        self.assertEqual(len(result['claims']), 1)
        self.assertNotIn('999999', result['answer'])
        self.assertEqual(result['verification']['status'], 'repaired')

    def test_review_failure_falls_back_without_unreviewed_text(self):
        self.model.generate.side_effect = [decision(), answer('UNREVIEWED-CONTENT'), ModelError('评审不可用')]
        result = self.ask().json()
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertNotIn('UNREVIEWED-CONTENT', result['answer'])

    def test_incomplete_review_is_rejected(self):
        self.model.generate.side_effect = [decision(), answer('UNREVIEWED'), {'verdicts': []}]
        result = self.ask().json()
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertNotIn('UNREVIEWED', result['answer'])

    def test_bad_citation_never_reaches_review(self):
        self.model.generate.side_effect = [decision(), answer('BAD-CITATION', 'E999')]
        result = self.ask().json()
        self.assertEqual(result['budget']['model_calls'], 2)
        self.assertNotIn('BAD-CITATION', result['answer'])

    def test_answer_without_evidence_is_blocked(self):
        self.model.generate.side_effect = [decision()]
        result = self.ask('火星探测').json()
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertEqual(result['budget']['model_calls'], 1)

    def test_invalid_model_action_falls_back(self):
        self.model.generate.side_effect = [decision('run_shell', {'command': 'anything'})]
        self.assertEqual(self.ask().json()['mode_used'], 'extractive')

    def test_invalid_tool_arguments_are_recoverable(self):
        self.model.generate.side_effect = [decision('read_source', {'source_id': '../../private', 'section': '材料'}),
                                           decision(), answer(), review()]
        result = self.ask().json()
        self.assertEqual(result['mode_used'], 'agent')
        self.assertTrue(any(e['event'] == 'tool_error' for e in result['agent_trace']))

    def test_no_model_configuration_preserves_offline_evidence(self):
        from app.agent.legacy.agent import agent_answer
        with patch.dict(os.environ, {}, clear=True):
            result = agent_answer(self.store, '硕士')
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertEqual(result['budget']['model_calls'], 0)

    def test_source_changes_during_research_block_result(self):
        def mutate(*args, **kwargs):
            manifest = read_json(self.store / 'manifest.json')
            manifest['documents']['test-source']['last_check_status'] = 'failed'
            write_json(self.store / 'manifest.json', manifest)
            raise ModelError('模拟调用失败')
        self.model.generate.side_effect = mutate
        self.assertEqual(self.ask().status_code, 503)


class ProtocolTests(unittest.TestCase):
    def test_invalid_decisions(self):
        for value in ({}, decision([]), decision('answer', {'extra': True}), decision('clarify'),
                      decision(gaps=['x'] * 4)):
            with self.subTest(value=value), self.assertRaises(ModelError):
                validate_decision(value)

    def test_duplicate_or_nonboolean_review_entries_rejected(self):
        for verdicts in ([{'claim_index': 0, 'supported': 1, 'reason': 'bad'}],
                         [{'claim_index': 0, 'supported': True, 'reason': 'ok'}] * 2):
            with self.subTest(verdicts=verdicts), self.assertRaises(ModelError):
                review_claims({'verdicts': verdicts}, answer()['claims'])

    def test_model_call_budget(self):
        model, budget = Mock(), Budget(max_calls=1)
        budget.call(model, '', {})
        with self.assertRaises(BudgetExceeded):
            budget.call(model, '', {})
        self.assertEqual(model.generate.call_count, 1)

    def test_deadline_before_and_after_model_call(self):
        with self.assertRaises(BudgetExceeded):
            Budget(seconds=0, clock=lambda: 0).call(Mock(), '', {})
        clock = Mock(side_effect=[0, 1, 121])
        with self.assertRaises(BudgetExceeded):
            Budget(clock=clock).call(Mock(), '', {})

    def test_tools_reject_arbitrary_urls_and_paths(self):
        tools = PolicyTools(None, '问题', 5)
        for name, args in [('fetch_url', {'url': 'https://evil.example'}),
                           ('read_source', {'source_id': '../file', 'section': '材料'}),
                           ('search_documents', {'query': '搜索', 'path': '../file'})]:
            with self.subTest(name=name), self.assertRaises(ToolError):
                tools.execute(name, args)
