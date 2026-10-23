from app.knowledge.domains import DOMAINS, route
from app.agent.llm import ModelError

class PolicyResearchAgent:
    name = 'policy_research'
    def run(self, store, model, question, context, top_k, routing, remaining):
        from app.agent.legacy.runtime import AgentRuntime
        from app.agent.state import RuntimePolicy
        return AgentRuntime(store,lambda:model,RuntimePolicy(timeout_seconds=remaining),
                            routing_override=routing).run(question,context,top_k)
