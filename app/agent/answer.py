"""Retrieve -> answer -> citation validation, without eligibility modeling."""
from app.agent.prompts import INSTRUCTION
from datetime import datetime, timezone
import re

from app.knowledge.ingestion import current_documents, search_documents
from app.agent.llm import JsonModel, ModelError
from app.knowledge.evidence import current_time, evidence_payload



def validate_answer(result, evidence):
    if not isinstance(result, dict) or not {'claims', 'missing_information'} <= set(result) or set(result) - {'claims', 'missing_information', 'follow_up'}:
        raise ModelError('问答模型输出格式不完整')
    if 'follow_up' in result and (not isinstance(result['follow_up'], str) or len(result['follow_up']) > 200):
        raise ModelError('追问格式无效')
    claims, missing = result['claims'], result['missing_information']
    if not isinstance(claims, list) or len(claims) > 8:
        raise ModelError('回答要点格式无效')
    if not isinstance(missing, list) or len(missing) > 3 or not all(
            isinstance(item, str) and 0 < len(item.strip()) <= 200 for item in missing):
        raise ModelError('回答缺口格式无效')
    if not claims and not missing:
        raise ModelError('模型返回空回答')
    allowed = {item['evidence_id'] for item in evidence}
    for claim in claims:
        if not isinstance(claim, dict) or not {'text', 'evidence_ids'} <= set(claim) or set(claim) - {'text', 'evidence_ids', 'title', 'kind'}:
            raise ModelError('回答要点字段无效')
        if 'title' in claim and (not isinstance(claim['title'], str) or not 0 < len(claim['title'].strip()) <= 80):
            raise ModelError('回答部分标题无效')
        if 'kind' in claim and claim['kind'] not in ('summary', 'interpretation', 'suggestion'):
            raise ModelError('回答部分类型无效')
        text, citations = claim['text'], claim['evidence_ids']
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 1200:
            raise ModelError('回答要点文本无效')
        if any('E' + value not in allowed for value in re.findall(r'\[E(\d+)\]', text)):
            raise ModelError('回答文本含不存在的引用')
        if re.search(r'https?://', text, re.IGNORECASE):
            raise ModelError('回答文本不应自行插入网址')
        if (not isinstance(citations, list) or not 1 <= len(citations) <= 8 or
                not all(isinstance(item, str) and item in allowed for item in citations) or
                len(set(citations)) != len(citations)):
            raise ModelError('回答包含无效或缺失引用')
    return result


def generate_validated(model, payload, evidence):
    """One bounded format repair; never guess which source supports a statement."""
    last = None
    for attempt in range(2):
        instruction = INSTRUCTION
        if attempt:
            instruction += '\n上次回答未通过结构/引用校验。请重新生成，删除无法引用的结论。可用ID：' + ','.join(e['evidence_id'] for e in evidence)
        value = model.generate(instruction, payload)
        try:
            return validate_answer(value, evidence)
        except ModelError as exc:
            last = exc
    raise last


def render_answer(result):
    lines = ['# 深圳政务政策问答', '']
    sources = {s['evidence_id']: s for s in result['sources']}
    cited = list(dict.fromkeys(c for claim in result['claims'] for c in claim['evidence_ids']))
    numbers = {citation: index for index, citation in enumerate(cited, 1)}
    if result['claims']:
        for index, claim in enumerate(result['claims'], 1):
            lines += [f"## {index}. {claim.get('title', '原文摘录' if result['mode_used'] == 'extractive' else '回复要点')}", '',
                      claim['text'] + ' ' + ''.join(f"[^{numbers[c]}]" for c in claim['evidence_ids']), '']
    else:
        lines += [result['answer'], '']
    if result['missing_information']:
        lines += ['需要补充或核查：', '', *['- ' + item for item in result['missing_information']], '']
    if cited:
        lines += ['## 引用片段', '']
        for citation in cited:
            source = sources[citation]
            lines += [f"[^{numbers[citation]}]: [{source['title']}]({source['source_url']}) · [{citation}] · {source['locator']}；发布时间：{source['published_at'] or '未提取'}；最近检查：{source['last_checked_at']}", '',
                      *['> ' + line for line in source['text'].splitlines()], '']
    lines += ['', *['- ' + warning for warning in result['warnings']], '',
              '文档刷新不等于政策有效性确认；办理时请核对主管部门最新要求。']
    return '\n'.join(lines)


