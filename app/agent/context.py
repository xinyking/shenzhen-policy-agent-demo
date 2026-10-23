"""Explicit projections for research, generation and cited-only review."""
from app.knowledge.evidence import evidence_payload, current_time

class ContextBuilder:
    def research(self, state, registry, policy):
        return {'question':state.question,'context':state.context,
                'current_time':current_time(),'response_goal':'policy_information_not_identity_matching',
                'selected_skills':[{'name':s['name'],'version':s['version']} for s in state.skills],
                'information_needs':state.routing.get('information_needs',[]),
                'sources_index':registry.tools.index(),'tools':registry.manifest(),
                'evidence':evidence_payload(state.result['sources']),
                'remaining_tool_rounds':policy.max_tool_rounds-state.rounds,
                'tool_history':state.result['agent_trace']}

    def answer(self, state):
        return {'question':state.question,'context':state.context,
                'current_time':current_time(),
                'evidence':evidence_payload(state.result['sources'])}

    def review(self, state, claims):
        by_id = {e['evidence_id']:e for e in state.result['sources']}
        return {'question':state.question,'claims':[
            {'claim_index':i,'text':c['text'],
             'cited_evidence':evidence_payload([by_id[e] for e in c['evidence_ids']])}
            for i,c in enumerate(claims)]}

    def compact_review(self, state, claims):
        """Send each cited original once; preserve the exact evidence IDs permitted for each claim."""
        by_id = {e['evidence_id']: e for e in state.result['sources']}
        cited = list(dict.fromkeys(e for claim in claims for e in claim['evidence_ids']))
        return {'question': state.question,
                'claims': [{'claim_index': i, 'text': c['text'], 'evidence_ids': list(c['evidence_ids'])}
                           for i, c in enumerate(claims)],
                'cited_evidence': [{key: by_id[e][key] for key in ('evidence_id', 'title', 'text', 'published_at')} for e in cited]}
