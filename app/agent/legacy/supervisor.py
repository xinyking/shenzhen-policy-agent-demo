"""Supervisor dispatches isolated specialists; shares one bounded model-call budget."""
import time
from app.agent.subagents import PolicyClassificationAgent, PolicyResearchAgent, SchedulePlanningAgent
from app.knowledge.domains import route
from app.agent.llm import ModelError
from app.agent.answer import answer_question, render_answer
from app.agent.intent import wants_schedule, information_needs

class SharedModel:
    def __init__(self, model):
        self.model=model; self.calls=0; self.deadline=time.monotonic()+180
    def generate(self, instruction, data, timeout=45):
        remaining=self.deadline-time.monotonic()
        if self.calls>=7 or remaining<=0: raise ModelError('多Agent共享预算已用尽')
        self.calls+=1
        result=self.model.generate(instruction,data,timeout=min(timeout,remaining))
        if time.monotonic()>self.deadline: raise ModelError('多Agent时间预算已用尽')
        return result

class PolicySupervisor:
    def __init__(self, store, model_factory): self.store,self.factory=store,model_factory
    def run(self, question, context='', top_k=5):
        trace=[]; warnings=[]
        try: model=SharedModel(self.factory())
        except ModelError:
            result=answer_question(self.store,question,context,top_k,'offline')
            result['warnings'].append('未配置可用模型，多Agent未执行。')
            result['multi_agent_trace']=[]; result['mode_requested']='multi_agent'
            result['report_markdown']=render_answer(result)
            return result
        classification=PolicyClassificationAgent()
        try:
            routing=classification.run(model,question,context)
            trace.append({'agent':classification.name,'status':'completed'})
        except ModelError:
            routing=route(question); trace.append({'agent':classification.name,'status':'fallback_rules'})
            warnings.append('分类Agent未成功，使用规则领域分类。')
        # Model classification cannot authorize personal planning on its own.
        schedule_requested=wants_schedule(question)
        routing['intent']='plan' if schedule_requested else 'answer'
        routing['information_needs']=information_needs(question)
        remaining=max(1,min(120,int(model.deadline-time.monotonic())))
        result=PolicyResearchAgent().run(self.store,model,question,context,top_k,routing,remaining)
        result['routing']=routing
        trace.append({'agent':'policy_research','status':result['runtime']['termination']})
        result['schedule_drafts']=[]
        if result['status']=='answered' and schedule_requested:
            try:
                result['schedule_drafts']=SchedulePlanningAgent().run(model,question,result)
                trace.append({'agent':'schedule_planning','status':'draft_ready','count':len(result['schedule_drafts'])})
            except ModelError:
                trace.append({'agent':'schedule_planning','status':'failed'})
                warnings.append('日程材料草案未通过检查，可手动创建日程。')
        elif not schedule_requested:
            trace.append({'agent':'schedule_planning','status':'skipped_not_requested'})
        result['warnings'].extend(warnings)
        result['multi_agent_trace']=trace
        result['multi_agent_budget']={'model_calls':model.calls,'max_model_calls':7,'seconds':180}
        result['mode_requested']='multi_agent'
        result['report_markdown']=render_answer(result)
        return result
