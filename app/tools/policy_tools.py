"""Read-only model-selected tools. Arguments never contain filesystem paths."""
import re

from app.knowledge.ingestion import current_documents, search_documents
from app.agent.answer import context_windows


class ToolError(ValueError):
    pass


class PolicyTools:
    def __init__(self, store, question, top_k):
        self.store, self.question, self.top_k = store, question, top_k

    def index(self):
        return [{'source_id': d['source_id'], 'title': d['title'], 'published_at': d['published_at']}
                for d in current_documents(self.store) if d['last_check_status'] == 'success']

    def execute(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ToolError('工具参数必须是对象')
        if name == 'search_documents':
            if set(arguments) != {'query'} or not isinstance(arguments['query'], str) or not 1 <= len(arguments['query'].strip()) <= 500:
                raise ToolError('检索工具参数无效')
            named = {d['source_id'] for d in current_documents(self.store)
                     if 4 <= len(d['title']) <= 40 and d['title'] in self.question}
            hits = search_documents(self.store, arguments['query'], 50, successful_only=True, source_ids=named or None)
            selected, counts = [], {}
            for hit in hits:
                if counts.get(hit['source_id'], 0) >= 2:
                    continue
                selected.append(hit)
                counts[hit['source_id']] = counts.get(hit['source_id'], 0) + 1
                if len(selected) >= self.top_k:
                    break
            return context_windows(self.store, selected) if selected else []
        if name != 'read_source':
            raise ToolError('工具不在白名单内')
        if (set(arguments) != {'source_id', 'section'} or not isinstance(arguments['source_id'], str)
                or not re.fullmatch(r'[a-z0-9-]{1,80}', arguments['source_id'])
                or not isinstance(arguments['section'], str) or not 1 <= len(arguments['section'].strip()) <= 100):
            raise ToolError('阅读工具参数无效')
        document = next((d for d in current_documents(self.store) if d['source_id'] == arguments['source_id']), None)
        if document is None or document['last_check_status'] != 'success':
            raise ToolError('指定来源当前不可用')
        spans = document['spans']
        index = next((i for i, span in enumerate(spans) if arguments['section'] in span['text']), None)
        if index is None:
            return []
        window = spans[index:index + 6]
        start, end = window[0]['start'], window[-1]['end']
        text = document['text'][start:end][:3000]
        return [{'source_id': document['source_id'], 'title': document['title'],
            'attachments': document.get('attachments', []),
            'source_url': document['source_url'], 'version': document['version'], 'kind': document['kind'],
            'published_at': document['published_at'], 'last_check_status': document['last_check_status'],
            'last_checked_at': document['last_checked_at'], 'id': window[0]['id'],
            'span_ids': [span['id'] for span in window], 'text': text, 'start': start, 'end': start + len(text),
            'truncated': end - start > 3000, 'locator': window[0]['locator'] + ' 至 ' + window[-1]['locator']}]
