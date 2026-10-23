"""Bounded recent official livelihood guides, preserving existing policy/company sources."""
import argparse
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from app.knowledge.ingestion import OfficialFetcher, parse_html, save_snapshot, read_json, write_json, current_documents
from app.knowledge.database import build_database
from app.knowledge.domains import DOMAINS, document_domains
from app.utils.storage_budget import ensure_capacity, directory_bytes, StorageLimit
from scripts.refresh_employment import age_status

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=65)
    parser.add_argument('--retry-failed',action='store_true')
    args=parser.parse_args()
    if not 1<=args.limit<=150: parser.error('limit must be 1..150')
    root=Path(__file__).resolve().parents[1];store=root/'data/store'
    catalog=read_json(root/'data/sources/livelihood-catalog.json')
    config_path=root/'data/sources/shenzhen.json';config=read_json(config_path)
    hosts=sorted(set(config['allowed_hosts'])|{'ga.sz.gov.cn','mzj.sz.gov.cn','fgw.sz.gov.cn'})
    fetcher=OfficialFetcher(hosts,tls_curve='prime256v1')
    candidates={s['url']:s['topic'] for s in catalog['seeds']}
    report={'cutoff':catalog['cutoff'],'as_of':catalog['as_of'],'directories':[],'results':[]}
    ensure_capacity(root,1_000_000)
    for directory in ([] if args.retry_failed else catalog['directories']):
        try:
            raw,_,charset=fetcher.fetch(directory['url'])
            soup=BeautifulSoup(raw,'html.parser',from_encoding=charset);count=0
            for anchor in soup.select('a[href]'):
                url=urljoin(directory['url'],anchor['href']).split('#')[0]
                if (urlparse(url).hostname in hosts and re.search(r'/m?post_\d+\.html$',url)
                    and re.search(directory['pattern'],anchor.get_text(' ',strip=True))):
                    candidates.setdefault(url,directory['topic']);count+=1
            report['directories'].append({**directory,'ok':True,'links':count})
        except (ValueError,OSError) as error:
            report['directories'].append({**directory,'ok':False,'error':type(error).__name__})
    if args.retry_failed:
        previous=read_json(root/'outputs/livelihood-refresh-report.json')
        candidates={r['url']:r['topic'] for r in previous['results'] if r['status']=='failed'}
        report['directories']=previous['directories']
        report['results']=[r for r in previous['results'] if r['status']!='failed']
    existing={s['url']:s for s in config['sources']}
    for url,topic in list(candidates.items())[:args.limit]:
        try:
            raw,kind,charset=fetcher.fetch(url)
            if kind not in ('text/html','application/xhtml+xml'): raise ValueError('not HTML')
            soup=BeautifulSoup(raw,'html.parser',from_encoding=charset)
            selector=next((s for s in ('.articlebox','.detailContent','#articleBox','.articleBox','.news_cont_d_wrap','.article-content','.TRS_Editor','.viewTRS','#zoom') if soup.select_one(s)),None)
            if not selector: raise ValueError('article selector not found')
            source=dict(existing.get(url) or {'id':'livelihood-'+re.search(r'post_(\d+)',url).group(1),'related_ids':[]},
                url=url,topic=topic,selector=selector,kind='livelihood_guide',status='pending')
            parsed=parse_html(raw,source,charset)
            status=age_status(parsed['published_at'],date.fromisoformat(catalog['cutoff']),date.fromisoformat(catalog['as_of']))
            if status!='recent':
                report['results'].append({'url':url,'topic':topic,'status':status});continue
            # Drafts are recorded as drafts, never a currently effective rule.
            if re.search('征求意见|征求.*意见稿',parsed['title']):
                report['results'].append({'url':url,'topic':topic,'status':'draft_skipped'});continue
            result=save_snapshot(store,source,raw,parsed)
            existing={u:s for u,s in existing.items() if s['id']!=source['id']}
            existing[url]=source
            report['results'].append({'url':url,'topic':topic,'title':parsed['title'],'status':'saved',**result})
            config.update(allowed_hosts=hosts,sources=list(existing.values()))
            write_json(config_path,config)
        except StorageLimit:
            report['stopped']='10GB capacity limit';break
        except (ValueError,OSError) as error:
            report['results'].append({'url':url,'topic':topic,'status':'failed','error':type(error).__name__})
        write_json(root/'outputs/livelihood-refresh-report.json',report)
    documents=current_documents(store)
    report['database']=build_database(store,documents)
    write_json(root/'data/sources/domain-catalog.json',{'schema_version':1,'complete_city_coverage':False,
        'domains':[{'id':key,'name':value[0],'sources':[{'source_id':d['source_id'],'title':d['title'],
            'url':d['source_url'],'published_at':d['published_at']} for d in documents if key in document_domains(d)]}
            for key,value in DOMAINS.items()]})
    report['bytes']=directory_bytes(root)
    write_json(root/'outputs/livelihood-refresh-report.json',report)
    print({'saved':sum(r['status']=='saved' for r in report['results']),**report['database'],'bytes':report['bytes']})

if __name__=='__main__': main()
