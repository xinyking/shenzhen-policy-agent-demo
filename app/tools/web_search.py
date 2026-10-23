"""Tavily discovers URLs; only directly fetched official article text is evidence."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from app.knowledge.ingestion import OfficialFetcher, parse_html, document_version
from app.knowledge.evidence import current_time

class SearchError(ValueError):
    pass

def official_url(url):
    if not isinstance(url,str): return False
    try:
        u=urlparse(url)
        return (u.scheme=='https' and (u.hostname=='sz.gov.cn' or (u.hostname or '').endswith('.sz.gov.cn'))
                and not u.username and not u.password and u.port in (None,443))
    except ValueError:
        return False

class TavilySearch:
    def __init__(self, private_path):
        self.key=os.environ.get('TAVILY_API_KEY','')
        self.enabled=True
        if not self.key and Path(private_path).is_file():
            try:
                with Path(private_path).open('rb') as stream: raw=stream.read(8193)
                if len(raw)>8192: raise ValueError()
                data=json.loads(raw)
                self.enabled=data.get('enabled',True) is True
                key=data.get('api_key','')
                if not isinstance(key,str) or len(key)>300: raise ValueError()
                self.key=key
            except (OSError,ValueError,TypeError):
                self.key=''

    @property
    def configured(self): return self.enabled and bool(self.key)

    async def candidates(self, query):
        if not self.configured: return []
        try:
            async with httpx.AsyncClient(timeout=12,follow_redirects=False) as client:
                async with client.stream('POST','https://api.tavily.com/search',
                    headers={'Authorization':'Bearer '+self.key},json={'query':query[:500],
                    'search_depth':'basic','max_results':4,'include_answer':False,
                    'include_raw_content':False,'include_domains':['sz.gov.cn']}) as response:
                    if response.status_code!=200:
                        raise SearchError('联网搜索未成功（认证、额度或服务异常）')
                    raw=bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>1_000_000: raise SearchError('搜索响应超过大小限制')
            result=json.loads(raw)
            if not isinstance(result,dict) or not isinstance(result.get('results'),list):
                raise SearchError('搜索结果格式异常')
            urls=[]
            for item in result['results'][:10]:
                url=item.get('url') if isinstance(item,dict) else None
                if official_url(url) and url not in urls: urls.append(url)
            return urls[:2]
        except (httpx.HTTPError,ValueError) as error:
            if isinstance(error,SearchError): raise
            raise SearchError('联网搜索暂不可用') from None

    @staticmethod
    def read_article(url, query):
        host=urlparse(url).hostname
        fetcher=OfficialFetcher([host],tls_curve='prime256v1',interval=0)
        raw,kind,charset=fetcher.fetch(url)
        if kind not in ('text/html','application/xhtml+xml'): raise SearchError('仅支持官方HTML正文')
        soup=BeautifulSoup(raw,'html.parser',from_encoding=charset)
        selector=next((s for s in ('.articlebox','.detailContent','#articleBox','.articleBox',
            '.news_cont_d_wrap','.article-content','.TRS_Editor','.viewTRS','#zoom') if soup.select_one(s)),None)
        if not selector: raise SearchError('官方正文暂无法读取')
        sid='web-'+hashlib.sha256(url.encode()).hexdigest()[:20]
        parsed=parse_html(raw,{'id':sid,'url':url,'selector':selector},charset)
        from app.knowledge.tokenizer import tokens
        terms=tokens(query)
        ranked=sorted(parsed['spans'],key=lambda s:-len(terms & tokens(s['text'])))
        selected=sorted(ranked[:6],key=lambda s:s['start'])
        text='\n'.join(s['text'] for s in selected)[:2400]
        version=document_version(parsed,url)
        return dict(source_id=sid,source_url=url,title=parsed['title'],text=text,version=version,
            published_at=parsed['published_at'],last_checked_at=current_time(),last_check_status='success',
            kind='live_official_article',attachments=parsed['attachments'],id=sid+':excerpt',
            start=0,end=len(text),locator='官方网页节选／'+ '、'.join(s['locator'] for s in selected),
            acquisition='live_web',excerpt_sha256=hashlib.sha256(text.encode()).hexdigest())

    async def search(self, query):
        try:
            async with asyncio.timeout(32):
                urls=await self.candidates(query)
                results=await asyncio.gather(*(asyncio.to_thread(self.read_article,url,query) for url in urls),return_exceptions=True)
                return [r for r in results if isinstance(r,dict)]
        except TimeoutError:
            raise SearchError('联网搜索或官方正文读取超时') from None
