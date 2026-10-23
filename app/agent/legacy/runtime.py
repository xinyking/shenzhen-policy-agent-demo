"""Runtime: initialize -> build context -> decide -> authorize/execute -> update -> finish."""
import json
from app.agent.protocol import Budget, BudgetExceeded, RESEARCH, REVIEW, validate_decision, review_claims, ensure_current
from app.agent.context import ContextBuilder
from app.agent.state import RuntimeState, RuntimePolicy
from app.tools.registry import ToolRegistry
from app.tools.policy_tools import PolicyTools, ToolError
from app.agent.llm import JsonModel, ModelError
from app.agent.answer import INSTRUCTION, answer_question, render_answer, validate_answer
from app.knowledge.domains import route
from app.agent.intent import asks_for_identity, personal_advice_requested

class AgentRuntime:
    def __init__(self, store, model_factory=JsonModel, policy=None, context_builder=None, routing_override=None):
        self.store, self.model_factory = store, model_factory
        self.policy = policy or RuntimePolicy()
        self.context_builder = context_builder or ContextBuilder()
        self.state = None
        self.routing_override = routing_override

    def run(self, question, context='', top_k=5):
        store, model_factory = self.store, self.model_factory
        # Baseline doubles as validated inputs, seed retrieval and safe fallback.
        output = answer_question(store, question, context, top_k, 'offline', selected_domains=
            [d['id'] for d in self.routing_override['domains']] if self.routing_override else None)
        output['mode_requested'] = 'agent'
        output['agent_trace'] = []
        output['verification'] = {'status': 'not_run', 'checked': 0, 'removed': 0}
        policy = self.policy
        output['sources'] = output['sources'][:policy.max_evidence]
        state = RuntimeState(question, context)
        state.routing = self.routing_override or route(question)
        self.state = state
        state.result = output
        budget = Budget(policy.max_model_calls, policy.timeout_seconds)
        builder = self.context_builder
        tools = PolicyTools(store, question, top_k)
        tool_registry = ToolRegistry(tools, policy)
        output['agent_trace'].append({'event': 'tool', 'tool': 'search_documents',
            'arguments': {'query': question + (' ' + context if context else '')},
            'results_count': len(output['sources']), 'phase': 'seed'})
        state.evidence_registry = {(e['source_id'], e['version'], e['start'], e['end']): e['evidence_id'] for e in output['sources']}
        state.next_id = len(state.evidence_registry) + 1
        state.executed = {('search_documents', json.dumps({'query': question + (' ' + context if context else '')}, sort_keys=True))}
        try:
            model = model_factory()
            while True:
                ensure_current(store, output['sources'])
                state.phase = 'research'
                decision = validate_decision(budget.call(model, RESEARCH, builder.research(state, tool_registry, policy)))
                action = decision['action']
                if action == 'clarify' and asks_for_identity(decision['arguments']['question']) and not personal_advice_requested(question):
                    if output['sources']:
                        action = 'answer'
                        output['agent_trace'].append({'event':'clarification_redirected','reason':'general_policy_information'})
                    else:
                        decision['arguments']['question']='请明确政策名称或查询主题，以便查找官方资料。'
                output['agent_trace'].append({'event': 'decision', 'action': action, 'gap_count': len(decision['gaps'])})
                if action == 'clarify':
                    state.termination = 'needs_user_input'
                    output.update(status='insufficient_evidence', mode_used='agent', claims=[],
                        answer='需要补充信息后才能可靠回答。', missing_information=[decision['arguments']['question']])
                    break
                if action == 'answer':
                    if not output['sources']:
                        raise ModelError('研究Agent试图在无证据时生成回答')
                    state.phase = 'generation'
                    generated = validate_answer(budget.call(model, INSTRUCTION, builder.answer(state)), output['sources'])
                    output['agent_trace'].append({'event': 'generate', 'claims_count': len(generated['claims']), 'citations_valid': True})
                    claims = generated['claims']
                    if claims:
                        state.phase = 'review'
                        checked = review_claims(budget.call(model, REVIEW, builder.review(state, claims)), claims)
                        removed = len(claims) - len(checked)
                        output['verification'] = {'status': 'repaired' if removed else 'supported',
                            'checked': len(claims), 'removed': removed}
                        output['agent_trace'].append({'event': 'review', 'checked': len(claims), 'removed': removed})
                        if removed:
                            generated['missing_information'] = [*generated['missing_information'], '部分要点缺乏引用支持，已移除；需要更多资料。'][:3]
                        claims = checked
                    else:
                        output['verification'] = {'status': 'not_needed', 'checked': 0, 'removed': 0}
                    output.update(status='answered' if claims else 'insufficient_evidence', mode_used='agent', claims=claims,
                        missing_information=generated['missing_information'],
                        answer='\n\n'.join(claim['text'] + ' ' + ' '.join('[' + c + ']' for c in claim['evidence_ids']) for claim in claims)
                               if claims else '现有证据不足以支持完整回答。')
                    break
                if state.rounds >= policy.max_tool_rounds:
                    output['missing_information'] = decision['gaps'] or ['两轮补查后仍存在资料缺口，请补充政策名称或官方文件。']
                    output['warnings'].append('检索补查预算已用尽，保留原文摘录，不输出未经评审的模型结论。')
                    output['agent_trace'].append({'event': 'stop', 'reason': 'tool_budget'})
                    break
                state.rounds += 1
                state.phase = 'tool_execution'
                try:
                    signature = (action, json.dumps(decision['arguments'], sort_keys=True))
                    if signature in state.executed:
                        raise ToolError('重复工具动作已阻止')
                    state.executed.add(signature)
                    hits = tool_registry.execute(action, decision['arguments'])
                    output['agent_trace'].append({'event': 'tool', 'tool': action,
                        'arguments': decision['arguments'], 'results_count': len(hits), 'phase': 'correction'})
                    state.merge(hits, policy.max_evidence)
                except ToolError:
                    output['agent_trace'].append({'event': 'tool_error', 'tool': action, 'message': '工具参数或来源不可用'})
            ensure_current(store, output['sources'])
        except ModelError as exc:
            state.termination = 'budget_exceeded' if isinstance(exc, BudgetExceeded) else 'model_failed'
            output['warnings'].append(str(exc) + '；Agent流程回退为可用原文摘录。')
            output['agent_trace'].append({'event': 'fallback', 'reason': 'budget' if isinstance(exc, BudgetExceeded) else 'model_or_output'})
            output['verification'] = {'status': 'failed', 'checked': 0, 'removed': 0}
        finally:
            ensure_current(store, output['sources'])
            if output['mode_used'] not in {'agent'}:
                # Rebuild fallback from the current evidence, including corrected searches.
                output['claims'] = [{'text': e['text'], 'evidence_ids': [e['evidence_id']]} for e in output['sources']]
                output['mode_used'] = 'extractive' if output['sources'] else 'no_evidence'
                output['status'] = 'evidence_only' if output['sources'] else 'insufficient_evidence'
                output['answer'] = ('以下是检索到的原文片段：\n\n' + '\n\n'.join(c['text'] + ' [' + c['evidence_ids'][0] + ']'
                    for c in output['claims'])) if output['sources'] else '当前资料不足以回答，请补充政策名称或官方文件。'
            else:
                output['warnings'] = [w for w in output['warnings'] if '当前为原文摘录模式' not in w]
                if output['verification']['status'] in {'supported', 'repaired'}:
                    output['warnings'].append('评审为模型辅助检查，不构成事实正确性或政策有效性的保证。')
            if output['mode_used'] == 'extractive' and not any('当前为原文摘录模式' in w for w in output['warnings']):
                output['warnings'].append('当前为原文摘录模式，尚未由模型整理为自然语言回答。')
            output['budget'] = {'model_calls': budget.calls, 'max_model_calls': policy.max_model_calls,
                'correction_rounds': state.rounds, 'max_correction_rounds': policy.max_tool_rounds, 'max_elapsed_seconds': policy.timeout_seconds}
            if not state.termination:
                state.termination = ('completed' if output['status'] == 'answered' else 'insufficient_evidence' if output['mode_used'] == 'agent' else 'tool_budget_exhausted')
            state.phase = 'finished'
            output['runtime'] = {'run_id': state.run_id, 'phase': state.phase, 'termination': state.termination, 'allowed_tools': list(policy.allowed_tools)}
            output['routing'] = state.routing
            output['trace'] = [event['event'] for event in output['agent_trace']]
            output['report_markdown'] = render_answer(output)
        return output
