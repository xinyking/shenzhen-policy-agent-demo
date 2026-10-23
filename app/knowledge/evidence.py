"""Time and evidence availability metadata, separate from policy facts."""
from datetime import datetime, timezone, timedelta

def current_time():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()

def evidence_payload(evidence):
    return [{**{key:item[key] for key in ('evidence_id','title','text','source_url','published_at','last_checked_at')},
             'attachments':item.get('attachments',[])[:8], 'attachment_text_available':False}
            for item in evidence]
