from app.agent.prompts import SCHEDULE
from app.knowledge.domains import DOMAINS, route
from app.agent.llm import ModelError

class SchedulePlanningAgent:
    name = 'schedule_planning'
    def run(self, model, question, result):
        evidence = result['sources']
        if not evidence: return []
        instruction, payload = self.request(question, result)
        return self.validate(model.generate(instruction, payload, timeout=45), evidence)

    def request(self, question, result):
        return (SCHEDULE,
            {'question':question,'claims':result['claims'],'evidence':[{k:e[k] for k in ('evidence_id','title','text')} for e in result['sources']]})

    def validate(self, value, evidence):
        if not isinstance(value,dict) or set(value)!={'items'} or not isinstance(value['items'],list) or len(value['items'])>3:
            raise ModelError('日程规划Agent格式无效')
        by_id={e['evidence_id']:e for e in evidence}
        for item in value['items']:
            if not isinstance(item,dict) or set(item)!={'title','evidence_ids','materials','method'}:
                raise ModelError('日程草案字段无效')
            if not isinstance(item['title'],str) or not 0<len(item['title'])<=150 or not isinstance(item['method'],str) or len(item['method'])>2000:
                raise ModelError('日程草案文本无效')
            ids=item['evidence_ids']
            if not isinstance(ids,list) or not 1<=len(ids)<=8 or not all(isinstance(e,str) and e in by_id for e in ids):
                raise ModelError('日程草案引用无效')
            if item['method'] and not any(item['method'] in by_id[e]['text'] for e in ids):
                raise ModelError('办理方法原文不匹配，未保存草案')
            if not isinstance(item['materials'],list) or len(item['materials'])>12: raise ModelError('材料清单无效')
            for material in item['materials']:
                if not isinstance(material,dict) or set(material)!={'text','evidence_id','quote'}: raise ModelError('材料引用字段无效')
                if (not isinstance(material['text'],str) or not 0<len(material['text'])<=200 or
                    not isinstance(material['evidence_id'],str) or material['evidence_id'] not in ids or
                    not isinstance(material['quote'],str) or not 0<len(material['quote'])<=500 or
                    material['quote'] not in by_id[material['evidence_id']]['text']):
                    raise ModelError('材料原文不匹配，未保存草案')
            item['date']=None
            item['requires_confirmation']=True
            item['sources']=[{k:by_id[e][k] for k in ('evidence_id','title','source_url','version','locator')} for e in dict.fromkeys(ids)]
        return value['items']
