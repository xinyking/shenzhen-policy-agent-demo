"""Compatibility entry point; Agent implementation lives in runtime.py."""
from app.agent.protocol import Budget, BudgetExceeded, validate_decision, review_claims
from app.agent.legacy.runtime import AgentRuntime
from app.agent.llm import JsonModel

def agent_answer(store, question, context='', top_k=5, model_factory=JsonModel):
    return AgentRuntime(store, model_factory).run(question, context, top_k)
