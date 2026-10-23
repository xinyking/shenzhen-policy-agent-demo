"""Request-local LangGraph workflow. Nodes perform real work, not a wrapped legacy run."""
import asyncio
import inspect
import json
import time
from app.agent.follow_up import follow_up_fields, build_follow_up, KNOWN
from app.knowledge.hybrid import temporal_weight
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context

from app.agent.async_llm import adapt_model
from app.agent.protocol import RESEARCH, REVIEW, BudgetExceeded, ensure_current, validate_decision, review_claims
from app.agent.context import ContextBuilder
from app.knowledge.domains import route
from app.agent.llm import ModelError
from app.memory.personal_memory import PersonalMemory
from app.agent.answer import INSTRUCTION, answer_question, validate_answer, render_answer
from app.agent.intent import wants_schedule, information_needs, asks_for_identity, personal_advice_requested
from app.agent.state import RuntimePolicy, RuntimeState
from app.agent.subagents import PolicyClassificationAgent, SchedulePlanningAgent
from app.tools.registry import ToolRegistry
from app.tools.web_search import TavilySearch, SearchError
from app.skills.selector import select_skills
from app.skills.loader import instructions as skill_instructions
from app.tools.policy_tools import PolicyTools, ToolError
from app.knowledge.evidence import current_time, evidence_payload
from app.tools.screenshot_tools import READ_SCREENSHOT, COMPARE_MATERIALS, validate_recognition, validate_comparison


class GraphState(TypedDict, total=False):
    question: str
    context: str
    use_memory: bool
    session_id: str | None
    memory_used: bool
    catalog_count: int
    work: RuntimeState
    next_node: str
    result: dict
    screenshot_analysis: dict


