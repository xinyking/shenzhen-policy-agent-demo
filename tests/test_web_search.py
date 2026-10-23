import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from app.tools.web_search import TavilySearch, SearchError, official_url
from app.agent.main_agent import PolicyGraph
from app.services.policy_service import PolicyService, Settings
from app.knowledge.ingestion import write_json
from test_ingestion import DataFixture
from test_runs import ScriptedModel, CLAIMS, REVIEW

class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_domain_restriction(self):
        for url in ['http://www.sz.gov.cn/a','https://sz.gov.cn.evil.test/a',
                    'https://user:pass@www.sz.gov.cn/a','https://www.sz.gov.cn:444/a']:
            self.assertFalse(official_url(url))
        self.assertTrue(official_url('https://hrss.sz.gov.cn/content/a.html'))

    async def test_missing_credentials_does_not_search(self):
        with patch.dict('os.environ',{'TAVILY_API_KEY':''}):
            search=TavilySearch(Path(__file__).parent/'not-present.json')
            self.assertFalse(search.configured)
            self.assertEqual(await search.search('测试'),[])

    async def test_summary_is_not_evidence_if_article_fails(self):
        search=TavilySearch(Path(__file__).parent/'not-present.json')
        with patch.object(search,'candidates',new=AsyncMock(return_value=['https://www.sz.gov.cn/a'])), \
             patch.object(search,'read_article',side_effect=ValueError('cannot parse')):
            self.assertEqual(await search.search('测试'),[])

class SearchGraphTests(DataFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp();self.snapshot()

    async def test_local_miss_uses_web_then_normal_generation_and_review(self):
        from app.knowledge.ingestion import current_documents
        doc=current_documents(self.store)[0]
        hit=dict(doc,source_id='web-test',id='web-test:excerpt',start=0,end=len(doc['text']),
            locator='官方网页节选',excerpt_sha256='test',last_checked_at='2026-10-18',last_check_status='success')
        service=PolicyService(Settings(self.store))
        model=ScriptedModel([CLAIMS,REVIEW])
        body=dict(question='硕士深圳政策',context='',mode='quick',top_k=5,use_memory=False,session_id=None)
        graph=PolicyGraph(service,body,'web-test',model)
        with patch.object(TavilySearch,'configured',new_callable=lambda:property(lambda s:True)), \
             patch.object(TavilySearch,'search',new=AsyncMock(return_value=[hit])) as search, \
             patch('app.agent.main_agent.answer_question',return_value={
                 'question':body['question'],'sources':[],'claims':[],'warnings':[],
                 'missing_information':[],'answer':'','status':'insufficient_evidence','mode_used':'no_evidence','checked_at':'2026-10-18'}):
            events=[]
            result=await graph.run(lambda e:events.append(e))
            self.assertEqual(result['status'],'answered')
            self.assertEqual(result['web_search']['source_count'],1)
            self.assertEqual(result['budget']['model_calls'],2)
            search.assert_awaited_once_with(body['question'])
            self.assertTrue(any(e['node']=='web_search' for e in events))

    async def test_offline_never_searches_even_with_key(self):
        graph=PolicyGraph(PolicyService(Settings(self.store)),dict(question='不存在的条目',context='',
            mode='offline',top_k=5,use_memory=False,session_id=None),'offline',None)
        with patch.object(TavilySearch,'search',new=AsyncMock()) as search:
            await graph.run(lambda e:None)
            search.assert_not_awaited()
