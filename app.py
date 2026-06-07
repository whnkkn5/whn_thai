from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'competition-2024')

DB_PATH = os.path.join(os.path.dirname(__file__), 'competition.db')

THAI_MONTHS = ['มกราคม','กุมภาพันธ์','มีนาคม','เมษายน','พฤษภาคม','มิถุนายน',
               'กรกฎาคม','สิงหาคม','กันยายน','ตุลาคม','พฤศจิกายน','ธันวาคม']

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        INSERT OR IGNORE INTO settings VALUES ('competition_name', 'การแข่งขันวันภาษาไทยแห่งชาติ');
        INSERT OR IGNORE INTO settings VALUES ('network_name', 'ศูนย์เครือข่าย');
        INSERT OR IGNORE INTO settings VALUES ('competition_date', '');
        INSERT OR IGNORE INTO settings VALUES ('signer1_name', '');
        INSERT OR IGNORE INTO settings VALUES ('signer1_position', '');
        INSERT OR IGNORE INTO settings VALUES ('signer2_name', '');
        INSERT OR IGNORE INTO settings VALUES ('signer2_position', '');

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            level TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            school_id INTEGER REFERENCES schools(id) ON DELETE CASCADE,
            class_level TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            school_id INTEGER REFERENCES schools(id) ON DELETE CASCADE,
            position TEXT DEFAULT 'ครู'
        );
        CREATE TABLE IF NOT EXISTS judges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            position TEXT DEFAULT 'ครู'
        );
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            score REAL,
            rank_pos INTEGER,
            award TEXT,
            UNIQUE(event_id, student_id)
        );
        CREATE TABLE IF NOT EXISTS coaches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE,
            UNIQUE(event_id, teacher_id)
        );
        CREATE TABLE IF NOT EXISTS event_judges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            judge_id INTEGER REFERENCES judges(id) ON DELETE CASCADE,
            UNIQUE(event_id, judge_id)
        );
    ''')
    db.commit()
    db.close()

def get_settings():
    db = get_db()
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    db.close()
    return {r['key']: r['value'] for r in rows}

def format_thai_date(date_str):
    if not date_str:
        return ''
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        return f'{d.day} {THAI_MONTHS[d.month-1]} พ.ศ. {d.year + 543}'
    except Exception:
        return date_str

def award_css(award):
    if not award:
        return 'participate'
    if 'ชนะเลิศ' in award:
        return 'gold-champion'
    if 'รองชนะเลิศอันดับ 1' in award:
        return 'gold-runner1'
    if 'รองชนะเลิศอันดับ 2' in award:
        return 'gold-runner2'
    if 'ทอง' in award:
        return 'gold'
    if 'เงิน' in award:
        return 'silver'
    if 'ทองแดง' in award:
        return 'bronze'
    return 'participate'

def award_icon(award):
    if not award:
        return '🎖'
    if 'ทอง' in award:
        return '🥇'
    if 'เงิน' in award:
        return '🥈'
    if 'ทองแดง' in award:
        return '🥉'
    return '🎖'

app.jinja_env.filters['award_css'] = award_css
app.jinja_env.filters['award_icon'] = award_icon
app.jinja_env.filters['thai_date'] = format_thai_date

def calculate_awards(event_id):
    db = get_db()
    rows = db.execute(
        'SELECT id, score FROM participants WHERE event_id=? AND score IS NOT NULL ORDER BY score DESC',
        [event_id]
    ).fetchall()
    for i, row in enumerate(rows):
        rank = i + 1
        score = row['score']
        if rank == 1 and score >= 80:
            award = 'เหรียญทอง ชนะเลิศ'
        elif rank == 2 and score >= 80:
            award = 'เหรียญทอง รองชนะเลิศอันดับ 1'
        elif rank == 3 and score >= 80:
            award = 'เหรียญทอง รองชนะเลิศอันดับ 2'
        elif score >= 80:
            award = 'เหรียญทอง'
        elif score >= 70:
            award = 'เหรียญเงิน'
        elif score >= 60:
            award = 'เหรียญทองแดง'
        else:
            award = 'เข้าร่วม'
        db.execute('UPDATE participants SET rank_pos=?, award=? WHERE id=?', [rank, award, row['id']])
    db.commit()
    db.close()

# ── Routes ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    db = get_db()
    events = db.execute('''
        SELECT e.*,
               COUNT(DISTINCT p.id) as cnt_total,
               COUNT(DISTINCT CASE WHEN p.score IS NOT NULL THEN p.id END) as cnt_scored
        FROM events e LEFT JOIN participants p ON p.event_id=e.id
        GROUP BY e.id ORDER BY e.level, e.name
    ''').fetchall()
    school_count = db.execute('SELECT COUNT(*) FROM schools').fetchone()[0]
    judge_count = db.execute('SELECT COUNT(*) FROM judges').fetchone()[0]
    settings = get_settings()
    db.close()
    return render_template('index.html', events=events, settings=settings,
                           school_count=school_count, judge_count=judge_count)

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        db = get_db()
        for key in ['competition_name','network_name','competition_date',
                    'signer1_name','signer1_position','signer2_name','signer2_position']:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       [key, request.form.get(key, '')])
        db.commit()
        db.close()
        flash('บันทึกการตั้งค่าเรียบร้อยแล้ว', 'success')
        return redirect(url_for('settings_page'))
    return render_template('settings.html', s=get_settings())

# ── Events ──────────────────────────────────────────────────────────────

@app.route('/events', methods=['GET', 'POST'])
def events():
    db = get_db()
    if request.method == 'POST':
        name = request.form['name'].strip()
        level = request.form['level'].strip()
        if name and level:
            db.execute('INSERT INTO events (name,level) VALUES (?,?)', [name, level])
            db.commit()
            flash(f'เพิ่มกิจกรรม "{name}" แล้ว', 'success')
        return redirect(url_for('events'))
    rows = db.execute('''
        SELECT e.*, COUNT(p.id) as cnt
        FROM events e LEFT JOIN participants p ON p.event_id=e.id
        GROUP BY e.id ORDER BY e.level, e.name
    ''').fetchall()
    db.close()
    return render_template('events.html', events=rows)

@app.route('/events/<int:eid>/delete', methods=['POST'])
def delete_event(eid):
    db = get_db()
    db.execute('DELETE FROM events WHERE id=?', [eid])
    db.commit()
    db.close()
    flash('ลบกิจกรรมแล้ว', 'info')
    return redirect(url_for('events'))

@app.route('/event/<int:eid>')
def event_detail(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=?', [eid]).fetchone()
    if not event:
        flash('ไม่พบกิจกรรม', 'danger')
        return redirect(url_for('events'))
    schools = db.execute('SELECT * FROM schools ORDER BY name').fetchall()
    participants = db.execute('''
        SELECT p.id, p.score, p.rank_pos, p.award,
               st.name as student_name, st.class_level,
               s.name as school_name, s.id as school_id
        FROM participants p
        JOIN students st ON st.id=p.student_id
        JOIN schools s ON s.id=st.school_id
        WHERE p.event_id=? ORDER BY COALESCE(p.rank_pos,999), st.name
    ''', [eid]).fetchall()
    coaches = db.execute('''
        SELECT c.id, t.name as teacher_name, t.position, s.name as school_name
        FROM coaches c
        JOIN teachers t ON t.id=c.teacher_id
        JOIN schools s ON s.id=t.school_id
        WHERE c.event_id=? ORDER BY t.name
    ''', [eid]).fetchall()
    event_judges = db.execute('''
        SELECT ej.id, j.name as judge_name, j.position
        FROM event_judges ej JOIN judges j ON j.id=ej.judge_id
        WHERE ej.event_id=? ORDER BY j.name
    ''', [eid]).fetchall()
    all_judges = db.execute('SELECT * FROM judges ORDER BY name').fetchall()
    db.close()
    return render_template('event_detail.html', event=event, schools=schools,
                           participants=participants, coaches=coaches,
                           event_judges=event_judges, all_judges=all_judges)

@app.route('/event/<int:eid>/participants/add', methods=['POST'])
def add_participant(eid):
    student_id = request.form.get('student_id')
    if student_id:
        db = get_db()
        try:
            db.execute('INSERT INTO participants (event_id,student_id) VALUES (?,?)',
                       [eid, student_id])
            db.commit()
        except Exception:
            flash('นักเรียนคนนี้ลงทะเบียนในกิจกรรมนี้แล้ว', 'warning')
        db.close()
    return redirect(url_for('event_detail', eid=eid))

@app.route('/participants/<int:pid>/delete', methods=['POST'])
def delete_participant(pid):
    db = get_db()
    row = db.execute('SELECT event_id FROM participants WHERE id=?', [pid]).fetchone()
    eid = row['event_id']
    db.execute('DELETE FROM participants WHERE id=?', [pid])
    db.commit()
    db.close()
    return redirect(url_for('event_detail', eid=eid))

@app.route('/event/<int:eid>/coaches/add', methods=['POST'])
def add_coach(eid):
    teacher_id = request.form.get('teacher_id')
    if teacher_id:
        db = get_db()
        try:
            db.execute('INSERT INTO coaches (event_id,teacher_id) VALUES (?,?)',
                       [eid, teacher_id])
            db.commit()
        except Exception:
            flash('ครูคนนี้ลงทะเบียนในกิจกรรมนี้แล้ว', 'warning')
        db.close()
    return redirect(url_for('event_detail', eid=eid))

@app.route('/coaches/<int:cid>/delete', methods=['POST'])
def delete_coach(cid):
    db = get_db()
    row = db.execute('SELECT event_id FROM coaches WHERE id=?', [cid]).fetchone()
    eid = row['event_id']
    db.execute('DELETE FROM coaches WHERE id=?', [cid])
    db.commit()
    db.close()
    return redirect(url_for('event_detail', eid=eid))

@app.route('/event/<int:eid>/judges/add', methods=['POST'])
def add_event_judge(eid):
    judge_id = request.form.get('judge_id')
    if judge_id:
        db = get_db()
        try:
            db.execute('INSERT INTO event_judges (event_id,judge_id) VALUES (?,?)',
                       [eid, judge_id])
            db.commit()
        except Exception:
            pass
        db.close()
    return redirect(url_for('event_detail', eid=eid))

@app.route('/event_judges/<int:ejid>/delete', methods=['POST'])
def delete_event_judge(ejid):
    db = get_db()
    row = db.execute('SELECT event_id FROM event_judges WHERE id=?', [ejid]).fetchone()
    eid = row['event_id']
    db.execute('DELETE FROM event_judges WHERE id=?', [ejid])
    db.commit()
    db.close()
    return redirect(url_for('event_detail', eid=eid))

# ── Scores ──────────────────────────────────────────────────────────────

@app.route('/event/<int:eid>/scores', methods=['GET', 'POST'])
def event_scores(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=?', [eid]).fetchone()
    if request.method == 'POST':
        rows = db.execute('SELECT id FROM participants WHERE event_id=?', [eid]).fetchall()
        for row in rows:
            val = request.form.get(f'score_{row["id"]}', '').strip()
            if val:
                try:
                    db.execute('UPDATE participants SET score=? WHERE id=?',
                               [float(val), row['id']])
                except ValueError:
                    pass
        db.commit()
        calculate_awards(eid)
        flash('บันทึกคะแนนและคำนวณรางวัลเรียบร้อยแล้ว', 'success')
        db.close()
        return redirect(url_for('event_results', eid=eid))
    participants = db.execute('''
        SELECT p.id, p.score, st.name as student_name, s.name as school_name
        FROM participants p
        JOIN students st ON st.id=p.student_id
        JOIN schools s ON s.id=st.school_id
        WHERE p.event_id=? ORDER BY st.name
    ''', [eid]).fetchall()
    db.close()
    return render_template('scores.html', event=event, participants=participants)

@app.route('/event/<int:eid>/results')
def event_results(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=?', [eid]).fetchone()
    participants = db.execute('''
        SELECT p.*, st.name as student_name, s.name as school_name, s.id as school_id
        FROM participants p
        JOIN students st ON st.id=p.student_id
        JOIN schools s ON s.id=st.school_id
        WHERE p.event_id=? ORDER BY COALESCE(p.rank_pos,999)
    ''', [eid]).fetchall()
    db.close()
    return render_template('results.html', event=event, participants=participants)

# ── Schools ──────────────────────────────────────────────────────────────

@app.route('/schools', methods=['GET', 'POST'])
def schools():
    db = get_db()
    if request.method == 'POST':
        name = request.form['name'].strip()
        if name:
            try:
                db.execute('INSERT INTO schools (name) VALUES (?)', [name])
                db.commit()
                flash(f'เพิ่มโรงเรียน "{name}" แล้ว', 'success')
            except Exception:
                flash('โรงเรียนนี้มีอยู่แล้ว', 'warning')
        return redirect(url_for('schools'))
    rows = db.execute('''
        SELECT s.*, COUNT(DISTINCT st.id) as cnt_st, COUNT(DISTINCT t.id) as cnt_t
        FROM schools s
        LEFT JOIN students st ON st.school_id=s.id
        LEFT JOIN teachers t ON t.school_id=s.id
        GROUP BY s.id ORDER BY s.name
    ''').fetchall()
    db.close()
    return render_template('schools.html', schools=rows)

@app.route('/schools/<int:sid>/delete', methods=['POST'])
def delete_school(sid):
    db = get_db()
    db.execute('DELETE FROM schools WHERE id=?', [sid])
    db.commit()
    db.close()
    flash('ลบโรงเรียนแล้ว', 'info')
    return redirect(url_for('schools'))

@app.route('/schools/<int:sid>')
def school_detail(sid):
    db = get_db()
    school = db.execute('SELECT * FROM schools WHERE id=?', [sid]).fetchone()
    students = db.execute('SELECT * FROM students WHERE school_id=? ORDER BY name', [sid]).fetchall()
    teachers = db.execute('SELECT * FROM teachers WHERE school_id=? ORDER BY name', [sid]).fetchall()
    db.close()
    return render_template('school_detail.html', school=school,
                           students=students, teachers=teachers)

@app.route('/schools/<int:sid>/students/add', methods=['POST'])
def add_student(sid):
    name = request.form['name'].strip()
    class_level = request.form.get('class_level', '').strip()
    if name:
        db = get_db()
        db.execute('INSERT INTO students (name,school_id,class_level) VALUES (?,?,?)',
                   [name, sid, class_level])
        db.commit()
        db.close()
    return redirect(url_for('school_detail', sid=sid))

@app.route('/students/<int:stid>/delete', methods=['POST'])
def delete_student(stid):
    db = get_db()
    row = db.execute('SELECT school_id FROM students WHERE id=?', [stid]).fetchone()
    sid = row['school_id']
    db.execute('DELETE FROM students WHERE id=?', [stid])
    db.commit()
    db.close()
    return redirect(url_for('school_detail', sid=sid))

@app.route('/schools/<int:sid>/teachers/add', methods=['POST'])
def add_teacher(sid):
    name = request.form['name'].strip()
    position = request.form.get('position', 'ครู').strip()
    if name:
        db = get_db()
        db.execute('INSERT INTO teachers (name,school_id,position) VALUES (?,?,?)',
                   [name, sid, position])
        db.commit()
        db.close()
    return redirect(url_for('school_detail', sid=sid))

@app.route('/teachers/<int:tid>/delete', methods=['POST'])
def delete_teacher(tid):
    db = get_db()
    row = db.execute('SELECT school_id FROM teachers WHERE id=?', [tid]).fetchone()
    sid = row['school_id']
    db.execute('DELETE FROM teachers WHERE id=?', [tid])
    db.commit()
    db.close()
    return redirect(url_for('school_detail', sid=sid))

# ── Judges ──────────────────────────────────────────────────────────────

@app.route('/judges', methods=['GET', 'POST'])
def judges():
    db = get_db()
    if request.method == 'POST':
        name = request.form['name'].strip()
        position = request.form.get('position', 'ครู').strip()
        if name:
            db.execute('INSERT INTO judges (name,position) VALUES (?,?)', [name, position])
            db.commit()
            flash(f'เพิ่มกรรมการ "{name}" แล้ว', 'success')
        return redirect(url_for('judges'))
    rows = db.execute('SELECT * FROM judges ORDER BY name').fetchall()
    db.close()
    return render_template('judges.html', judges=rows)

@app.route('/judges/<int:jid>/delete', methods=['POST'])
def delete_judge(jid):
    db = get_db()
    db.execute('DELETE FROM judges WHERE id=?', [jid])
    db.commit()
    db.close()
    return redirect(url_for('judges'))

# ── Certificates ──────────────────────────────────────────────────────────

@app.route('/certificates')
def certificates():
    db = get_db()
    settings = get_settings()
    school_id = request.args.get('school_id', type=int)
    event_id  = request.args.get('event_id',  type=int)
    cert_type = request.args.get('type', 'all')

    schools_list = db.execute('SELECT * FROM schools ORDER BY name').fetchall()
    events_list  = db.execute('SELECT * FROM events ORDER BY level,name').fetchall()

    # Student certificates
    sq = '''
        SELECT p.award, p.rank_pos, p.score,
               st.name as student_name, st.class_level,
               s.name as school_name, s.id as school_id,
               e.name as event_name, e.level as event_level, e.id as event_id
        FROM participants p
        JOIN students st ON st.id=p.student_id
        JOIN schools  s  ON s.id=st.school_id
        JOIN events   e  ON e.id=p.event_id
        WHERE p.award IS NOT NULL AND p.award != 'เข้าร่วม'
    '''
    sp = []
    if school_id: sq += ' AND s.id=?';  sp.append(school_id)
    if event_id:  sq += ' AND e.id=?';  sp.append(event_id)
    sq += ' ORDER BY e.level,e.name,p.rank_pos'
    student_certs = db.execute(sq, sp).fetchall()

    # Coach certificates — group by coach+event
    cq = '''
        SELECT c.id as coach_id,
               t.name as teacher_name, t.position,
               s.name as school_name, s.id as school_id,
               e.name as event_name, e.level as event_level, e.id as event_id,
               (SELECT p2.award FROM participants p2
                JOIN students st2 ON st2.id=p2.student_id
                WHERE p2.event_id=e.id AND st2.school_id=s.id
                AND p2.award IS NOT NULL
                ORDER BY p2.rank_pos LIMIT 1) as best_award
        FROM coaches c
        JOIN teachers t ON t.id=c.teacher_id
        JOIN schools  s ON s.id=t.school_id
        JOIN events   e ON e.id=c.event_id
        WHERE 1=1
    '''
    cp = []
    if school_id: cq += ' AND s.id=?'; cp.append(school_id)
    if event_id:  cq += ' AND e.id=?'; cp.append(event_id)
    cq += ' ORDER BY e.level,e.name,t.name'
    coach_certs = db.execute(cq, cp).fetchall()

    # Judge certificates
    jq = '''
        SELECT ej.id, j.name as judge_name, j.position,
               e.name as event_name, e.level as event_level, e.id as event_id
        FROM event_judges ej
        JOIN judges j ON j.id=ej.judge_id
        JOIN events e ON e.id=ej.event_id
        WHERE 1=1
    '''
    jp = []
    if event_id: jq += ' AND e.id=?'; jp.append(event_id)
    jq += ' ORDER BY e.level,e.name,j.name'
    judge_certs = db.execute(jq, jp).fetchall()

    db.close()
    return render_template('certificates.html',
        settings=settings,
        schools=schools_list, events=events_list,
        student_certs=student_certs,
        coach_certs=coach_certs,
        judge_certs=judge_certs,
        selected_school=school_id,
        selected_event=event_id,
        cert_type=cert_type,
        thai_date=format_thai_date(settings.get('competition_date','')))

# ── API ──────────────────────────────────────────────────────────────────

@app.get('/api/schools/<int:sid>/students')
def api_students(sid):
    db = get_db()
    rows = db.execute(
        'SELECT id,name,class_level FROM students WHERE school_id=? ORDER BY name', [sid]
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.get('/api/schools/<int:sid>/teachers')
def api_teachers(sid):
    db = get_db()
    rows = db.execute(
        'SELECT id,name,position FROM teachers WHERE school_id=? ORDER BY name', [sid]
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

init_db()

if __name__ == '__main__':
    print('✅  เปิดเบราว์เซอร์ที่  http://localhost:5000')
    app.run(debug=True, port=5000)