class PolicyGraph:
    def __init__(self, service, body, run_id, model, screenshot=None):
        self.service, self.body, self.run_id = service, body, run_id
        self.store, self.mode = service.settings.store, body['mode']
        self.model = adapt_model(model) if model is not None else None
        self.calls, self.research_calls, self.attempts = 0, 0, 0
        self.decision_repairs = 0
        self.screenshot = screenshot
        self.has_screenshot = bool(body.get('screenshot_id'))
        self.max_calls = 7 if self.mode == 'multi_agent' or self.has_screenshot else 5 if self.mode in ('agent', 'quick') else 2
        self.seconds = 180 if self.mode == 'multi_agent' or self.has_screenshot else 120
        self.deadline = time.monotonic() + self.seconds
        self.research_deadline = None
        self.lock = asyncio.Lock()
        self.builder, self.policy = ContextBuilder(), RuntimePolicy()
        self.registry = ToolRegistry(PolicyTools(self.store, body['question'], body['top_k']), self.policy)
        self.web_searcher = TavilySearch(self.store.parent / 'private/search-config.json')
        self.web_evidence = {}
        self.memory = PersonalMemory(self.store.parent / 'private/personal-memory.sqlite3')
        self.warnings = []
        self.graph = self.build()

    async def call(self, instruction, data, timeout=45, research=False, completion=False, image=None):
        async with self.lock:
            remaining = self.deadline - time.monotonic()
            if research and not completion and self.research_deadline is not None:
                remaining = min(remaining, self.research_deadline - time.monotonic())
            if remaining <= 0:
                raise BudgetExceeded('模型执行时间预算已用尽')
            if self.calls >= self.max_calls:
                raise BudgetExceeded('模型调用次数已达到本轮上限')
            if self.model is None:
                raise ModelError('没有可用模型配置')
            self.calls += 1
            if research:
                self.research_calls += 1
        try:
            async with asyncio.timeout(min(timeout, remaining)):
                if image is not None:
                    if not inspect.iscoroutinefunction(getattr(self.model, 'generate_image', None)):
                        raise ModelError('当前模型适配器不支持图片，请选择视觉模型或重新上传清晰截图')
                    return await self.model.generate_image(instruction, data, image['raw'], image['mime'],
                                                           model_name=self.body.get('vision_model', ''), timeout=min(timeout, remaining))
                if inspect.iscoroutinefunction(self.model.generate):
                    return await self.model.generate(instruction, data, timeout=min(timeout, remaining))
                # Only injected legacy test/integration models use threads, never the production HTTP adapter.
                return await asyncio.to_thread(self.model.generate, instruction, data, timeout=min(timeout, remaining))
        except TimeoutError:
            raise ModelError('模型调用超时') from None

    def node(self, name, function):
        async def execute(state):
            writer = get_stream_writer()
            writer({'type': 'node_started', 'node': name, 'data': {}})
            started = time.monotonic()
            try:
                update = await function(state)
            except ModelError as exc:
                if name == 'read_screenshot':
                    writer({'type': 'node_failed', 'node': name, 'data': {'reason': 'vision_unavailable'}})
                    return {'screenshot_analysis': {'status': 'unavailable', 'message': str(exc), 'materials': [], 'dates': [], 'gaps': []}}
                if name == 'compare_materials':
                    screenshot = state['screenshot_analysis']
                    screenshot['comparison_status'] = 'unavailable'
                    screenshot['gaps'] = [*screenshot['gaps'], '政策对照未完成，材料仍需人工核实。'][:5]
                    writer({'type': 'fallback_used', 'node': name, 'data': {'reason': 'comparison_unavailable'}})
                    return {'screenshot_analysis': screenshot, 'next_node': 'finish'}
                work = state['work']
                code = ('time_limit' if isinstance(exc, BudgetExceeded) and '时间' in str(exc) else
                        'call_limit' if isinstance(exc, BudgetExceeded) else
                        'timeout' if '超时' in str(exc) or 'Timeout' in str(exc) else 'model_or_output')
                writer({'type': 'node_failed', 'node': name, 'data': {'reason': code}})
                # A research decision is not the answer. Valid local evidence can still support generation.
                if name == 'decide' and work.result['sources'] and self.max_calls - self.calls >= 2 and self.remaining_research() > 10:
                    work.result['warnings'].append('研究决策未成功，已停止补查并基于现有证据整理答复。')
                    update = {'work': work, 'next_node': 'generate'}
                else:
                    work.termination = 'budget_exceeded' if isinstance(exc, BudgetExceeded) else 'model_failed'
                    work.result['failure'] = {'stage': name, 'code': code, 'message': str(exc)}
                    work.result['warnings'].append(str(exc) + '；保留可用原文，不展示未通过检查的回答。')
                    work.result['verification'] = {'status': 'not_completed', 'checked': 0, 'removed': 0}
                    update = {'work': work, 'next_node': 'fallback'}
            except Exception:
                writer({'type': 'node_failed', 'node': name, 'data': {'reason': 'data_or_execution'}})
                raise
            else:
                data = {'elapsed_ms': round((time.monotonic() - started) * 1000)}
                if name in ('retrieve', 'web_search'):
                    data['source_count'] = len(update['work'].result['sources'])
                writer({'type': 'node_completed', 'node': name, 'data': data})
            return update
        return execute

    def build(self):
        graph = StateGraph(GraphState)
        nodes = {'check_store': self.check_store, 'load_memory': self.load_memory,
                 'read_screenshot': self.read_screenshot, 'prepare_evidence': self.prepare_evidence,
                 'classify': self.classify, 'retrieve': self.retrieve, 'web_search': self.web_search, 'decide': self.decide,
                 'tool': self.tool, 'generate': self.generate, 'review': self.review,
                 'schedule': self.schedule, 'compare_materials': self.compare_materials,
                 'fallback': self.fallback, 'finish': self.finish}
        for name, fn in nodes.items():
            graph.add_node(name, self.node(name, fn))
        # Independent preparation nodes really execute in the same graph superstep.
        graph.add_edge(START, 'check_store')
        graph.add_edge(START, 'load_memory')
        graph.add_node('prepare_input', lambda state: {})
        graph.add_edge(['check_store', 'load_memory'], 'prepare_input')
        graph.add_conditional_edges('prepare_input', lambda state: 'read_screenshot' if self.has_screenshot else 'classify',
                                    {'read_screenshot': 'read_screenshot', 'classify': 'classify'})
        graph.add_edge('read_screenshot', 'classify')
        graph.add_conditional_edges('classify', lambda state: state['next_node'], {'retrieve': 'retrieve', 'finish': 'finish'})
        transitions = {
            'retrieve': ('decide', 'generate', 'prepare_evidence', 'fallback', 'web_search'),
            'web_search': ('generate', 'fallback'),
            'prepare_evidence': ('generate', 'fallback'),
            'decide': ('tool', 'generate', 'finish', 'fallback'),
            'tool': ('decide', 'fallback'),
            'generate': ('generate', 'review', 'fallback'),
            'review': ('schedule', 'compare_materials', 'finish', 'fallback'),
            'schedule': ('finish', 'fallback'),
            'compare_materials': ('finish', 'fallback'),
        }
        for name, targets in transitions.items():
            graph.add_conditional_edges(name, lambda state: state['next_node'],
                                        {n: n for n in targets})
        graph.add_conditional_edges('fallback', lambda state: 'compare_materials' if self.recognized_materials(state) else 'finish',
                                    {'compare_materials': 'compare_materials', 'finish': 'finish'})
        graph.add_edge('finish', END)
        return graph.compile()

    async def check_store(self, state):
        docs = await asyncio.to_thread(self.service.documents)
        return {'catalog_count': len(docs)}

    async def load_memory(self, state):
        context = state['context']
        remembered = await asyncio.to_thread(self.memory.context, state['session_id']) if state['use_memory'] else ''
        used = bool(remembered and len(context) < 1000)
        if remembered:
            context = (context + '\n以下是可过期的用户记忆，本次明确背景优先：\n' + remembered)[:1000]
        return {'context': context, 'memory_used': used}

    async def read_screenshot(self, state):
        try:
            value = await self.call(READ_SCREENSHOT, {'task': state['question']}, image=self.screenshot)
            return {'screenshot_analysis': validate_recognition(value)}
        finally:
            self.screenshot = None

    @staticmethod
    def recognized_materials(state):
        image = state.get('screenshot_analysis', {})
        return image.get('status') == 'recognized' and bool(image.get('materials'))

    async def classify(self, state):
        screenshot = state.get('screenshot_analysis', {})
        title = screenshot.get('title', '')
        routing = route(state['question'] + ' ' + state['context'] + ' ' + title)
        if self.has_screenshot and (screenshot.get('status') != 'recognized' or not screenshot.get('is_application_page')):
            work = RuntimeState(state['question'], state['context'], run_id=self.run_id, routing=routing, termination='needs_user_input')
            work.result = {'question': state['question'], 'status': 'insufficient_evidence', 'mode_requested': self.mode,
                           'mode_used': 'no_evidence', 'answer': '截图未能识别为办事材料页面，请选择支持图片的模型或上传包含事项名称与材料列表的截图。',
                           'claims': [], 'sources': [], 'missing_information': [], 'warnings': [], 'agent_trace': [],
                           'schedule_drafts': [], 'verification': {'status': 'not_run', 'checked': 0, 'removed': 0}, 'checked_at': current_time()}
            return {'work': work, 'next_node': 'finish'}
        if self.mode == 'multi_agent' and self.model is not None:
            agent = PolicyClassificationAgent()
            try:
                instruction, payload = agent.request(state['question'], state['context'])
                routing = agent.validate(await self.call(instruction, payload, 20))
            except ModelError:
                self.warnings.append('分类Agent未成功，使用规则领域分类。')
                get_stream_writer()({'type': 'fallback_used', 'node': 'classify', 'data': {'reason': 'rules_routing'}})
        routing.update(intent='plan' if wants_schedule(state['question']) else 'answer',
                       information_needs=information_needs(state['question']))
        work = RuntimeState(state['question'], state['context'], run_id=self.run_id, routing=routing)
        work.skills, unavailable = select_skills(work.question, offline=self.mode == 'offline')
        if unavailable:
            self.warnings.append('部分任务指引未加载，继续使用基础流程。')
        if title:
            work.context = (work.context + '\n当前事项（用户截图标题，非官方依据）：' + title)[:1000]
        return {'work': work, 'next_node': 'retrieve'}

    async def retrieve(self, state):
        work = state['work']
        self.research_deadline = time.monotonic() + 120
        output = await asyncio.to_thread(answer_question, self.store, work.question, work.context,
                                         self.body['top_k'], 'offline', selected_domains=
                                         [d['id'] for d in work.routing['domains']])
        output.update(mode_requested=self.mode, agent_trace=[], schedule_drafts=[],
                      skills_used=[{'name':s['name'],'version':s['version']} for s in work.skills],
                      verification={'status': 'not_run', 'checked': 0, 'removed': 0})
        work.result = output
        work.evidence_registry = {(e['source_id'], e['version'], e['start'], e['end']): e['evidence_id'] for e in output['sources']}
        work.next_id = len(work.evidence_registry) + 1
        signature = ('search_documents', json.dumps({'query': work.question + (' ' + work.context if work.context else '')}, sort_keys=True))
        work.executed.add(signature)
        output['agent_trace'].append({'event': 'tool', 'tool': 'search_documents', 'phase': 'seed', 'results_count': len(output['sources'])})
        next_node = 'decide' if self.mode in ('agent', 'multi_agent') else 'prepare_evidence' if self.mode == 'quick' else 'generate'
        if self.mode == 'offline' or self.model is None or not output['sources'] and self.mode == 'llm':
            next_node = 'fallback'
            if self.mode != 'offline' and self.model is None:
                output['warnings'].append('未配置可用模型，本次只展示原文。')
                work.termination = 'model_failed'
        if not output['sources'] and self.mode != 'offline' and self.model is not None and self.web_searcher.configured:
            next_node = 'web_search'
        return {'work': work, 'next_node': next_node}

    async def web_search(self, state):
        work = state['work']
        # Only the current question goes to the provider, never profile/history/screenshots.
        work.result['web_search'] = {'attempted': True, 'provider': 'tavily', 'source_count': 0}
        try:
            hits = await self.web_searcher.search(work.question)
            self.web_evidence = {hit['source_id']: dict(hit) for hit in hits}
            work.merge(hits, self.policy.max_evidence)
            work.result['web_search']['source_count'] = len(hits)
            work.result['agent_trace'].append({'event':'tool','tool':'tavily_search','results_count':len(hits),'phase':'local_miss'})
            if not hits:
                work.result['warnings'].append('联网搜索未取得可核实的官方正文，未使用搜索摘要作答。')
        except SearchError as error:
            work.result['warnings'].append(str(error))
            get_stream_writer()({'type':'fallback_used','node':'web_search','data':{'reason':'web_unavailable'}})
        return {'work': work, 'next_node': 'generate' if work.result['sources'] else 'fallback'}

    async def prepare_evidence(self, state):
        """At most two independent, rule-selected local reads; no LLM planning round trips."""
        work = state['work']
        query = work.question + ' ' + work.context
        section = '申请材料' if any(w in query for w in ('材料', '资料', '截图')) else '住房支持' if any(w in query for w in ('租房', '安居')) else ''
        ids = list(dict.fromkeys(e['source_id'] for e in work.result['sources']))[:2] if section else []
        async def read(source_id):
            try:
                return await asyncio.to_thread(self.registry.execute, 'read_source', {'source_id': source_id, 'section': section})
            except ToolError:
                return []
        batches = await asyncio.gather(*(read(source_id) for source_id in ids))
        for source_id, hits in zip(ids, batches):
            work.rounds += 1
            work.result['agent_trace'].append({'event': 'tool', 'tool': 'read_source', 'phase': 'quick_context', 'results_count': len(hits)})
            if hits:
                work.merge(hits, 8)
        # Section reads must not evict the annual notice behind several windows
        # from the same older source. Keep two windows per source and year intent.
        ordered = sorted(work.result['sources'], key=lambda hit: -temporal_weight(hit, work.question))
        selected, counts = [], {}
        for hit in ordered:
            if counts.get(hit['source_id'], 0) < 2:
                selected.append(hit)
                counts[hit['source_id']] = counts.get(hit['source_id'], 0) + 1
            if len(selected) == 6:
                break
        work.result['sources'] = selected
        return {'work': work, 'next_node': 'generate' if work.result['sources'] else 'fallback'}

    async def current(self, work):
        local = []
        for item in work.result['sources']:
            expected = self.web_evidence.get(item['source_id'])
            if expected is None:
                local.append(item)
            elif any(item.get(key) != expected.get(key) for key in ('version','text','source_url','start','end','excerpt_sha256')):
                raise ValueError('联网原文证据发生变化')
        await asyncio.to_thread(ensure_current, self.store, local)

    def remaining_research(self):
        return min(self.deadline, self.research_deadline or self.deadline) - time.monotonic()

    def conclude_research(self, work, reason):
        get_stream_writer()({'type': 'research_concluded', 'node': 'decide', 'data': {'reason': reason}})
        work.result['agent_trace'].append({'event': 'research_concluded', 'reason': reason})
        if work.result['sources']:
            return {'work': work, 'next_node': 'generate'}
        work.termination = 'insufficient_evidence'
        work.result['missing_information'] = ['现有资料仍不足，已停止补查；请明确事项或补充官方来源。']
        return {'work': work, 'next_node': 'fallback'}

    async def decide(self, state):
        work = state['work']
        await self.current(work)
        # Protect the actual answer and its review instead of spending all calls on deciding to answer.
        if work.rounds >= self.policy.max_tool_rounds:
            return self.conclude_research(work, 'tool_round_limit')
        if self.max_calls - self.calls <= 2:
            return self.conclude_research(work, 'reserve_answer_review')
        if self.remaining_research() <= 80:
            return self.conclude_research(work, 'reserve_completion_time')
        payload = await asyncio.to_thread(self.builder.research, work, self.registry, self.policy)
        value = await self.call(RESEARCH + skill_instructions(work.skills), payload, timeout=20, research=True)
        try:
            decision = validate_decision(value)
        except ModelError:
            if self.max_calls - self.calls <= 2:
                return self.conclude_research(work, 'reserve_answer_review')
            if self.decision_repairs >= 1:
                raise
            self.decision_repairs += 1
            get_stream_writer()({'type': 'repair_requested', 'node': 'decide', 'data': {'reason': 'invalid_decision_structure'}})
            instruction = RESEARCH + skill_instructions(work.skills) + '\n上次决策格式无效，请重新输出且只含action、arguments、gaps三个字段。action只能是answer、clarify、search_documents或read_source中的一个。资料足够时输出{"action":"answer","arguments":{},"gaps":[]}。不要输出回答正文或额外字段。'
            decision = validate_decision(await self.call(instruction, payload, timeout=20, research=True))
        action = decision['action']
        if action == 'clarify' and asks_for_identity(decision['arguments']['question']) and not personal_advice_requested(work.question):
            if work.result['sources']:
                action = 'answer'
            else:
                decision['arguments']['question'] = '请明确政策名称或查询主题，以便查找官方资料。'
        work.result['agent_trace'].append({'event': 'decision', 'action': action, 'gap_count': len(decision['gaps'])})
        if action == 'clarify':
            work.termination = 'needs_user_input'
            work.result.update(status='insufficient_evidence', mode_used='agent', claims=[],
                               answer='请明确查询范围后继续。', missing_information=[decision['arguments']['question']])
            return {'work': work, 'next_node': 'finish'}
        if action == 'answer':
            if not work.result['sources']:
                raise ModelError('研究Agent试图在无证据时回答')
            return {'work': work, 'next_node': 'generate'}
        work.result['_decision'] = decision
        return {'work': work, 'next_node': 'tool'}

    async def tool(self, state):
        work = state['work']
        decision = work.result.pop('_decision')
        work.rounds += 1
        name, args = decision['action'], decision['arguments']
        signature = (name, json.dumps(args, sort_keys=True))
        try:
            if signature in work.executed:
                raise ToolError('重复动作')
            work.executed.add(signature)
            hits = await asyncio.to_thread(self.registry.execute, name, args)
            work.merge(hits, self.policy.max_evidence)
            work.result['agent_trace'].append({'event': 'tool', 'tool': name, 'results_count': len(hits), 'phase': 'correction'})
        except ToolError:
            work.result['agent_trace'].append({'event': 'tool_error', 'tool': name, 'message': '工具参数或来源不可用'})
            get_stream_writer()({'type': 'fallback_used', 'node': 'tool', 'data': {'reason': 'tool_unavailable'}})
        return {'work': work, 'next_node': 'decide'}

    async def generate(self, state):
        work = state['work']
        await self.current(work)
        self.attempts += 1
        instruction = INSTRUCTION + skill_instructions(work.skills)
        if self.mode == 'quick':
            instruction += '\n本轮为快速问答：用3至4部分、总计约300至500字回答；每部分尽量不超过160字。保留条件和日期，缺失的政策材料/截止时间在相关正文一句话说明，不展开泛化建议。'
        if self.attempts > 1:
            instruction += '\n上次回答未通过结构/引用校验，请重新生成，删除无依据结论。可用ID：' + ','.join(e['evidence_id'] for e in work.result['sources'])
        is_agent = self.mode in ('agent', 'multi_agent', 'quick')
        # Completion uses the overall deadline, not the shorter exploration window.
        timeout = max(1, min(45, self.deadline - time.monotonic() - 45)) if is_agent else 45
        payload = self.builder.answer(work)
        payload['optional_follow_up_fields'] = [{'key': key, 'options': KNOWN[key]} for key in follow_up_fields(work.routing, work.context + ' ' + work.question)]
        if self.recognized_materials(state):
            screenshot = state['screenshot_analysis']
            payload['user_screenshot_context'] = {key: screenshot.get(key) for key in ('title', 'materials', 'gaps')}
            instruction += '\nuser_screenshot_context是已识别的用户截图资料，非官方政策依据。不要声称用户未提供截图或没有识别截图。材料逐项核对由独立截图卡展示；本回答只总结官方证据，不能将截图材料当作官方要求，也不能用S编号代替E政策引用。'
        value = await self.call(instruction, payload, timeout=timeout, research=is_agent, completion=True)
        try:
            generated = validate_answer(value, work.result['sources'])
        except ModelError:
            if self.attempts >= 2:
                raise
            get_stream_writer()({'type': 'repair_requested', 'node': 'generate', 'data': {'reason': 'invalid_citations_or_structure'}})
            return {'work': work, 'next_node': 'generate'}
        # Pending claims are private until review and final source checks succeed.
        work.result['_generated'] = generated
        return {'work': work, 'next_node': 'review'}

    async def review(self, state):
        work = state['work']
        generated = work.result.pop('_generated')
        claims = generated['claims']
        await self.current(work)
        if claims and self.mode in ('agent', 'multi_agent', 'quick'):
            payload = self.builder.compact_review(work, claims)
            instruction = REVIEW + '\n原文在cited_evidence中按evidence_id去重。每条claim只允许使用它自身evidence_ids指定的原文，不能借用其他原文。reason用不超过30字的一句话。'
            value = await self.call(instruction, payload, timeout=45, research=True, completion=True)
            try:
                checked = review_claims(value, claims)
            except ModelError:
                if self.calls >= self.max_calls or self.deadline - time.monotonic() <= 5:
                    raise
                get_stream_writer()({'type': 'repair_requested', 'node': 'review', 'data': {'reason': 'invalid_review_structure'}})
                checked = review_claims(await self.call(instruction + '\n上次评审格式无效。请只输出verdicts数组，每个claim_index必须恰好出现一次，supported为布尔值。',
                                                       payload, timeout=45, research=True, completion=True), claims)
            removed = len(claims) - len(checked)
            work.result['verification'] = {'status': 'repaired' if removed else 'supported', 'checked': len(claims), 'removed': removed}
            if removed:
                generated['missing_information'] = [*generated['missing_information'], '部分要点缺乏原文支持，已移除。'][:3]
            claims = checked
        else:
            work.result['verification'] = {'status': 'structure_only' if claims else 'not_needed', 'checked': len(claims), 'removed': 0}
        work.result.update(claims=claims, status='answered' if claims else 'insufficient_evidence',
                           mode_used='llm' if self.mode == 'llm' else 'agent', missing_information=generated['missing_information'],
                           follow_up=build_follow_up(work.routing, work.context + ' ' + work.question, generated.get('follow_up', '')) if claims else None)
        return {'work': work, 'next_node': 'compare_materials' if self.recognized_materials(state) else
                'schedule' if self.mode in ('multi_agent', 'quick') and claims and wants_schedule(work.question) else 'finish'}

    async def compare_materials(self, state):
        screenshot = state['screenshot_analysis']
        evidence = state['work'].result['sources']
        # Default is explicitly unverified; no model/citation failure can turn it into confirmed policy.
        checks = {m['id']: {'id': m['id'], 'verdict': 'not_found', 'policy_evidence_ids': [],
                           'policy_quote': '', 'explanation': '现有政策依据尚未确认此项，请核对办理页面与最新指南。'} for m in screenshot['materials']}
        screenshot['checks'] = checks
        if evidence:
            value = await self.call(COMPARE_MATERIALS, {'materials': screenshot['materials'], 'evidence': evidence_payload(evidence)}, timeout=30, completion=True)
            screenshot['checks'] = validate_comparison(value, screenshot['materials'], evidence)
        screenshot['comparison_status'] = 'completed' if evidence else 'no_policy_evidence'
        return {'screenshot_analysis': screenshot, 'next_node': 'finish'}

    async def schedule(self, state):
        work = state['work']
        agent = SchedulePlanningAgent()
        try:
            instruction, payload = agent.request(work.question, work.result)
            instruction += skill_instructions([s for s in work.skills if s['name'] == 'application_planning'])
            work.result['schedule_drafts'] = agent.validate(await self.call(instruction, payload), work.result['sources'])
        except ModelError:
            work.result['warnings'].append('日程草案未成功或未通过检查，可手动创建。')
            get_stream_writer()({'type': 'fallback_used', 'node': 'schedule', 'data': {'reason': 'draft_unavailable'}})
        return {'work': work, 'next_node': 'finish'}

    async def fallback(self, state):
        work = state['work']
        work.result.pop('_generated', None)
        work.result.pop('_decision', None)
        evidence = work.result['sources']
        work.result.update(claims=[{'text': e['text'], 'evidence_ids': [e['evidence_id']]} for e in evidence],
                           mode_used='extractive' if evidence else 'no_evidence',
                           status='evidence_only' if evidence else 'insufficient_evidence')
        if evidence and not any('当前为原文摘录模式' in w for w in work.result['warnings']):
            work.result['warnings'].append('当前为原文摘录模式，尚未由模型整理为自然语言回答。')
        return {'work': work}

    async def finish(self, state):
        work = state['work']
        await self.current(work)
        result = work.result
        result['warnings'].extend(self.warnings)
        if result['mode_used'] in ('agent', 'llm'):
            result['warnings'] = [w for w in result['warnings'] if '当前为原文摘录模式' not in w]
            if self.mode in ('agent', 'multi_agent', 'quick') and result['claims']:
                result['warnings'].append('评审为模型辅助检查，不构成事实正确性或政策有效性的保证。')
        if result['claims']:
            result['answer'] = ('以下是检索到的原文片段：\n\n' if result['mode_used'] == 'extractive' else '') + '\n\n'.join(
                c['text'] + ' ' + ' '.join('[' + e + ']' for e in c['evidence_ids']) for c in result['claims'])
        elif work.termination != 'needs_user_input':
            result['answer'] = '现有资料不足以支持完整回答，请明确事项或补充官方来源。'
        result.update(routing=work.routing, memory_used=state['memory_used'],
                      runtime={'run_id': self.run_id, 'phase': 'finished',
                               'termination': work.termination or ('completed' if result['status'] == 'answered' else 'insufficient_evidence'),
                               'engine': 'langgraph', 'allowed_tools': list(self.policy.allowed_tools)},
                      budget={'model_calls': self.calls, 'max_model_calls': self.max_calls,
                              'correction_rounds': work.rounds, 'max_correction_rounds': 2, 'max_elapsed_seconds': self.seconds,
                              'research_model_calls': self.research_calls, 'reserved_completion_calls': 2 if self.mode in ('agent', 'multi_agent', 'quick') else 0})
        result['execution_profile'] = 'quick' if self.mode == 'quick' else 'deep' if self.mode in ('agent', 'multi_agent') else self.mode
        if self.has_screenshot:
            result['screenshot_analysis'] = state.get('screenshot_analysis', {})
        result['report_markdown'] = render_answer(result)
        return {'result': result}

    async def run(self, emit):
        final = None
        initial = {k: self.body[k] for k in ('question', 'context', 'use_memory', 'session_id')}
        # v1 tuple format is explicit and tested against the locked LangGraph version.
        # Local personal data must not be exported even if a shell enables LangSmith tracing.
        with tracing_context(enabled=False):
            async for mode, data in self.graph.astream(initial, stream_mode=['updates', 'custom'],
                                                       config={'recursion_limit': 30}):
                if mode == 'custom':
                    emit(data)
                elif mode == 'updates' and 'finish' in data:
                    final = data['finish']['result']
        if final is None:
            raise RuntimeError('图执行未产生终态结果')
        return final
