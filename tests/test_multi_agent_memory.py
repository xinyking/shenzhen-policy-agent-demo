from unittest.mock import Mock
import unittest
from fastapi.testclient import TestClient
from app.api.server import create_app
from app.services.policy_service import Settings
from app.memory.personal_memory import PersonalMemory
from app.agent.subagents import SchedulePlanningAgent
from app.agent.llm import ModelError
from test_ingestion import DataFixture

class MultiAgentMemoryTests(DataFixture,unittest.TestCase):
    def setUp(self):
        super().setUp(); self.snapshot()
        self.factory=Mock(); self.model=self.factory.return_value
        self.client=self.enterContext(TestClient(create_app(Settings(self.store),self.factory)))
        self.headers={'X-Policy-Client':'workspace'}

    def test_three_model_specialists_and_draft_never_saved_automatically(self):
        self.model.generate.side_effect=[
            {'domains':['employment'],'intent':'plan'},
            {'action':'answer','arguments':{},'gaps':[]},
            {'claims':[{'text':'测试条件是硕士深圳','evidence_ids':['E1']}],'missing_information':[]},
            {'verdicts':[{'claim_index':0,'supported':True,'reason':'测试依据'}]},
            {'items':[{'title':'测试申请','evidence_ids':['E1'],'materials':[],'method':''}]}]
        r=self.client.post('/v1/ask',json={'question':'硕士深圳，请帮我创建申请日程','mode':'multi_agent'}).json()
        self.assertEqual(r['status'],'answered')
        self.assertEqual(r['multi_agent_budget']['model_calls'],5)
        self.assertEqual(r['routing']['method'],'classification_agent')
        self.assertTrue(r['schedule_drafts'][0]['requires_confirmation'])
        self.assertIsNone(r['schedule_drafts'][0]['date'])
        self.assertEqual(self.client.get('/v1/memory').json()['applications'],[])

    def test_profile_and_context_survive_instance_and_are_opt_in(self):
        self.client.put('/v1/memory',json={'profile':'用户确认：硕士'},headers=self.headers)
        memory=PersonalMemory(self.store.parent/'private/personal-memory.sqlite3')
        memory.remember('session-one','历史问题','历史答复')
        self.assertIn('硕士',memory.context('session-one'))
        self.assertNotIn('历史问题',memory.context('session-two'))
        self.model.generate.return_value={'claims':[{'text':'测试回答','evidence_ids':['E1']}],'missing_information':[]}
        self.client.post('/v1/ask',json={'question':'硕士深圳','mode':'llm'})
        self.assertNotIn('用户确认',self.model.generate.call_args.args[1]['context'])
        self.client.post('/v1/ask',json={'question':'硕士深圳','mode':'llm','use_memory':True,'session_id':'session-one'})
        self.assertIn('用户确认',self.model.generate.call_args.args[1]['context'])

    def test_information_query_does_not_plan_even_if_classifier_says_plan(self):
        self.model.generate.side_effect=[
            {'domains':['employment'],'intent':'plan'},
            {'action':'answer','arguments':{},'gaps':[]},
            {'claims':[{'title':'适用对象','text':'测试资料面向硕士和深圳就业人员。','evidence_ids':['E1']}],'missing_information':[]},
            {'verdicts':[{'claim_index':0,'supported':True,'reason':'测试原文支持'}]}]
        response=self.client.post('/v1/ask',json={'question':'硕士深圳补贴的适用对象和申请流程、材料是什么？','mode':'multi_agent'})
        self.assertEqual(response.status_code,200)
        result=response.json()
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['schedule_drafts'],[])
        self.assertEqual(result['routing']['intent'],'answer')
        self.assertEqual(self.model.generate.call_count,4)
        self.assertEqual(result['multi_agent_trace'][-1]['status'],'skipped_not_requested')
        self.assertIn('materials',result['routing']['information_needs'])

    def test_explicit_opt_out_prevents_schedule_agent(self):
        from app.agent.intent import wants_schedule
        for question in ('申请流程和材料是什么','怎么申请基金项目','不要创建日程，只说明申请材料','提醒的申请流程是什么'):
            self.assertFalse(wants_schedule(question),question)
        for question in ('帮我加到日程','请创建申请计划','这个项目提醒我','把它安排到日历'):
            self.assertTrue(wants_schedule(question),question)

    def test_generic_query_redirects_identity_clarification_into_answer(self):
        self.model.generate.side_effect=[
            {'domains':['employment'],'intent':'answer'},
            {'action':'clarify','arguments':{'question':'请提供您的学历和个人信息'},'gaps':[]},
            {'claims':[{'text':'测试政策面向硕士和深圳工作人员。','evidence_ids':['E1']}],'missing_information':[]},
            {'verdicts':[{'claim_index':0,'supported':True,'reason':'原文支持'}]}]
        result=self.client.post('/v1/ask',json={'question':'硕士深圳政策面向哪些人？','mode':'multi_agent'}).json()
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['missing_information'],[])
        self.assertTrue(any(event['event']=='clarification_redirected' for event in result['agent_trace']))

    def test_application_save_validation_clear_and_origin_protection(self):
        app={'id':'test-app','title':'测试政策','date':'2026-10-15','created':'test','applicationStatus':'submitted'}
        self.assertEqual(self.client.post('/v1/memory/applications',json=app).status_code,403)
        self.assertEqual(self.client.post('/v1/memory/applications',json=app,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/v1/memory').json()['applications'][0]['applicationStatus'],'submitted')
        self.assertEqual(self.client.post('/v1/memory/applications',json={**app,'date':'2026-99-99'},headers=self.headers).status_code,422)
        self.client.delete('/v1/memory',headers=self.headers)
        self.assertEqual(self.client.get('/v1/memory').json()['applications'],[])

    def test_material_quote_must_exist(self):
        model=Mock(); model.generate.return_value={'items':[{'title':'测试','evidence_ids':['E1'],
            'materials':[{'text':'伪造材料','evidence_id':'E1','quote':'并不存在的原文'}],'method':''}]}
        with self.assertRaises(ModelError):
            SchedulePlanningAgent().run(model,'测试',{'sources':[{'evidence_id':'E1','title':'测试','text':'真实正文'}],'claims':[]})
