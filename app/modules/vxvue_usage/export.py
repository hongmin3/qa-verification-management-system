"""Default exports contain only fixed classifications and metrics, never original messages."""
import csv,io,re
from app.core.security_filter import mask_text
from app.core.vxusage_storage import month_value

COLUMNS=['kind','month','channel','feature','status','evidence','calls','retries','average_duration_ms','evaluation','baseline_minutes','actual_minutes','declared_minutes_difference','tokens','cost_usd']


def safe_cell(value):
    text='' if value is None else str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text


def public_note(value):
    text=re.sub(r'\\\\[^\r\n]*','[NETWORK_PATH]',value)
    text=re.sub(r'\b[A-Za-z]:[\\/][^\r\n]*','[LOCAL_PATH]',text)
    text=re.sub(r'https?://\S+','[URL]',text,flags=re.I)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]{16,}','[SECRET]',text)
    return mask_text(text)[0]


def preview(store,include_notes=False):
    columns=COLUMNS+(['portfolio_note'] if include_notes else [])
    rows=[]
    dashboard=store.usage_dashboard()
    for row in dashboard['monthly']:
        if row['diagnostic']:continue
        rows.append({'kind':'month','month':row['month'],'channel':row['channel'],'feature':row['feature'],'status':row['status'],'evidence':row['evidence'],'calls':row['calls'],'retries':row['retries'],'average_duration_ms':round(row['duration_ms']/row['calls'],2) if row['calls'] else None})
    for row in store.usage_rows(bookmark=True,limit=100):
        if row['source']!='live':continue
        value={'kind':'case','month':month_value(row['created_at']),'channel':row['channel'],'feature':row['feature'],'status':row['status'],'evaluation':row['evaluation'],'baseline_minutes':row['baseline_minutes'],'actual_minutes':row['actual_minutes'],'declared_minutes_difference':row['baseline_minutes']-row['actual_minutes'] if row['baseline_minutes'] is not None and row['actual_minutes'] is not None else None}
        if include_notes:value['portfolio_note']=public_note(row['portfolio_note'])
        rows.append(value)
    return {'columns':columns,'rows':[{key:safe_cell(row.get(key)) for key in columns} for row in rows],
            'notice':'원문·연결 식별자는 포함하지 않습니다. 공개용 한 줄을 선택한 경우 마스킹 결과를 확인하세요. 모든 개인정보의 익명화를 보장하지 않습니다.'}


def csv_text(value):
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=value['columns'],lineterminator='\r\n')
    writer.writeheader();writer.writerows(value['rows']);return output.getvalue()

# Reviewed export snapshots contain only the selected, sanitized public columns.
# A short, bounded per-session cache keeps the download equal to its preview.
import threading,time,uuid,copy
from collections import OrderedDict
_snapshots=OrderedDict()
_snapshot_lock=threading.Lock()


def capture(value,nonce):
    ident=uuid.uuid4().hex
    with _snapshot_lock:
        now=time.monotonic()
        for key in list(_snapshots):
            if now-_snapshots[key][0]>600:_snapshots.pop(key,None)
        while len(_snapshots)>=64:_snapshots.popitem(last=False)
        _snapshots[ident]=(now,nonce,copy.deepcopy(value))
    return value|{'preview_id':ident}


def reviewed(ident,nonce):
    with _snapshot_lock:
        value=_snapshots.get(ident)
        if not value or value[1]!=nonce or time.monotonic()-value[0]>600:return None
        return copy.deepcopy(value[2])
