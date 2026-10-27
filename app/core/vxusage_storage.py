"""Storage-owned, isolated usage tables; no existing conversation retention changes."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
import re
import sqlite3
import uuid

CHANNELS={'desktop','http_mcp','web_chat','owner_ui'}
FEATURES={'documents','specification','testcase','issue','manual','generator','polarion','redmine','other','diagnostic'}
TOOLS={'get_status','get_workflow','search_documents','get_document','get_document_page','get_document_pages','find_testcases','compare_packages','trace_requirements','get_generator_evidence','read_attachment','batch_lookup','get_polarion_item','polarion_query','read_polarion_result','get_redmine_issue','search_redmine_issues','read_redmine_result','get_manual_review','make_manual_review_report','begin_vxvue_activity','finish_vxvue_activity','unknown'}
EVIDENCE={'document','alm','issue','generator','manual','attachment','none','unknown'}
STATES={'IN_PROGRESS','ANSWER_RECORDED','ANSWER_GENERATED','INCOMPLETE','FAILED','CANCELLED'}

def now_iso():return datetime.now(timezone.utc).isoformat()

def month_value(value):return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m')

def uuid_value(value):
    try:return str(uuid.UUID(str(value)))
    except (ValueError,TypeError,AttributeError):raise ValueError('UUID 형식을 확인하세요.') from None

def hex_value(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('연결 확인값 형식이 맞지 않습니다.')
    return value

def text_value(value,limit=30000):
    if not isinstance(value,str) or len(value)>limit:raise ValueError('글자 수와 입력 형식을 확인하세요.')
    return value


class VxUsageStorageMixin:
    def _initialize_vxusage(self):
        with closing(self.connect()) as db, db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS vxusage_events(
                event_id TEXT PRIMARY KEY,occurred_at TEXT NOT NULL,month TEXT NOT NULL,
                channel TEXT NOT NULL,feature TEXT NOT NULL,tool TEXT NOT NULL,evidence TEXT NOT NULL,
                status TEXT NOT NULL,duration_ms INTEGER NOT NULL,actor_tag TEXT,activity_id TEXT,
                attempt INTEGER NOT NULL,source TEXT NOT NULL,diagnostic INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS vxusage_event_time ON vxusage_events(occurred_at);
            CREATE INDEX IF NOT EXISTS vxusage_event_activity ON vxusage_events(activity_id);
            CREATE TABLE IF NOT EXISTS vxusage_activities(
                id TEXT PRIMARY KEY,binding TEXT NOT NULL,owner_scope INTEGER NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,channel TEXT NOT NULL,feature TEXT NOT NULL,
                status TEXT NOT NULL,source TEXT NOT NULL,save_original INTEGER NOT NULL,
                question TEXT NOT NULL DEFAULT '',answer TEXT NOT NULL DEFAULT '',summary TEXT NOT NULL DEFAULT '',
                portfolio_note TEXT NOT NULL DEFAULT '',bookmark INTEGER NOT NULL DEFAULT 0,evaluation TEXT NOT NULL DEFAULT 'unrated',
                baseline_minutes REAL,actual_minutes REAL);
            CREATE INDEX IF NOT EXISTS vxusage_activity_time ON vxusage_activities(created_at);
            CREATE TABLE IF NOT EXISTS vxusage_monthly(
                month TEXT NOT NULL,channel TEXT NOT NULL,feature TEXT NOT NULL,tool TEXT NOT NULL,
                evidence TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,diagnostic INTEGER NOT NULL,
                calls INTEGER NOT NULL,retries INTEGER NOT NULL,duration_ms INTEGER NOT NULL,
                PRIMARY KEY(month,channel,feature,tool,evidence,status,source,diagnostic));
            CREATE TABLE IF NOT EXISTS vxusage_monthly_tasks(
                month TEXT NOT NULL,channel TEXT NOT NULL,feature TEXT NOT NULL,status TEXT NOT NULL,
                source TEXT NOT NULL,tasks INTEGER NOT NULL,PRIMARY KEY(month,channel,feature,status,source));
            CREATE TABLE IF NOT EXISTS vxusage_web_bindings(binding TEXT PRIMARY KEY,created_at TEXT NOT NULL);
            ''')
            if 'portfolio_note' not in {r[1] for r in db.execute('PRAGMA table_info(vxusage_activities)')}:
                db.execute("ALTER TABLE vxusage_activities ADD COLUMN portfolio_note TEXT NOT NULL DEFAULT ''")

    def _usage_connect(self):
        # This optional observer must never create an empty application DB or wait seconds on a lock.
        from app.core.private_files import require_private_sqlite
        require_private_sqlite(self.db_path)
        db=sqlite3.connect('file:'+self.db_path.resolve().as_posix()+'?mode=rw',uri=True,timeout=0.05)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        return db

    def usage_event(self,value,*,binding=None):
        expected={'event_id','occurred_at','channel','feature','tool','evidence','status','duration_ms','actor_tag','activity_id','attempt','source','diagnostic'}
        if set(value)!=expected:raise ValueError('허용된 실행 메타데이터만 기록할 수 있습니다.')
        v=dict(value);v['event_id']=uuid_value(v['event_id'])
        timestamp=datetime.fromisoformat(v['occurred_at'])
        if timestamp.tzinfo is None:raise ValueError('UTC 시각이 필요합니다.')
        v['occurred_at']=timestamp.astimezone(timezone.utc).isoformat();v['month']=month_value(v['occurred_at'])
        for key,allowed in [('channel',CHANNELS),('feature',FEATURES),('tool',TOOLS),('evidence',EVIDENCE),('status',{'success','error'}),('source',{'live','test'})]:
            if v[key] not in allowed:raise ValueError('허용된 분류값이 아닙니다.')
        for key,maximum in [('duration_ms',86400000),('attempt',20)]:
            if isinstance(v[key],bool) or not isinstance(v[key],int) or not (1 if key=='attempt' else 0)<=v[key]<=maximum:raise ValueError('실행 수치 형식이 맞지 않습니다.')
        if not isinstance(v['diagnostic'],bool):raise ValueError('진단 분류 형식을 확인하세요.')
        if v['actor_tag'] is not None:hex_value(v['actor_tag'])
        if v['activity_id'] is not None:v['activity_id']=uuid_value(v['activity_id'])
        with closing(self._usage_connect()) as db,db:
            if v['activity_id']:
                a=db.execute('SELECT binding,source FROM vxusage_activities WHERE id=?',(v['activity_id'],)).fetchone()
                if not a or binding!=a['binding']:v['activity_id']=None
                else:v['source']=a['source']
            columns=['event_id','occurred_at','month','channel','feature','tool','evidence','status','duration_ms','actor_tag','activity_id','attempt','source','diagnostic']
            added=db.execute('INSERT OR IGNORE INTO vxusage_events('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[v[k] for k in columns]).rowcount
            if added:
                keys=[v[k] for k in ('month','channel','feature','tool','evidence','status','source','diagnostic')]
                db.execute('''INSERT INTO vxusage_monthly VALUES(?,?,?,?,?,?,?,?,1,?,?)
                    ON CONFLICT(month,channel,feature,tool,evidence,status,source,diagnostic)
                    DO UPDATE SET calls=calls+1,retries=retries+excluded.retries,duration_ms=duration_ms+excluded.duration_ms''',keys+[int(v['attempt']>1),v['duration_ms']])
        return bool(added)

    @staticmethod
    def _usage_task_bucket(db,row,delta):
        db.execute('''INSERT INTO vxusage_monthly_tasks VALUES(?,?,?,?,?,?)
            ON CONFLICT(month,channel,feature,status,source) DO UPDATE SET tasks=tasks+excluded.tasks''',
            (month_value(row['created_at']),row['channel'],row['feature'],row['status'],row['source'],delta))

    def usage_start(self,activity_id,binding,channel,feature,*,question='',save_original=False,scope='owner',source='live'):
        activity_id=uuid_value(activity_id);hex_value(binding);text_value(question)
        if channel not in CHANNELS or feature not in FEATURES or source not in {'live','test'} or scope not in {'owner','anonymous'} or not isinstance(save_original,bool):raise ValueError('업무 분류를 확인하세요.')
        original=save_original and scope=='owner';now=now_iso()
        row=dict(id=activity_id,binding=binding,owner_scope=int(scope=='owner'),created_at=now,updated_at=now,channel=channel,feature=feature,status='IN_PROGRESS',source=source,save_original=int(original),question=question if original else '')
        with closing(self._usage_connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT binding FROM vxusage_activities WHERE id=?',(activity_id,)).fetchone()
            if existing:
                if existing['binding']!=binding:raise ValueError('이 업무에 접근할 수 없습니다.')
                return activity_id
            db.execute('INSERT INTO vxusage_activities('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
            self._usage_task_bucket(db,row,1)
        return activity_id

    def usage_finish(self,activity_id,binding,*,status='ANSWER_RECORDED',answer=None,summary=None):
        activity_id=uuid_value(activity_id)
        if answer is not None:text_value(answer)
        if summary is not None:text_value(summary,12000)
        if status not in STATES-{'IN_PROGRESS'}:raise ValueError('업무 상태를 확인하세요.')
        with closing(self._usage_connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            found=db.execute('SELECT * FROM vxusage_activities WHERE id=? AND binding=?',(activity_id,binding)).fetchone()
            if not found:return False
            row=dict(found)
            answer=row['answer'] if answer is None else answer
            summary=row['summary'] if summary is None else summary
            if status=='ANSWER_RECORDED' and row['owner_scope'] and row['save_original'] and not answer.strip():
                raise ValueError('최종 답변이 없으면 미완료·실패·취소 상태를 지정하세요.')
            if row['status']!=status:
                self._usage_task_bucket(db,row,-1);self._usage_task_bucket(db,row|{'status':status},1)
            db.execute('UPDATE vxusage_activities SET status=?,answer=?,summary=?,updated_at=? WHERE id=?',
                (status,answer if row['owner_scope'] and row['save_original'] else '',summary if row['owner_scope'] else '',now_iso(),activity_id))
        return True

    def usage_get(self,activity_id):
        with closing(self._usage_connect()) as db:
            row=db.execute('SELECT * FROM vxusage_activities WHERE id=? AND owner_scope=1 AND created_at>=?',(uuid_value(activity_id),(datetime.now(timezone.utc)-timedelta(days=365)).isoformat())).fetchone()
        return dict(row) if row else None

    def usage_rows(self,search='',feature='',status='',bookmark=False,offset=0,limit=50):
        if isinstance(offset,bool) or not isinstance(offset,int) or offset<0 or not isinstance(limit,int) or not 1<=limit<=100:raise ValueError('조회 범위를 확인하세요.')
        text_value(search,200)
        where=['owner_scope=1','created_at>=?'];values=[(datetime.now(timezone.utc)-timedelta(days=365)).isoformat()]
        for key,value,allowed in [('feature',feature,FEATURES),('status',status,STATES)]:
            if value:
                if value not in allowed:raise ValueError('조회 분류를 확인하세요.')
                where.append(key+'=?');values.append(value)
        if bookmark:where.append('bookmark=1')
        if search:
            where.append('(question LIKE ? OR answer LIKE ? OR summary LIKE ?)');values += ['%'+search+'%']*3
        with closing(self._usage_connect()) as db:
            rows=db.execute('SELECT * FROM vxusage_activities WHERE '+' AND '.join(where)+' ORDER BY created_at DESC,id LIMIT ? OFFSET ?',values+[limit,offset]).fetchall()
        return [dict(row) for row in rows]

    def usage_feedback(self,activity_id,*,bookmark=False,evaluation='unrated',baseline_minutes=None,actual_minutes=None,portfolio_note=''):
        text_value(portfolio_note,1000)
        if not isinstance(bookmark,bool) or evaluation not in {'unrated','used','modified','discarded'}:raise ValueError('활용 평가를 확인하세요.')
        for value in (baseline_minutes,actual_minutes):
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=100000):raise ValueError('시간 표본을 확인하세요.')
        with closing(self._usage_connect()) as db,db:
            return bool(db.execute('UPDATE vxusage_activities SET bookmark=?,evaluation=?,baseline_minutes=?,actual_minutes=?,portfolio_note=?,updated_at=? WHERE id=? AND owner_scope=1',
                (bookmark,evaluation,baseline_minutes,actual_minutes,portfolio_note,now_iso(),uuid_value(activity_id))).rowcount)

    def usage_bind_web(self,binding):
        hex_value(binding)
        with closing(self._usage_connect()) as db,db:db.execute('INSERT OR IGNORE INTO vxusage_web_bindings VALUES(?,?)',(binding,now_iso()))

    def usage_web_bound(self,binding):
        with closing(self._usage_connect()) as db:return bool(db.execute('SELECT 1 FROM vxusage_web_bindings WHERE binding=?',(binding,)).fetchone())

    def usage_dashboard(self):
        with closing(self._usage_connect()) as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM vxusage_monthly WHERE source='live' ORDER BY month DESC,feature,channel")]
            tasks=[dict(r) for r in db.execute("SELECT * FROM vxusage_monthly_tasks WHERE source='live' AND tasks>0 ORDER BY month DESC")]
            unknown=db.execute("SELECT count(*) FROM vxusage_events WHERE source='live' AND diagnostic=0 AND actor_tag IS NULL AND month=?",(month_value(now_iso()),)).fetchone()[0]
            devices=db.execute("SELECT count(DISTINCT actor_tag) FROM vxusage_events WHERE source='live' AND diagnostic=0 AND month=?",(month_value(now_iso()),)).fetchone()[0]
        functional=[r for r in rows if not r['diagnostic']]
        count=sum(r['calls'] for r in functional)
        return {'calls':count,'retries':sum(r['retries'] for r in functional),'errors':sum(r['calls'] for r in functional if r['status']=='error'),
            'diagnostics':sum(r['calls'] for r in rows if r['diagnostic']),'tasks':sum(r['tasks'] for r in tasks),'monthly':rows,'task_states':tasks,
            'average_duration_ms':sum(r['duration_ms'] for r in functional)/count if count else None,'active_connections':devices,
            'unattributed_connection_calls':unknown,'active_connections_month':month_value(now_iso()),'cost_usd':None,'tokens':None,'quality_accuracy':None}

    def usage_retention(self,now=None):
        now=now or datetime.now(timezone.utc);cutoff=(now-timedelta(days=365)).isoformat()
        local=now.astimezone(timezone(timedelta(hours=9)));index=local.year*12+local.month-1-35;oldest=f'{index//12:04d}-{index%12+1:02d}'
        with closing(self._usage_connect()) as db,db:
            events=db.execute('DELETE FROM vxusage_events WHERE occurred_at<?',(cutoff,)).rowcount
            activities=db.execute('DELETE FROM vxusage_activities WHERE created_at<?',(cutoff,)).rowcount
            bindings=db.execute('DELETE FROM vxusage_web_bindings WHERE created_at<?',(cutoff,)).rowcount
            for table in ('vxusage_monthly','vxusage_monthly_tasks'):db.execute('DELETE FROM '+table+' WHERE month<?',(oldest,))
        return {'events':events,'activities':activities,'bindings':bindings}

    def usage_web_jobs(self,web_owner):
        cutoff=(datetime.now(timezone.utc)-timedelta(days=365)).isoformat()
        with closing(self._usage_connect()) as db:
            return [dict(r) for r in db.execute('''SELECT j.id,j.status,j.created_at,substr(m.content,1,160) AS question
                FROM vxchat_jobs j JOIN vxchat_conversations c ON c.id=j.conversation_id
                JOIN vxchat_messages m ON m.id=j.user_message_id
                WHERE c.owner=? AND j.created_at>=? ORDER BY j.created_at DESC LIMIT 50''',(web_owner,cutoff))]

    def usage_import_web(self,activity_id,binding,question,answer,status,*,save_original=False,summary='',feature='other',created_at=None):
        activity_id=uuid_value(activity_id);hex_value(binding);text_value(question);text_value(answer);text_value(summary,12000)
        if not isinstance(save_original,bool) or feature not in FEATURES or status not in STATES:raise ValueError('선택한 기록 값을 확인하세요.')
        created_at=created_at or now_iso()
        if datetime.fromisoformat(created_at)<datetime.now(timezone.utc)-timedelta(days=365):raise ValueError('최근 365일의 웹 작업만 선택할 수 있습니다.')
        with closing(self._usage_connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM vxusage_activities WHERE id=?',(activity_id,)).fetchone()
            if existing:
                row=dict(existing)
                if row['binding']!=binding:raise ValueError('이 웹 작업을 선택할 수 없습니다.')
                if row['status']!=status or row['feature']!=feature:
                    self._usage_task_bucket(db,row,-1);self._usage_task_bucket(db,row|{'status':status,'feature':feature},1)
                db.execute('UPDATE vxusage_activities SET owner_scope=1,save_original=?,question=?,answer=?,summary=?,status=?,feature=?,updated_at=? WHERE id=?',
                    (save_original,question if save_original else '',answer if save_original else '',summary,status,feature,now_iso(),activity_id))
            else:
                row=dict(id=activity_id,binding=binding,owner_scope=1,created_at=created_at,updated_at=now_iso(),channel='web_chat',feature=feature,status=status,source='live',save_original=int(save_original),question=question if save_original else '',answer=answer if save_original else '',summary=summary)
                db.execute('INSERT INTO vxusage_activities('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
                self._usage_task_bucket(db,row,1)
        return activity_id


    def usage_active(self,binding):
        if not binding:return None
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with closing(self._usage_connect()) as db:
            rows=db.execute("SELECT id FROM vxusage_activities WHERE binding=? AND owner_scope=1 AND source='live' AND status='IN_PROGRESS' AND created_at>=? LIMIT 2",(binding,cutoff)).fetchall()
        return rows[0]['id'] if len(rows)==1 else None


    def usage_retention_if_present(self,now=None):
        if not self.db_path.exists():return {'events':0,'activities':0,'bindings':0}
        with closing(self._usage_connect()) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vxusage_%'")}
        if not {'vxusage_events','vxusage_activities','vxusage_monthly','vxusage_monthly_tasks','vxusage_web_bindings'}<=tables:
            return {'events':0,'activities':0,'bindings':0}
        return self.usage_retention(now)
