"""SQLite ownership for QA call reservations and immutable prepared selections."""
import json
import uuid

class QaExecutionStorageMixin:
    def _initialize_qa_execution(self):
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS qa_call_reservations (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, product TEXT NOT NULL,
                day TEXT NOT NULL, state TEXT NOT NULL, outcome_json TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS qa_call_day ON qa_call_reservations(day,state);
            CREATE TABLE IF NOT EXISTS qa_execution_plans (
                id TEXT PRIMARY KEY, product TEXT NOT NULL, created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS qa_execution_selections (
                id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, product TEXT NOT NULL,
                payload_json TEXT NOT NULL, claimed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS qa_result_links (
                run_id TEXT NOT NULL, finding_id INTEGER NOT NULL, kind TEXT NOT NULL,
                subject TEXT NOT NULL, PRIMARY KEY(run_id,finding_id));
            CREATE TABLE IF NOT EXISTS qa_issue_reporters (
                product TEXT NOT NULL, subject TEXT NOT NULL, reporter_id TEXT NOT NULL,
                reporter_name TEXT NOT NULL, PRIMARY KEY(product,subject));
            CREATE TABLE IF NOT EXISTS qa_processing (
                finding_id INTEGER PRIMARY KEY, state TEXT NOT NULL, actor TEXT NOT NULL,
                note TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS qa_processing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id INTEGER NOT NULL,
                state TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(qa_issue_reporters)')}
            if 'issue_updated_at' not in columns:
                db.execute("ALTER TABLE qa_issue_reporters ADD COLUMN issue_updated_at TEXT NOT NULL DEFAULT ''")

    def qa_reserve_call(self, run_id, product, day, per_run, per_day):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            daily=db.execute("SELECT count(*) FROM qa_call_reservations WHERE day=? AND state!='not_started'",(day,)).fetchone()[0]
            run=db.execute("SELECT count(*) FROM qa_call_reservations WHERE run_id=? AND state!='not_started'",(run_id,)).fetchone()[0]
            if daily >= per_day or run >= per_run: return None
            key=uuid.uuid4().hex
            db.execute("INSERT INTO qa_call_reservations(id,run_id,product,day,state) VALUES(?,?,?,?,'reserved')",(key,run_id,product,day))
            return key

    def qa_finish_call(self, key, outcome):
        state='not_started' if outcome.get('cli_started') is False else ('finished' if outcome.get('usage') else 'usage_unknown')
        with self.connect() as db:
            db.execute("UPDATE qa_call_reservations SET state=?,outcome_json=? WHERE id=? AND state='reserved'",(state,json.dumps(outcome),key))

    def qa_run_calls(self, run_id):
        """한 실행의 호출 기록(시작 순서): `[{"state": ..., "outcome": {...}}]`. 읽을 수 없는 기록은 빈 결과로 둔다."""
        with self.connect() as db:
            rows = db.execute('SELECT state,outcome_json FROM qa_call_reservations WHERE run_id=? ORDER BY rowid', (run_id,)).fetchall()
        calls = []
        for state, raw in rows:
            try:
                outcome = json.loads(raw or '{}')
            except ValueError:
                outcome = {}
            calls.append({'state': state, 'outcome': outcome if isinstance(outcome, dict) else {}})
        return calls

    def qa_call_usage(self, day):
        with self.connect() as db:
            rows=db.execute('SELECT state,count(*) AS n FROM qa_call_reservations WHERE day=? GROUP BY state',(day,)).fetchall()
        counts={row['state']:row['n'] for row in rows}
        return {'used':sum(n for state,n in counts.items() if state!='not_started'),'states':counts,'day':day}

    def qa_save_plan(self, payload):
        with self.connect() as db:
            db.execute('INSERT INTO qa_execution_plans VALUES(?,?,?,?)',(payload['id'],payload['product'],payload['created_at'],json.dumps(payload,ensure_ascii=False)))

    def qa_get_plan(self, key='', product=''):
        with self.connect() as db:
            row=db.execute('SELECT payload_json FROM qa_execution_plans WHERE id=? AND product=?',(key,product)).fetchone() if key else db.execute('SELECT payload_json FROM qa_execution_plans WHERE product=? ORDER BY created_at DESC LIMIT 1',(product,)).fetchone()
        return json.loads(row[0]) if row else None

    def qa_save_selection(self, plan_id, product, payload):
        key=uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO qa_execution_selections VALUES(?,?,?,?,0)',(key,plan_id,product,json.dumps(payload)))
        return key

    def qa_claim_selection(self, key, product):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload_json FROM qa_execution_selections WHERE id=? AND product=? AND claimed=0',(key,product)).fetchone()
            if row is None: return None
            db.execute('UPDATE qa_execution_selections SET claimed=1 WHERE id=?',(key,))
            return json.loads(row[0])

    def qa_link_results(self, run_id, finding_ids, kind, subject):
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO qa_result_links VALUES(?,?,?,?)',
                           [(run_id, finding_id, kind, subject) for finding_id in finding_ids])

    def qa_save_reporters(self, product, issues):
        with self.connect() as db:
            for item in issues:
                if item.get('id'):
                    db.execute('INSERT INTO qa_issue_reporters(product,subject,reporter_id,reporter_name,issue_updated_at) VALUES(?,?,?,?,?) ON CONFLICT(product,subject) '
                               "DO UPDATE SET reporter_id=CASE WHEN excluded.reporter_id!='' THEN excluded.reporter_id ELSE qa_issue_reporters.reporter_id END,reporter_name=CASE "
                               "WHEN excluded.reporter_name!='' OR excluded.reporter_id!=qa_issue_reporters.reporter_id "
                               "AND excluded.reporter_id!='' THEN excluded.reporter_name ELSE qa_issue_reporters.reporter_name END, "
                               "issue_updated_at=CASE WHEN julianday(excluded.issue_updated_at) IS NOT NULL AND "
                               "(julianday(qa_issue_reporters.issue_updated_at) IS NULL OR julianday(excluded.issue_updated_at)>=julianday(qa_issue_reporters.issue_updated_at)) "
                               "THEN excluded.issue_updated_at ELSE qa_issue_reporters.issue_updated_at END",
                               (product, item['id'], item.get('reporter_id') or '', item.get('reporter_name') or '', item.get('updated') or ''))

    def qa_get_reporters(self, product, subjects):
        """이슈 번호 → 저장된 작성자·업데이트 시각. 없는 번호는 넣지 않는다."""
        subjects = sorted({subject for subject in subjects if subject})
        if not subjects:
            return {}
        with self.connect() as db:
            rows = db.execute('SELECT subject,reporter_id,reporter_name,issue_updated_at FROM qa_issue_reporters '
                              f"WHERE product=? AND subject IN ({','.join('?' * len(subjects))})", (product, *subjects))
            return {row[0]: {'reporter_id': row[1], 'reporter_name': row[2], 'issue_updated_at': row[3]} for row in rows}

    def qa_set_processing(self, finding_id, product, state, actor, note):
        from app.core.daily_qa_storage import now_iso
        if state not in ('open', 'in_progress', 'done', 'hold') or not actor.strip() or len(actor) > 80 or len(note) > 2000:
            raise ValueError('처리 상태와 처리자 이름을 확인해 주세요. 메모는 2,000자까지입니다.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM daily_qa_findings WHERE id=? AND product=?', (finding_id, product)).fetchone():
                raise ValueError('해당 제품의 분석 결과가 없습니다.')
            values = (finding_id, state, actor.strip(), note.strip(), now_iso())
            db.execute('INSERT INTO qa_processing VALUES(?,?,?,?,?) ON CONFLICT(finding_id) DO UPDATE SET '
                       'state=excluded.state,actor=excluded.actor,note=excluded.note,updated_at=excluded.updated_at', values)
            db.execute('INSERT INTO qa_processing_events(finding_id,state,actor,note,created_at) VALUES(?,?,?,?,?)', values)

    def qa_processing_history(self, finding_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM qa_processing_events WHERE finding_id=? ORDER BY id DESC', (finding_id,))]

    def qa_search_findings(self, product, *, category='', subject='', reporter='', query='', state='', verdict='',
                           since='', until='', run_id='', page=1, finding_id=None, include_legacy=False,
                           verdicts=None, exclude_verdicts=False, sort='updated'):
        updated = "julianday(r.issue_updated_at) DESC, julianday(f.created_at) DESC, f.id DESC"
        orders = {'updated': updated, 'analyzed': 'f.id DESC',
                  'author': "CASE WHEN coalesce(r.reporter_id,'')='' THEN 1 ELSE 0 END, coalesce(nullif(r.reporter_name,''),r.reporter_id), r.reporter_id, " + updated,
                  'number': "CAST(substr(f.subject,instr(f.subject,'-')+1) AS INTEGER) DESC, f.subject, " + updated}
        if sort not in orders:
            raise ValueError('정렬 조건을 확인해 주세요.')
        clauses, params = ["f.product IN (?,'')" if include_legacy else 'f.product=?'], [product]
        spec = "f.analysis_type IN ('SPEC_COVERAGE','TC_CHECK','SRS_REMOVED')"
        if category in ('spec', 'issues'):
            clauses.append(spec if category == 'spec' else "f.analysis_type IN ('NEW_ISSUE','FIXED_ISSUE','SPEC_DECISION','COMMENT','ISSUE_AUDIT','TC_DRAFT')")
        for expression, value in [('f.subject LIKE ?', subject), ('r.reporter_id=?', reporter),
                                  ("coalesce(p.state,'open')=?", state), ('f.verdict=?', verdict),
                                  ('f.created_at>=?', since), ('f.created_at<?', until), ('f.id=?', finding_id)]:
            if value:
                clauses.append(expression)
                params.append('%' + value + '%' if expression.endswith('LIKE ?') else value)
        if query:
            clauses.append('(f.subject_title LIKE ? OR f.summary LIKE ? OR f.detail LIKE ?)')
            params.extend(['%' + query + '%'] * 3)
        if verdicts:
            clauses.append('f.verdict ' + ('NOT IN' if exclude_verdicts else 'IN') + '(' + ','.join('?' for _ in verdicts) + ')')
            params.extend(verdicts)
        if run_id:
            clauses.append('(f.run_id=? OR EXISTS (SELECT 1 FROM qa_result_links l WHERE l.finding_id=f.id AND l.run_id=?))')
            params.extend([run_id, run_id])
        source = ' FROM daily_qa_findings f LEFT JOIN qa_issue_reporters r ON r.product=f.product AND r.subject=f.subject '
        source += 'LEFT JOIN qa_processing p ON p.finding_id=f.id WHERE ' + ' AND '.join(clauses)
        with self.connect() as db:
            total = db.execute('SELECT count(*)' + source, params).fetchone()[0]
            rows = db.execute("SELECT f.*, coalesce(r.reporter_id,'') AS reporter_id, coalesce(r.reporter_name,'') AS reporter_name, "
                              "coalesce(r.issue_updated_at,'') AS issue_updated_at, coalesce(p.state,'open') AS processing_state" + source + ' ORDER BY ' + orders[sort] + ' LIMIT 50 OFFSET ?',
                              [*params, (max(1, page) - 1) * 50]).fetchall()
            reporters = [dict(row) for row in db.execute("SELECT DISTINCT reporter_id,reporter_name FROM qa_issue_reporters WHERE product=? AND reporter_id!='' ORDER BY reporter_name,reporter_id", (product,))]
        return {'items': [self._daily_qa_finding_dict(row) for row in rows], 'total': total, 'reporters': reporters}
