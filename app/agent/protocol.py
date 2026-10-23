"""Role instructions, budget and structured response contracts."""
from app.agent.prompts import RESEARCH, REVIEW
import time
from app.agent.llm import ModelError
from app.knowledge.ingestion import current_documents




class BudgetExceeded(ModelError):
    pass


class Budget:
    def __init__(self, max_calls=5, seconds=120, clock=time.monotonic):
        self.calls, self.max_calls, self.clock = 0, max_calls, clock
        self.deadline = clock() + seconds

    def call(self, model, instruction, data):
        remaining = self.deadline - self.clock()
        if self.calls >= self.max_calls or remaining <= 0:
            raise BudgetExceeded('Agent模型调用预算已用尽')
        self.calls += 1
        result = model.generate(instruction, data, timeout=min(45, remaining))
        if self.clock() > self.deadline:
            raise BudgetExceeded('Agent时间预算已用尽')
        return result


def validate_decision(value):
    if not isinstance(value, dict) or set(value) != {'action', 'arguments', 'gaps'}:
        raise ModelError('研究Agent返回无效决策')
    if not isinstance(value['action'], str) or value['action'] not in {'answer', 'clarify', 'search_documents', 'read_source'} or not isinstance(value['arguments'], dict):
        raise ModelError('研究Agent返回未允许的动作')
    gaps = value['gaps']
    if not isinstance(gaps, list) or len(gaps) > 3 or not all(isinstance(g, str) and 0 < len(g.strip()) <= 200 for g in gaps):
        raise ModelError('研究Agent缺口格式无效')
    if value['action'] == 'answer' and value['arguments']:
        raise ModelError('answer动作不允许参数')
    if value['action'] == 'clarify':
        args = value['arguments']
        if set(args) != {'question'} or not isinstance(args['question'], str) or not 0 < len(args['question'].strip()) <= 200:
            raise ModelError('研究Agent追问格式无效')
    return value


def review_claims(value, claims):
    if not isinstance(value, dict) or set(value) != {'verdicts'} or not isinstance(value['verdicts'], list):
        raise ModelError('评审Agent返回格式无效')
    verdicts = value['verdicts']
    if len(verdicts) != len(claims):
        raise ModelError('评审遗漏回答要点')
    seen, supported = set(), set()
    for item in verdicts:
        if (not isinstance(item, dict) or set(item) != {'claim_index', 'supported', 'reason'}
                or type(item['claim_index']) is not int or not 0 <= item['claim_index'] < len(claims)
                or item['claim_index'] in seen or type(item['supported']) is not bool
                or not isinstance(item['reason'], str) or not 0 < len(item['reason'].strip()) <= 200):
            raise ModelError('评审条目无效或重复')
        seen.add(item['claim_index'])
        if item['supported']:
            supported.add(item['claim_index'])
    return [claim for index, claim in enumerate(claims) if index in supported]


def ensure_current(store, evidence):
    docs = {doc['source_id']: doc for doc in current_documents(store)}
    if any(item['source_id'] not in docs or docs[item['source_id']]['version'] != item['version']
           or docs[item['source_id']]['last_check_status'] != 'success' for item in evidence):
        raise ValueError('研究期间来源变化，请重新提问')


