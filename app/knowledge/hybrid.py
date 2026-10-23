"""BM25 and title lexical ranks fused with RRF; no embedding or vector claims."""
import math
import re
from collections import Counter
from app.knowledge.tokenizer import tokens
from app.knowledge.evidence import current_time

def temporal_weight(hit, query):
    """Bounded relevance boost, not legal validity or application-window confirmation."""
    requested = re.findall(r'(20\d{2})\s*(?:年|届|年度)?', query)
    target = int(requested[0]) if requested else int(current_time()[:4])
    title_years = re.findall(r'(20\d{2})\s*(?:年|届)', hit.get('title', ''))
    published = re.search(r'20\d{2}', hit.get('published_at') or '')
    year = int(title_years[0]) if title_years else int(published[0]) if published else 0
    return 1.35 if year == target and requested else 1.18 if year == target else 1.06 if year == target - 1 and not requested else 1.0

ALIASES = {'租房': '住房', '看病': '医疗医保', '找工作': '就业求职', '五险': '社会保险社保'}
def rerank(hits, query):
    if not hits: return hits
    expanded = query + ' ' + ' '.join(value for key,value in ALIASES.items() if key in query)
    terms = tokens(expanded)
    def counts(text):
        words = re.findall(r'[a-z0-9_]+', text.lower())
        for run in re.findall(r'[\u4e00-\u9fff]+', text):
            words.extend(run[i:i+2] for i in range(len(run)-1))
            if len(run) == 1: words.append(run)
        return Counter(words)
    bags = [counts(hit['text']) for hit in hits]
    lengths = [sum(bag.values()) for bag in bags]; avg = sum(lengths)/len(lengths) or 1
    dfs = {t: sum(t in bag for bag in bags) for t in terms}
    scores = []
    for bag,length in zip(bags,lengths):
        scores.append(sum(math.log(1+(len(hits)-dfs[t]+.5)/(dfs[t]+.5)) *
            (bag[t]*2.2)/(bag[t]+1.2*(.25+.75*length/avg)) for t in terms if bag[t]))
    order = sorted(range(len(hits)), key=lambda i:(-scores[i],hits[i]['id']))
    ranks = {index: rank for rank,index in enumerate(order,1)}
    merged = [{**h, 'lexical_score': h['score'], 'bm25_score': scores[i],
               'score': (1/(60+i+1)+1/(60+ranks[i])) * temporal_weight(h, query),
               'temporal_weight': temporal_weight(h, query), 'retrieval_method': 'lexical_bm25_rrf'} for i,h in enumerate(hits)]
    return sorted(merged,key=lambda h:(-h['score'],h['id']))
