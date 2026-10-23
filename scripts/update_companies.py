"""Import verified official rosters into company tables and cited RAG snapshots."""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
import xlrd
from app.knowledge.companies import pdf_rows, save_directory
from app.knowledge.ingestion import OfficialFetcher, read_json, write_json, save_snapshot, current_documents
from app.knowledge.database import build_database
from app.utils.storage_budget import ensure_capacity, directory_bytes

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
 dict(id='companies-sz-sme-2025', file='sz-sme-2025.pdf', category='深圳市专精特新中小企业',cohort='2025',status='认定公示',count=2536,
      title='2025年深圳市专精特新中小企业公示名单',published_at='2026-01-09',
      url='https://zxqyj.sz.gov.cn/attachment/1/1671/1671112/12595049.pdf',notice='https://zxqyj.sz.gov.cn/gkmlpt/content/12/12595/post_12595049.html'),
 dict(id='companies-sz-review-2025',file='sz-review-2025.pdf',category='深圳市专精特新中小企业',cohort='2022认定／2025复核',status='复核公示',count=2746,
      title='2022年深圳市专精特新中小企业复核通过公示名单',published_at='2026-01-09',
      url='https://zxqyj.sz.gov.cn/attachment/1/1671/1671113/12595049.pdf',notice='https://zxqyj.sz.gov.cn/gkmlpt/content/12/12595/post_12595049.html'),
 dict(id='companies-sz-cancel-2026',file='sz-cancel-2026.pdf',category='专精特新／创新型混合取消名单（类别待核实）',cohort='2026第一批',status='取消认定名单涉及',count=68,
      title='取消认定的专精特新中小企业、创新型中小企业名单（2026年第一批）',published_at=None,
      url='https://www.sz.gov.cn/attachment/1/1738/1738951/12875438.pdf',notice='https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/content/mpost_12875438.html'),
]

def main():
    store=ROOT/'data/store'; directory=store/'company-attachments'
    ensure_capacity(ROOT, 65_000_000)
    directory.mkdir(exist_ok=True)
    fetcher=OfficialFetcher(['zxqyj.sz.gov.cn','www.sz.gov.cn','gdii.gd.gov.cn'],tls_curve='prime256v1',max_bytes=20_000_000)
    batches=[]
    for original in SOURCES:
        batch=dict(original)
        raw,_,_=fetcher.fetch(batch['url'])
        ensure_capacity(ROOT,len(raw)*3+1_000_000)
        path=directory/batch['file'];path.write_bytes(raw)
        batch['sha256']=hashlib.sha256(raw).hexdigest()
        batch['rows']=pdf_rows(path)
        if len(batch['rows'])!=batch['count']: raise ValueError('附件条数与核对数量不符')
        if not batch['published_at']:
            notice,_,enc=fetcher.fetch(batch['notice'])
            soup=BeautifulSoup(notice,'html.parser',from_encoding=enc)
            meta=soup.find('meta',attrs={'name':'PubDate'}) or soup.find('meta',attrs={'name':'pubdate'})
            if not meta: raise ValueError('取消通知缺少发布日期')
            batch['published_at']=meta['content']
        batches.append(batch)
    # Provincial list is checked by its region column; never infer Shenzhen from a name alone.
    raw,_,_=fetcher.fetch('https://gdii.gd.gov.cn/attachment/0/606/606639/4856033.xls')
    ensure_capacity(ROOT,len(raw)*2+1_000_000)
    (directory/'gd-sme-2025.xls').write_bytes(raw)
    sheet=xlrd.open_workbook(file_contents=raw).sheet_by_index(0)
    provincial_rows=[sheet.row_values(i) for i in range(3,sheet.nrows)]
    provincial_sz=[row for row in provincial_rows if '深圳' in str(row[1])]
    # Province data is retained for audit only: this importer handles Shenzhen PDF rosters.
    write_json(ROOT/'data/sources/company-catalog.json',{'schema_version':1,'sources':SOURCES,
        'province_notice':'https://gdii.gd.gov.cn/gkmlpt/content/4/4856/post_4856033.html',
        'national_pending':{'url':'https://zxqyj.sz.gov.cn/zwgk/zfxxgkml/tzgg/content/post_12444316.html','reason':'official original HTTP 404; no secondary roster imported'}})
    stats=save_directory(store/'policies.sqlite3',batches)
    config=read_json(ROOT/'data/sources/shenzhen.json')
    for batch in batches:
        lines=[f"企业目录；{batch['category']}；状态：{batch['status']}；批次：{batch['cohort']}。此名单不证明当前招聘、获得补贴到账或认定目前有效。"]
        lines += [f"{r['ordinal']} {r['name']}；{batch['status']}；招聘情况未核实。" for r in batch['rows']]
        text='\n'.join(lines);offset=0;spans=[]
        for index,line in enumerate(lines):
            locator='名单说明' if index==0 else f"PDF第{batch['rows'][index-1]['page']}页／序号{batch['rows'][index-1]['ordinal']}"
            spans.append(dict(id=f"{batch['id']}:p{index+1:04d}",text=line,start=offset,end=offset+len(line),locator=locator))
            offset+=len(line)+1
        source=dict(id=batch['id'],url=batch['url'],selector='pdf-company-roster',kind='company_directory',
                    topic='就业与人才 企业目录 专精特新',status='pending',related_ids=[])
        parsed=dict(title=batch['title'],text=text,spans=spans,published_at=batch['published_at'],publisher='深圳市中小企业服务局',
                    attachments=[{'url':batch['url'],'title':batch['file']}],attachment_sha256=batch['sha256'],notice_url=batch['notice'])
        # raw.html is the existing generic snapshot filename; it contains original PDF bytes here.
        save_snapshot(store,source,(directory/batch['file']).read_bytes(),parsed,'official_pdf_text_extraction')
        config['sources']=[s for s in config['sources'] if s['id']!=source['id']]+[source]
    config['allowed_hosts']=sorted(set(config['allowed_hosts'])|{'zxqyj.sz.gov.cn'})
    write_json(ROOT/'data/sources/shenzhen.json',config)
    report={**stats,'batches':[{k:v for k,v in b.items() if k!='rows'} for b in batches],
      'provincial_rows':len(provincial_rows),'provincial_shenzhen_rows':len(provincial_sz),
      'national':'pending official source recovery','jobs':'not collected',
      'database':build_database(store,current_documents(store)),'bytes':directory_bytes(ROOT)}
    write_json(ROOT/'outputs/company-import-report.json',report)
    print({k:v for k,v in report.items() if k!='batches'})

if __name__=='__main__': main()
