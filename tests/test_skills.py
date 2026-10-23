import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from app.skills.loader import load_skill, instructions
from app.skills.selector import select_skills
from app.agent.state import RuntimePolicy, RuntimeState
from app.agent.context import ContextBuilder


class SkillTests(unittest.TestCase):
    def test_windows_newlines_are_supported(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            root=Path(folder);directory=root/'policy_research';directory.mkdir()
            (directory/'SKILL.md').write_bytes(b'# policy_research\r\n\r\nTrusted instructions.\r\n')
            with patch('app.skills.loader.ROOT',root):
                self.assertEqual(load_skill('policy_research')['instructions'],'Trusted instructions.')

    def test_question_selects_only_relevant_guides(self):
        cases=[('居住证怎样续签',['policy_research']),
               ('推荐深圳专精特新企业',['policy_research','company_discovery']),
               ('帮我添加日程',['policy_research','application_planning'])]
        for question,expected in cases:
            skills,missing=select_skills(question)
            self.assertEqual([s['name'] for s in skills],expected)
            self.assertEqual(missing,[])
        self.assertEqual(select_skills('推荐企业',offline=True),([],[]))

    def test_skill_cannot_grant_tool_authority(self):
        before=RuntimePolicy().allowed_tools
        self.assertIn('不是政策证据',instructions(select_skills('推荐企业')[0]))
        self.assertEqual(RuntimePolicy().allowed_tools,before)
        with self.assertRaises(ValueError):
            RuntimePolicy(allowed_tools=('search_documents','web_search'))
        with self.assertRaises(ValueError):
            load_skill('../../private/model-config.json')

    def test_missing_skill_degrades_without_stopping_answer(self):
        with patch('app.skills.selector.load_skill',side_effect=OSError):
            self.assertEqual(select_skills('医保'),([],['policy_research']))

    def test_research_metadata_is_separate_from_evidence(self):
        state=RuntimeState('推荐专精特新企业','')
        state.skills,_=select_skills(state.question)
        state.result={'sources':[],'agent_trace':[]}
        class Registry:
            def __init__(self): self.tools=self
            def index(self): return []
            def manifest(self): return []
        payload=ContextBuilder().research(state,Registry(),RuntimePolicy())
        self.assertEqual(len(payload['selected_skills']),2)
        self.assertEqual(payload['evidence'],[])
        self.assertTrue(all(len(s['version'])==12 for s in payload['selected_skills']))
