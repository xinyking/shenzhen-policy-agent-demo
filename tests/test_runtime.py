import unittest
from unittest.mock import Mock
from app.agent.legacy.runtime import AgentRuntime
from app.agent.state import RuntimePolicy, RuntimeState
from app.agent.context import ContextBuilder
from app.tools.registry import ToolRegistry
from app.tools.policy_tools import ToolError
from test_ingestion import DataFixture

class RuntimeTests(DataFixture, unittest.TestCase):
    def test_permission_check_precedes_tool_execution(self):
        tools = Mock()
        registry = ToolRegistry(tools, RuntimePolicy(allowed_tools=('search_documents',)))
        with self.assertRaises(ToolError):
            registry.execute('read_source', {'source_id':'test-source','section':'条件'})
        tools.execute.assert_not_called()
        self.assertEqual([s['name'] for s in registry.manifest()], ['search_documents'])

    def test_policy_rejects_unbounded_budget_and_unknown_tools(self):
        for options in ({'max_model_calls': 100}, {'max_tool_rounds':3}, {'allowed_tools':('shell',)}):
            with self.assertRaises(ValueError): RuntimePolicy(**options)

    def test_clarification_has_explicit_termination_and_request_local_state(self):
        self.snapshot()
        factory = Mock()
        factory.return_value.generate.return_value = {'action':'clarify','arguments':{'question':'请提供具体政策名称'},'gaps':[]}
        runtime = AgentRuntime(self.store, factory)
        result = runtime.run('硕士深圳')
        self.assertEqual(result['runtime']['termination'], 'needs_user_input')
        self.assertEqual(runtime.state.phase, 'finished')
        self.assertEqual(result['budget']['model_calls'],1)
        other = AgentRuntime(self.store, factory).run('硕士深圳')
        self.assertNotEqual(result['runtime']['run_id'],other['runtime']['run_id'])

    def test_review_context_contains_only_cited_evidence(self):
        state = RuntimeState('测试','')
        def evidence(id): return dict(evidence_id=id,title='测试',text='证据',source_url='https://www.sz.gov.cn/',published_at=None,last_checked_at='test')
        state.result = {'sources':[evidence('E1'),evidence('E2')]}
        payload = ContextBuilder().review(state,[{'text':'说明','evidence_ids':['E2']}])
        self.assertEqual([e['evidence_id'] for e in payload['claims'][0]['cited_evidence']],['E2'])
