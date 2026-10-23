"""Read-only service for policy evidence and question answering."""
from dataclasses import dataclass
from pathlib import Path

from app.knowledge.ingestion import current_documents, search_documents
from app.agent.llm import JsonModel
from app.agent.answer import answer_question
from app.knowledge.domains import route, document_domains
from app.memory.personal_memory import PersonalMemory


class ServiceUnavailable(Exception):
    pass


@dataclass
class Settings:
    store: Path


class PolicyService:
    def __init__(self, settings, model_factory=JsonModel):
        self.settings, self.model_factory = settings, model_factory

    def documents(self):
        if not (self.settings.store / 'manifest.json').is_file():
            raise ServiceUnavailable('尚未采集政策原文，请先执行collect')
        return current_documents(self.settings.store)

    def sources(self):
        return [{key: doc[key] for key in ('source_id', 'title', 'source_url', 'version',
                 'kind', 'topic', 'published_at', 'last_check_status', 'last_checked_at')}
                | {'paragraphs': len(doc['spans']), 'domains': document_domains(doc)} for doc in self.documents()]

    def search(self, query, top_k=5):
        self.documents()
        return {'query': query, 'hits': search_documents(self.settings.store, query, top_k)}

    def ask(self, question, context='', top_k=5, mode='offline', use_memory=False, session_id=None):
        self.documents()
        memory=PersonalMemory(self.settings.store.parent / 'private/personal-memory.sqlite3')
        remembered=memory.context(session_id) if use_memory else ''
        memory_used=bool(remembered and len(context)<1000)
        if use_memory:
            context=(context+('\n以下是可过期的用户记忆，本次明确背景优先：\n'+remembered if remembered else ''))[:1000]
        if mode == 'multi_agent':
            from app.agent.legacy.supervisor import PolicySupervisor
            result=PolicySupervisor(self.settings.store,self.model_factory).run(question,context,top_k)
        elif mode == 'agent':
            from app.agent.legacy.runtime import AgentRuntime
            result = AgentRuntime(self.settings.store, self.model_factory).run(question, context, top_k)
        else:
            result = answer_question(self.settings.store, question, context, top_k, mode, self.model_factory)
        result.setdefault('routing',route(question))
        result['memory_used']=memory_used
        if use_memory: memory.remember(session_id,question,result['answer'])
        return result
