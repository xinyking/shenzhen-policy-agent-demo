"""Request-local state; never stores credentials or hidden model reasoning."""
from dataclasses import dataclass, field
from uuid import uuid4

@dataclass(frozen=True)
class RuntimePolicy:
    max_model_calls: int = 5
    max_tool_rounds: int = 2
    timeout_seconds: int = 120
    max_evidence: int = 8
    allowed_tools: tuple = ('search_documents', 'read_source')

    def __post_init__(self):
        if not (1 <= self.max_model_calls <= 5 and 0 <= self.max_tool_rounds <= 2
                and 1 <= self.timeout_seconds <= 120 and 1 <= self.max_evidence <= 8):
            raise ValueError('Runtime策略超出安全预算')
        if set(self.allowed_tools) - {'search_documents', 'read_source'}:
            raise ValueError('Runtime策略含未知工具')
        if 'search_documents' not in self.allowed_tools:
            raise ValueError('当前政策Runtime要求授权基础检索工具')

@dataclass
class RuntimeState:
    question: str
    context: str
    run_id: str = field(default_factory=lambda: uuid4().hex)
    phase: str = 'initializing'
    termination: str = ''
    rounds: int = 0
    next_id: int = 1
    evidence_registry: dict = field(default_factory=dict)
    executed: set = field(default_factory=set)
    result: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)
    skills: list = field(default_factory=list)

    def merge(self, hits, limit):
        merged = []
        for hit in hits:
            key = (hit['source_id'],hit['version'],hit['start'],hit['end'])
            if key not in self.evidence_registry:
                self.evidence_registry[key] = f'E{self.next_id}'
                self.next_id += 1
            merged.append({**hit, 'evidence_id': self.evidence_registry[key]})
        ids = {e['evidence_id'] for e in merged}
        self.result['sources'] = [*merged,*[e for e in self.result['sources'] if e['evidence_id'] not in ids]][:limit]
