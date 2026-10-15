from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from app.core.storage import Storage
from app.modules.daily_qa.execution_budget import reserve_call, finish_call, usage

NOW=datetime(2026,10,14,5,tzinfo=timezone.utc)

def test_atomic_global_daily_cap(tmp_path):
    store=Storage(tmp_path/'app.db')
    def reserve(i): return reserve_call(store,str(i//2),str(i%2),NOW,2,4)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tickets=list(pool.map(reserve,range(16)))
    assert sum(t is not None for t in tickets)==4
    assert usage(store,NOW)['used']==4

def test_cancelled_or_unknown_call_still_counts(tmp_path):
    store=Storage(tmp_path/'app.db')
    ticket=reserve_call(store,'run','vxvue',NOW,2,4)
    finish_call(store,ticket,{'cancelled':True})
    assert usage(store,NOW)['used']==1
    ticket=reserve_call(store,'run','vxvue',NOW,2,4)
    assert reserve_call(store,'run','vxvue',NOW,2,4) is None
    finish_call(store,ticket,{'cli_started':False})
    assert usage(store,NOW)['used']==1
    assert reserve_call(store,'run','vxvue',NOW,2,4)
    assert usage(store,NOW+timedelta(days=1))['used']==0