def context_windows(store, selected):
    """Include surrounding paragraphs so headings and list items stay together."""
    documents = {doc['source_id']: doc for doc in current_documents(store)}
    result = []
    for hit in selected:
        doc = documents[hit['source_id']]
        if doc['version'] != hit['version'] or doc['last_check_status'] != 'success':
            raise ValueError('检索期间来源已变化，请重试')
        spans = doc['spans']
        index = next(i for i, span in enumerate(spans) if span['id'] == hit['id'])
        window = spans[max(0, index - 1):index + 4]
        start, end = window[0]['start'], window[-1]['end']
        text = doc['text'][start:end][:3000]
        result.append({**hit, 'start': start, 'end': start + len(text), 'text': text,
            'locator': window[0]['locator'] + ' 至 ' + window[-1]['locator'],
            'span_ids': [span['id'] for span in window], 'truncated': end - start > 3000})
    return result


def answer_question(store, question, context='', top_k=5, mode='offline', model_factory=JsonModel, selected_domains=None):
    if mode not in {'offline', 'llm'} or not isinstance(question, str) or not question.strip():
        raise ValueError('问答模式或问题无效')
    if len(question) > 500 or not isinstance(context, str) or len(context) > 1000 or type(top_k) is not int or not 1 <= top_k <= 8:
        raise ValueError('问答参数超过限制')
    search_query = question + ' ' + context
    # When the question names a document exactly, keep adjacent policies separate.
    named_sources = {doc['source_id'] for doc in current_documents(store)
        if 4 <= len(doc['title']) <= 40 and doc['title'] in question}
    source_ids = named_sources or None
    from app.knowledge.domains import route, document_domains
    domains = set(selected_domains) if selected_domains is not None else {d['id'] for d in route(question)['domains']}
    if domains and not named_sources:
        related = {d['source_id'] for d in current_documents(store) if domains & set(document_domains(d))}
        source_ids = related or None
    hits = search_documents(store, search_query, 50, source_ids=source_ids)
    warnings, selected, counts = [], [], {}
    for hit in search_documents(store, search_query, 50, successful_only=True, source_ids=source_ids):
        count = counts.get(hit['source_id'], 0)
        if count >= 2:
            continue
        selected.append(hit)
        counts[hit['source_id']] = count + 1
        if len(selected) == top_k:
            break
    if any(hit['last_check_status'] != 'success' for hit in hits):
        warnings.append('最近刷新失败的来源已排除，当前资料可能不完整。')
    evidence = [{**hit, 'evidence_id': f'E{index}'}
                for index, hit in enumerate(context_windows(store, selected), 1)] if selected else []
    output = {'question': question, 'status': 'insufficient_evidence', 'mode_requested': mode,
              'mode_used': 'no_evidence', 'answer': '当前本地资料中未找到足够相关的可用证据，请补充政策名称或具体事项。',
              'claims': [], 'missing_information': [], 'sources': evidence, 'warnings': warnings,
              'checked_at': datetime.now(timezone.utc).isoformat(),
              'trace': ['retrieve', 'answer', 'validate_citations']}
    if not evidence:
        output['missing_information'] = ['本地库并非全部深圳政策，可补充官方文件后重新提问。']
    else:
        claims = [{'text': item['text'], 'evidence_ids': [item['evidence_id']]} for item in evidence]
        output.update(status='evidence_only', mode_used='extractive', claims=claims)
        if mode == 'llm':
            try:
                model = model_factory()
                generated = generate_validated(model, {'question': question,
                    'context': context, 'current_time':current_time(),
                    'evidence':evidence_payload(evidence)}, evidence)
                claims = generated['claims']
                output.update(status='answered' if claims else 'insufficient_evidence', mode_used='llm',
                              claims=claims, missing_information=generated['missing_information'])
            except ModelError as exc:
                warnings.append(str(exc) + '；已回退为原文摘录，未展示无效模型回答。')
        if output['mode_used'] == 'extractive':
            warnings.append('当前为原文摘录模式，尚未由模型整理为自然语言回答。')
        prefix = '以下是检索到的原文片段：\n\n' if output['mode_used'] == 'extractive' else ''
        output['answer'] = prefix + '\n\n'.join(claim['text'] + ' ' +
            ' '.join('[' + citation + ']' for citation in claim['evidence_ids']) for claim in claims)
        if not claims:
            output['answer'] = '已检索到相关资料，但现有证据不足以回答这一问题。'
    output['report_markdown'] = render_answer(output)
    return output
