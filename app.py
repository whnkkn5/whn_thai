from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import os
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import json
import base64
from flask import send_file
import io

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'competition-2024')

THAI_MONTHS = ['มกราคม','กุมภาพันธ์','มีนาคม','เมษายน','พฤษภาคม','มิถุนายน',
               'กรกฎาคม','สิงหาคม','กันยายน','ตุลาคม','พฤศจิกายน','ธันวาคม']

# ── DB wrapper (makes psycopg2 behave like sqlite3) ───────

class _Row(dict):
    """Dict with attribute access and integer-index fallback."""
    def __getattr__(self, name):
        try: return self[name]
        except KeyError: raise AttributeError(name)
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class _Cur:
    def __init__(self, cur): self._c = cur
    def fetchone(self):
        r = self._c.fetchone()
        return _Row(r) if r else None
    def fetchall(self):
        return [_Row(r) for r in (self._c.fetchall() or [])]
    def __iter__(self):
        return iter(self.fetchall())

class _Db:
    def __init__(self, conn): self._conn = conn
    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        return _Cur(cur)
    def commit(self):   self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self):    self._conn.close()

def get_db():
    url = os.environ.get('DATABASE_URL', '')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    conn = psycopg2.connect(url)
    return _Db(conn)

def init_db():
    db = get_db()
    for stmt in [
        '''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )''',
        '''CREATE TABLE IF NOT EXISTS schools (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        )''',
        '''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'school',
            school_id INTEGER REFERENCES schools(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        '''CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            level TEXT NOT NULL
        )''',
        '''CREATE TABLE IF NOT EXISTS students (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            school_id INTEGER REFERENCES schools(id) ON DELETE CASCADE,
            class_level TEXT DEFAULT ''
        )''',
        '''CREATE TABLE IF NOT EXISTS teachers (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            school_id INTEGER REFERENCES schools(id) ON DELETE CASCADE,
            position TEXT DEFAULT 'ครู'
        )''',
        '''CREATE TABLE IF NOT EXISTS judges (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            position TEXT DEFAULT 'ครู'
        )''',
        '''CREATE TABLE IF NOT EXISTS participants (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            score REAL,
            rank_pos INTEGER,
            award TEXT,
            UNIQUE(event_id, student_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS coaches (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE,
            UNIQUE(event_id, teacher_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS event_judges (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            judge_id INTEGER REFERENCES judges(id) ON DELETE CASCADE,
            UNIQUE(event_id, judge_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS cert_templates (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            cert_type TEXT NOT NULL,
            image_data TEXT NOT NULL,
            image_mime TEXT DEFAULT 'image/png',
            config TEXT DEFAULT '[]',
            is_active INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
    ]:
        db.execute(stmt)
    db.commit()

    for key, val in [
        ('competition_name', 'การแข่งขันวันภาษาไทยแห่งชาติ'),
        ('network_name',     'ศูนย์เครือข่าย'),
        ('competition_date', ''),
        ('signer1_name',     ''),
        ('signer1_position', ''),
        ('signer2_name',     ''),
        ('signer2_position', ''),
    ]:
        db.execute(
            'INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING',
            [key, val]
        )
    db.commit()

    # Migrations
    for alter in [
        "ALTER TABLE events ADD COLUMN code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN max_students INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE events ADD COLUMN max_coaches INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE events ALTER COLUMN level SET DEFAULT ''",
        "ALTER TABLE events ALTER COLUMN level DROP NOT NULL",
    ]:
        try:
            db.execute(alter)
            db.commit()
        except Exception:
            db.rollback()

    admin = db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            ['admin', generate_password_hash('admin1234', method='pbkdf2:sha256'), 'admin']
        )
        print('👤 สร้าง admin เริ่มต้น: username=admin  password=admin1234')

    db.commit()
    db.close()

# ── Helpers ──────────────────────────────────────────────

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
    if not award: return 'participate'
    if 'ชนะเลิศ' in award: return 'gold-champion'
    if 'รองชนะเลิศอันดับ 1' in award: return 'gold-runner1'
    if 'รองชนะเลิศอันดับ 2' in award: return 'gold-runner2'
    if 'ทอง' in award: return 'gold'
    if 'เงิน' in award: return 'silver'
    if 'ทองแดง' in award: return 'bronze'
    return 'participate'

def award_icon(award):
    if not award: return '🎖'
    if 'ทอง' in award: return '🥇'
    if 'เงิน' in award: return '🥈'
    if 'ทองแดง' in award: return '🥉'
    return '🎖'

app.jinja_env.filters['award_css'] = award_css
app.jinja_env.filters['award_icon'] = award_icon
app.jinja_env.filters['thai_date'] = format_thai_date

def calculate_awards(event_id):
    db = get_db()
    rows = db.execute(
        'SELECT id, score FROM participants WHERE event_id=%s AND score IS NOT NULL ORDER BY score DESC',
        [event_id]
    ).fetchall()
    for i, row in enumerate(rows):
        rank  = i + 1
        score = row['score']
        if rank == 1 and score >= 80:   award = 'เหรียญทอง ชนะเลิศ'
        elif rank == 2 and score >= 80: award = 'เหรียญทอง รองชนะเลิศอันดับ 1'
        elif rank == 3 and score >= 80: award = 'เหรียญทอง รองชนะเลิศอันดับ 2'
        elif score >= 80: award = 'เหรียญทอง'
        elif score >= 70: award = 'เหรียญเงิน'
        elif score >= 60: award = 'เหรียญทองแดง'
        else:             award = 'เข้าร่วม'
        db.execute('UPDATE participants SET rank_pos=%s, award=%s WHERE id=%s', [rank, award, row['id']])
    db.commit()
    db.close()

# ── Auth decorators ───────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('กรุณาเข้าสู่ระบบ', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('กรุณาเข้าสู่ระบบ', 'warning')
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('ไม่มีสิทธิ์เข้าถึงหน้านี้', 'danger')
            return redirect(url_for('school_home'))
        return f(*args, **kwargs)
    return decorated

# ── Auth routes ───────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=%s', [username]).fetchone()
        db.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id']   = user['id']
            session['username']  = user['username']
            session['role']      = user['role']
            session['school_id'] = user['school_id']
            flash(f'ยินดีต้อนรับ {user["username"]}', 'success')
            return redirect(url_for('index'))
        flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('ออกจากระบบแล้ว', 'info')
    return redirect(url_for('login'))

# ── Dashboard ─────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    if session.get('role') == 'school':
        return redirect(url_for('school_home'))
    db = get_db()
    events = db.execute('''
        SELECT e.*,
               COUNT(DISTINCT p.id) as cnt_total,
               COUNT(DISTINCT CASE WHEN p.score IS NOT NULL THEN p.id END) as cnt_scored
        FROM events e LEFT JOIN participants p ON p.event_id=e.id
        GROUP BY e.id ORDER BY e.code, e.name
    ''').fetchall()
    school_count = db.execute('SELECT COUNT(*) FROM schools').fetchone()[0]
    judge_count  = db.execute('SELECT COUNT(*) FROM judges').fetchone()[0]
    user_count   = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    settings = get_settings()
    db.close()
    return render_template('index.html', events=events, settings=settings,
                           school_count=school_count, judge_count=judge_count,
                           user_count=user_count)

# ── School home (role=school) ─────────────────────────────

@app.route('/my-school')
@login_required
def school_home():
    if session.get('role') == 'admin':
        return redirect(url_for('index'))
    sid = session.get('school_id')
    db = get_db()
    school   = db.execute('SELECT * FROM schools WHERE id=%s', [sid]).fetchone() if sid else None
    students = db.execute('SELECT * FROM students WHERE school_id=%s ORDER BY name', [sid]).fetchall() if sid else []
    teachers = db.execute('SELECT * FROM teachers WHERE school_id=%s ORDER BY name', [sid]).fetchall() if sid else []
    events   = db.execute('SELECT * FROM events ORDER BY code, name').fetchall() if sid else []

    parts_by_event   = {}
    coaches_by_event = {}
    if sid:
        for p in db.execute('''
            SELECT p.id, p.score, p.rank_pos, p.award, p.event_id,
                   st.name as student_name, st.id as student_id, st.class_level
            FROM participants p JOIN students st ON st.id=p.student_id
            WHERE st.school_id=%s
            ORDER BY p.event_id, COALESCE(p.rank_pos,999), st.name
        ''', [sid]).fetchall():
            parts_by_event.setdefault(p['event_id'], []).append(p)

        for c in db.execute('''
            SELECT c.id, c.event_id, t.name as teacher_name, t.id as teacher_id, t.position
            FROM coaches c JOIN teachers t ON t.id=c.teacher_id
            WHERE t.school_id=%s
            ORDER BY c.event_id, t.name
        ''', [sid]).fetchall():
            coaches_by_event.setdefault(c['event_id'], []).append(c)

    events_joined       = len(parts_by_event)
    total_registrations = sum(len(v) for v in parts_by_event.values())
    db.close()
    return render_template('school_home.html', school=school,
                           students=students, teachers=teachers,
                           events=events,
                           parts_by_event=parts_by_event,
                           coaches_by_event=coaches_by_event,
                           events_joined=events_joined,
                           total_registrations=total_registrations)

# ── Admin: User Management ────────────────────────────────

@app.route('/admin/users')
@admin_required
def admin_users():
    db = get_db()
    users   = db.execute('''
        SELECT u.*, s.name as school_name
        FROM users u LEFT JOIN schools s ON s.id=u.school_id
        ORDER BY u.role DESC, u.username
    ''').fetchall()
    schools = db.execute('SELECT * FROM schools ORDER BY name').fetchall()
    db.close()
    return render_template('admin_users.html', users=users, schools=schools)

@app.route('/admin/users/add', methods=['POST'])
@admin_required
def admin_add_user():
    username  = request.form['username'].strip()
    password  = request.form['password'].strip()
    role      = request.form['role']
    school_id = request.form.get('school_id') or None
    if not username or not password:
        flash('กรุณากรอกชื่อผู้ใช้และรหัสผ่าน', 'warning')
        return redirect(url_for('admin_users'))
    if role == 'school' and not school_id:
        flash('ผู้ใช้งานโรงเรียนต้องเลือกโรงเรียน', 'warning')
        return redirect(url_for('admin_users'))
    db = get_db()
    try:
        db.execute('INSERT INTO users (username, password_hash, role, school_id) VALUES (%s,%s,%s,%s)',
                   [username, generate_password_hash(password, method='pbkdf2:sha256'), role, school_id])
        db.commit()
        flash(f'เพิ่มผู้ใช้ "{username}" แล้ว', 'success')
    except Exception:
        db.rollback()
        flash('ชื่อผู้ใช้นี้มีอยู่แล้ว', 'warning')
    db.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@admin_required
def admin_delete_user(uid):
    if uid == session['user_id']:
        flash('ไม่สามารถลบบัญชีตัวเองได้', 'warning')
        return redirect(url_for('admin_users'))
    db = get_db()
    db.execute('DELETE FROM users WHERE id=%s', [uid])
    db.commit()
    db.close()
    flash('ลบผู้ใช้แล้ว', 'info')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/reset-password', methods=['POST'])
@admin_required
def admin_reset_password(uid):
    new_pass = request.form['new_password'].strip()
    if not new_pass:
        flash('กรุณากรอกรหัสผ่านใหม่', 'warning')
        return redirect(url_for('admin_users'))
    db = get_db()
    db.execute('UPDATE users SET password_hash=%s WHERE id=%s',
               [generate_password_hash(new_pass, method='pbkdf2:sha256'), uid])
    db.commit()
    db.close()
    flash('เปลี่ยนรหัสผ่านแล้ว', 'success')
    return redirect(url_for('admin_users'))

# ── Settings ──────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings_page():
    if request.method == 'POST':
        db = get_db()
        for key in ['competition_name','network_name','competition_date',
                    'signer1_name','signer1_position','signer2_name','signer2_position']:
            db.execute(
                'INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value',
                [key, request.form.get(key, '')]
            )
        db.commit()
        db.close()
        flash('บันทึกการตั้งค่าเรียบร้อยแล้ว', 'success')
        return redirect(url_for('settings_page'))
    return render_template('settings.html', s=get_settings())

# ── Events ────────────────────────────────────────────────

@app.route('/events', methods=['GET', 'POST'])
@admin_required
def events():
    db = get_db()
    if request.method == 'POST':
        code         = request.form.get('code', '').strip()
        name         = request.form['name'].strip()
        max_students = int(request.form.get('max_students') or 1)
        max_coaches  = int(request.form.get('max_coaches') or 1)
        if name:
            db.execute(
                'INSERT INTO events (code,name,level,max_students,max_coaches) VALUES (%s,%s,%s,%s,%s)',
                [code, name, '', max_students, max_coaches]
            )
            db.commit()
            flash(f'เพิ่มกิจกรรม "{name}" แล้ว', 'success')
        return redirect(url_for('events'))
    rows = db.execute('''
        SELECT e.*, COUNT(p.id) as cnt
        FROM events e LEFT JOIN participants p ON p.event_id=e.id
        GROUP BY e.id ORDER BY e.code, e.name
    ''').fetchall()
    db.close()
    return render_template('events.html', events=rows)

@app.route('/events/<int:eid>/delete', methods=['POST'])
@admin_required
def delete_event(eid):
    db = get_db()
    db.execute('DELETE FROM events WHERE id=%s', [eid])
    db.commit()
    db.close()
    flash('ลบกิจกรรมแล้ว', 'info')
    return redirect(url_for('events'))

@app.route('/event/<int:eid>')
@admin_required
def event_detail(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=%s', [eid]).fetchone()
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
        JOIN schools  s  ON s.id=st.school_id
        WHERE p.event_id=%s ORDER BY COALESCE(p.rank_pos,999), st.name
    ''', [eid]).fetchall()
    coaches = db.execute('''
        SELECT c.id, t.name as teacher_name, t.position, s.name as school_name
        FROM coaches c
        JOIN teachers t ON t.id=c.teacher_id
        JOIN schools  s ON s.id=t.school_id
        WHERE c.event_id=%s ORDER BY t.name
    ''', [eid]).fetchall()
    event_judges = db.execute('''
        SELECT ej.id, j.name as judge_name, j.position
        FROM event_judges ej JOIN judges j ON j.id=ej.judge_id
        WHERE ej.event_id=%s ORDER BY j.name
    ''', [eid]).fetchall()
    all_judges = db.execute('SELECT * FROM judges ORDER BY name').fetchall()
    db.close()
    return render_template('event_detail.html', event=event, schools=schools,
                           participants=participants, coaches=coaches,
                           event_judges=event_judges, all_judges=all_judges)

@app.route('/event/<int:eid>/participants/add', methods=['POST'])
@login_required
def add_participant(eid):
    student_id = request.form.get('student_id')
    is_school  = session.get('role') == 'school'
    back       = url_for('school_home') if is_school else url_for('event_detail', eid=eid)
    if student_id:
        db = get_db()
        st = db.execute('SELECT school_id FROM students WHERE id=%s', [student_id]).fetchone()
        if not st:
            db.close()
            return redirect(back)
        if is_school and st['school_id'] != session.get('school_id'):
            flash('ไม่มีสิทธิ์ลงทะเบียนนักเรียนของโรงเรียนอื่น', 'danger')
            db.close()
            return redirect(back)
        # Enforce per-school student limit
        ev = db.execute('SELECT max_students FROM events WHERE id=%s', [eid]).fetchone()
        cur_count = db.execute('''
            SELECT COUNT(*) FROM participants p
            JOIN students s ON s.id=p.student_id
            WHERE p.event_id=%s AND s.school_id=%s
        ''', [eid, st['school_id']]).fetchone()[0]
        if cur_count >= (ev['max_students'] if ev else 1):
            flash(f'กิจกรรมนี้รับนักเรียนได้สูงสุด {ev["max_students"]} คนต่อโรงเรียน', 'warning')
            db.close()
            return redirect(back)
        try:
            db.execute('INSERT INTO participants (event_id,student_id) VALUES (%s,%s)', [eid, student_id])
            db.commit()
        except Exception:
            db.rollback()
            flash('นักเรียนคนนี้ลงทะเบียนในกิจกรรมนี้แล้ว', 'warning')
        db.close()
    return redirect(back)

@app.route('/participants/<int:pid>/delete', methods=['POST'])
@login_required
def delete_participant(pid):
    db  = get_db()
    row = db.execute('''SELECT p.event_id, st.school_id
                        FROM participants p JOIN students st ON st.id=p.student_id
                        WHERE p.id=%s''', [pid]).fetchone()
    eid       = row['event_id']
    is_school = session.get('role') == 'school'
    if is_school and row['school_id'] != session.get('school_id'):
        flash('ไม่มีสิทธิ์', 'danger')
        db.close()
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM participants WHERE id=%s', [pid])
    db.commit()
    db.close()
    return redirect(url_for('school_home') if is_school else url_for('event_detail', eid=eid))

@app.route('/event/<int:eid>/coaches/add', methods=['POST'])
@login_required
def add_coach(eid):
    teacher_id = request.form.get('teacher_id')
    is_school  = session.get('role') == 'school'
    back       = url_for('school_home') if is_school else url_for('event_detail', eid=eid)
    if teacher_id:
        db = get_db()
        t = db.execute('SELECT school_id FROM teachers WHERE id=%s', [teacher_id]).fetchone()
        if not t:
            db.close()
            return redirect(back)
        if is_school and t['school_id'] != session.get('school_id'):
            flash('ไม่มีสิทธิ์', 'danger')
            db.close()
            return redirect(back)
        # Enforce per-school coach limit
        ev = db.execute('SELECT max_coaches FROM events WHERE id=%s', [eid]).fetchone()
        cur_count = db.execute('''
            SELECT COUNT(*) FROM coaches c
            JOIN teachers tc ON tc.id=c.teacher_id
            WHERE c.event_id=%s AND tc.school_id=%s
        ''', [eid, t['school_id']]).fetchone()[0]
        if cur_count >= (ev['max_coaches'] if ev else 1):
            flash(f'กิจกรรมนี้รับครูผู้ฝึกสอนได้สูงสุด {ev["max_coaches"]} คนต่อโรงเรียน', 'warning')
            db.close()
            return redirect(back)
        try:
            db.execute('INSERT INTO coaches (event_id,teacher_id) VALUES (%s,%s)', [eid, teacher_id])
            db.commit()
        except Exception:
            db.rollback()
            flash('ครูคนนี้ลงทะเบียนในกิจกรรมนี้แล้ว', 'warning')
        db.close()
    return redirect(back)

@app.route('/coaches/<int:cid>/delete', methods=['POST'])
@login_required
def delete_coach(cid):
    db  = get_db()
    row = db.execute('''SELECT c.event_id, t.school_id
                        FROM coaches c JOIN teachers t ON t.id=c.teacher_id
                        WHERE c.id=%s''', [cid]).fetchone()
    eid       = row['event_id']
    is_school = session.get('role') == 'school'
    if is_school and row['school_id'] != session.get('school_id'):
        flash('ไม่มีสิทธิ์', 'danger')
        db.close()
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM coaches WHERE id=%s', [cid])
    db.commit()
    db.close()
    return redirect(url_for('school_home') if is_school else url_for('event_detail', eid=eid))

@app.route('/event/<int:eid>/judges/add', methods=['POST'])
@admin_required
def add_event_judge(eid):
    judge_id = request.form.get('judge_id')
    if judge_id:
        db = get_db()
        try:
            db.execute('INSERT INTO event_judges (event_id,judge_id) VALUES (%s,%s)', [eid, judge_id])
            db.commit()
        except Exception:
            db.rollback()
        db.close()
    return redirect(url_for('event_detail', eid=eid))

@app.route('/event_judges/<int:ejid>/delete', methods=['POST'])
@admin_required
def delete_event_judge(ejid):
    db = get_db()
    row = db.execute('SELECT event_id FROM event_judges WHERE id=%s', [ejid]).fetchone()
    eid = row['event_id']
    db.execute('DELETE FROM event_judges WHERE id=%s', [ejid])
    db.commit()
    db.close()
    return redirect(url_for('event_detail', eid=eid))

# ── Scores ────────────────────────────────────────────────

@app.route('/event/<int:eid>/scores', methods=['GET', 'POST'])
@admin_required
def event_scores(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=%s', [eid]).fetchone()
    if request.method == 'POST':
        rows = db.execute('SELECT id FROM participants WHERE event_id=%s', [eid]).fetchall()
        for row in rows:
            val = request.form.get(f'score_{row["id"]}', '').strip()
            if val:
                try:
                    db.execute('UPDATE participants SET score=%s WHERE id=%s', [float(val), row['id']])
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
        JOIN schools  s  ON s.id=st.school_id
        WHERE p.event_id=%s ORDER BY st.name
    ''', [eid]).fetchall()
    db.close()
    return render_template('scores.html', event=event, participants=participants)

@app.route('/event/<int:eid>/results')
@admin_required
def event_results(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=%s', [eid]).fetchone()
    participants = db.execute('''
        SELECT p.*, st.name as student_name, s.name as school_name, s.id as school_id
        FROM participants p
        JOIN students st ON st.id=p.student_id
        JOIN schools  s  ON s.id=st.school_id
        WHERE p.event_id=%s ORDER BY COALESCE(p.rank_pos,999)
    ''', [eid]).fetchall()
    db.close()
    return render_template('results.html', event=event, participants=participants)

# ── Schools ───────────────────────────────────────────────

@app.route('/schools', methods=['GET', 'POST'])
@admin_required
def schools():
    db = get_db()
    if request.method == 'POST':
        name = request.form['name'].strip()
        if name:
            try:
                db.execute('INSERT INTO schools (name) VALUES (%s)', [name])
                db.commit()
                flash(f'เพิ่มโรงเรียน "{name}" แล้ว', 'success')
            except Exception:
                db.rollback()
                flash('โรงเรียนนี้มีอยู่แล้ว', 'warning')
        return redirect(url_for('schools'))
    rows = db.execute('''
        SELECT s.*, COUNT(DISTINCT st.id) as cnt_st, COUNT(DISTINCT t.id) as cnt_t
        FROM schools s
        LEFT JOIN students st ON st.school_id=s.id
        LEFT JOIN teachers t  ON t.school_id=s.id
        GROUP BY s.id ORDER BY s.name
    ''').fetchall()
    db.close()
    return render_template('schools.html', schools=rows)

@app.route('/schools/<int:sid>/delete', methods=['POST'])
@admin_required
def delete_school(sid):
    db = get_db()
    db.execute('DELETE FROM schools WHERE id=%s', [sid])
    db.commit()
    db.close()
    flash('ลบโรงเรียนแล้ว', 'info')
    return redirect(url_for('schools'))

@app.route('/schools/<int:sid>')
@login_required
def school_detail(sid):
    if session.get('role') == 'school' and session.get('school_id') != sid:
        return redirect(url_for('school_home'))
    db = get_db()
    school   = db.execute('SELECT * FROM schools WHERE id=%s', [sid]).fetchone()
    students = db.execute('SELECT * FROM students WHERE school_id=%s ORDER BY name', [sid]).fetchall()
    teachers = db.execute('SELECT * FROM teachers WHERE school_id=%s ORDER BY name', [sid]).fetchall()
    db.close()
    return render_template('school_detail.html', school=school,
                           students=students, teachers=teachers)

@app.route('/schools/<int:sid>/students/add', methods=['POST'])
@login_required
def add_student(sid):
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        return redirect(url_for('school_home'))
    name = request.form['name'].strip()
    if name:
        db = get_db()
        db.execute('INSERT INTO students (name,school_id) VALUES (%s,%s)',
                   [name, sid])
        db.commit()
        db.close()
    back = url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid)
    return redirect(back)

@app.route('/students/<int:stid>/edit', methods=['POST'])
@login_required
def edit_student(stid):
    db = get_db()
    row = db.execute('SELECT school_id FROM students WHERE id=%s', [stid]).fetchone()
    if not row:
        db.close()
        return redirect(url_for('school_home'))
    sid = row['school_id']
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        db.close()
        return redirect(url_for('school_home'))
    name = request.form['name'].strip()
    if name:
        db.execute('UPDATE students SET name=%s WHERE id=%s', [name, stid])
        db.commit()
    db.close()
    return redirect(url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid))

@app.route('/students/<int:stid>/delete', methods=['POST'])
@login_required
def delete_student(stid):
    db = get_db()
    row = db.execute('SELECT school_id FROM students WHERE id=%s', [stid]).fetchone()
    sid = row['school_id']
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM students WHERE id=%s', [stid])
    db.commit()
    db.close()
    back = url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid)
    return redirect(back)

@app.route('/schools/<int:sid>/teachers/add', methods=['POST'])
@login_required
def add_teacher(sid):
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        return redirect(url_for('school_home'))
    name     = request.form['name'].strip()
    position = request.form.get('position', 'ครู').strip()
    if name:
        db = get_db()
        db.execute('INSERT INTO teachers (name,school_id,position) VALUES (%s,%s,%s)',
                   [name, sid, position])
        db.commit()
        db.close()
    back = url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid)
    return redirect(back)

@app.route('/teachers/<int:tid>/edit', methods=['POST'])
@login_required
def edit_teacher(tid):
    db = get_db()
    row = db.execute('SELECT school_id FROM teachers WHERE id=%s', [tid]).fetchone()
    if not row:
        db.close()
        return redirect(url_for('school_home'))
    sid = row['school_id']
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        db.close()
        return redirect(url_for('school_home'))
    name     = request.form['name'].strip()
    position = request.form.get('position', 'ครู').strip()
    if name:
        db.execute('UPDATE teachers SET name=%s, position=%s WHERE id=%s', [name, position, tid])
        db.commit()
    db.close()
    return redirect(url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid))

@app.route('/teachers/<int:tid>/delete', methods=['POST'])
@login_required
def delete_teacher(tid):
    db = get_db()
    row = db.execute('SELECT school_id FROM teachers WHERE id=%s', [tid]).fetchone()
    sid = row['school_id']
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM teachers WHERE id=%s', [tid])
    db.commit()
    db.close()
    back = url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid)
    return redirect(back)

# ── Judges ────────────────────────────────────────────────

@app.route('/judges', methods=['GET', 'POST'])
@admin_required
def judges():
    db = get_db()
    if request.method == 'POST':
        name     = request.form['name'].strip()
        position = request.form.get('position', 'ครู').strip()
        if name:
            db.execute('INSERT INTO judges (name,position) VALUES (%s,%s)', [name, position])
            db.commit()
            flash(f'เพิ่มกรรมการ "{name}" แล้ว', 'success')
        return redirect(url_for('judges'))
    rows = db.execute('SELECT * FROM judges ORDER BY name').fetchall()
    db.close()
    return render_template('judges.html', judges=rows)

@app.route('/judges/<int:jid>/delete', methods=['POST'])
@admin_required
def delete_judge(jid):
    db = get_db()
    db.execute('DELETE FROM judges WHERE id=%s', [jid])
    db.commit()
    db.close()
    return redirect(url_for('judges'))

# ── Certificates ──────────────────────────────────────────

@app.route('/certificates')
@login_required
def certificates():
    db = get_db()
    settings  = get_settings()
    is_school = session.get('role') == 'school'
    school_id = session.get('school_id') if is_school else request.args.get('school_id', type=int)
    event_id  = request.args.get('event_id', type=int)
    cert_type = request.args.get('type', 'all')

    schools_list = db.execute('SELECT * FROM schools ORDER BY name').fetchall()
    events_list  = db.execute('SELECT * FROM events ORDER BY code, name').fetchall()

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
    if school_id: sq += ' AND s.id=%s'; sp.append(school_id)
    if event_id:  sq += ' AND e.id=%s'; sp.append(event_id)
    sq += ' ORDER BY e.code,e.name,p.rank_pos'
    student_certs = db.execute(sq, sp).fetchall()

    cq = '''
        SELECT c.id as coach_id,
               t.name as teacher_name, t.position,
               s.name as school_name, s.id as school_id,
               e.name as event_name, e.level as event_level, e.id as event_id,
               (SELECT p2.award FROM participants p2
                JOIN students st2 ON st2.id=p2.student_id
                WHERE p2.event_id=e.id AND st2.school_id=s.id
                AND p2.award IS NOT NULL ORDER BY p2.rank_pos LIMIT 1) as best_award
        FROM coaches c
        JOIN teachers t ON t.id=c.teacher_id
        JOIN schools  s ON s.id=t.school_id
        JOIN events   e ON e.id=c.event_id
        WHERE 1=1
    '''
    cp = []
    if school_id: cq += ' AND s.id=%s'; cp.append(school_id)
    if event_id:  cq += ' AND e.id=%s'; cp.append(event_id)
    cq += ' ORDER BY e.code,e.name,t.name'
    coach_certs = db.execute(cq, cp).fetchall()

    active_templates = get_active_templates()
    db.close()
    return render_template('certificates.html',
        settings=settings,
        schools=schools_list, events=events_list,
        student_certs=student_certs,
        coach_certs=coach_certs,
        selected_school=school_id,
        selected_event=event_id,
        cert_type=cert_type,
        active_templates=active_templates,
        is_school=is_school,
        thai_date=format_thai_date(settings.get('competition_date','')))

@app.route('/certificates/judges')
@admin_required
def judge_certificates():
    db = get_db()
    settings    = get_settings()
    event_id    = request.args.get('event_id', type=int)
    events_list = db.execute('SELECT * FROM events ORDER BY code, name').fetchall()
    jq = '''
        SELECT ej.id, j.name as judge_name, j.position,
               e.name as event_name, e.level as event_level, e.id as event_id
        FROM event_judges ej
        JOIN judges j ON j.id=ej.judge_id
        JOIN events e ON e.id=ej.event_id
        WHERE 1=1
    '''
    jp = []
    if event_id: jq += ' AND e.id=%s'; jp.append(event_id)
    jq += ' ORDER BY e.code,e.name,j.name'
    judge_certs      = db.execute(jq, jp).fetchall()
    active_templates = get_active_templates()
    db.close()
    return render_template('judge_certificates.html',
        settings=settings,
        events=events_list,
        judge_certs=judge_certs,
        selected_event=event_id,
        active_templates=active_templates,
        thai_date=format_thai_date(settings.get('competition_date','')))

@app.route('/results')
@login_required
def all_results():
    db     = get_db()
    events = db.execute('SELECT * FROM events ORDER BY code, name').fetchall()
    parts  = db.execute('''
        SELECT p.event_id, p.rank_pos, p.award, p.score,
               st.name as student_name, st.class_level,
               s.name  as school_name,  s.id as school_id
        FROM participants p
        JOIN students st ON st.id = p.student_id
        JOIN schools  s  ON s.id  = st.school_id
        WHERE p.award IS NOT NULL
        ORDER BY p.event_id, COALESCE(p.rank_pos, 999), st.name
    ''').fetchall()
    db.close()
    results_by_event = {}
    for p in parts:
        results_by_event.setdefault(p['event_id'], []).append(p)
    my_school_id = session.get('school_id') if session.get('role') == 'school' else None
    return render_template('all_results.html',
        events=events,
        results_by_event=results_by_event,
        my_school_id=my_school_id)

# ── API ───────────────────────────────────────────────────

@app.get('/api/schools/<int:sid>/students')
@login_required
def api_students(sid):
    db = get_db()
    rows = db.execute(
        'SELECT id,name,class_level FROM students WHERE school_id=%s ORDER BY name', [sid]
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.get('/api/schools/<int:sid>/teachers')
@login_required
def api_teachers(sid):
    db = get_db()
    rows = db.execute(
        'SELECT id,name,position FROM teachers WHERE school_id=%s ORDER BY name', [sid]
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

# ── Certificate Templates ─────────────────────────────────

TEMPLATE_FIELDS = {
    'student': [
        {'key':'name',   'label':'ชื่อ-สกุล',          'x':50,'y':42,'size':32,'bold':True, 'color':'#1a1a1a'},
        {'key':'school', 'label':'โรงเรียน',            'x':50,'y':52,'size':22,'bold':False,'color':'#333333'},
        {'key':'award',  'label':'รางวัล',              'x':50,'y':62,'size':28,'bold':True, 'color':'#8b6914'},
        {'key':'event',  'label':'กิจกรรม + ระดับ',     'x':50,'y':71,'size':20,'bold':False,'color':'#1a5276'},
        {'key':'date',   'label':'วันที่',              'x':50,'y':83,'size':16,'bold':False,'color':'#666666'},
    ],
    'coach': [
        {'key':'name',     'label':'ชื่อ-สกุล',         'x':50,'y':42,'size':32,'bold':True, 'color':'#1a1a1a'},
        {'key':'position', 'label':'ตำแหน่ง + โรงเรียน','x':50,'y':52,'size':20,'bold':False,'color':'#333333'},
        {'key':'event',    'label':'กิจกรรม + ระดับ',   'x':50,'y':63,'size':20,'bold':False,'color':'#1a5276'},
        {'key':'award',    'label':'รางวัลที่ได้รับ',   'x':50,'y':73,'size':24,'bold':True, 'color':'#8b6914'},
        {'key':'date',     'label':'วันที่',            'x':50,'y':84,'size':16,'bold':False,'color':'#666666'},
    ],
    'judge': [
        {'key':'name',     'label':'ชื่อ-สกุล',         'x':50,'y':42,'size':32,'bold':True, 'color':'#1a1a1a'},
        {'key':'position', 'label':'ตำแหน่ง',           'x':50,'y':52,'size':22,'bold':False,'color':'#333333'},
        {'key':'event',    'label':'กิจกรรม + ระดับ',   'x':50,'y':65,'size':20,'bold':False,'color':'#1a5276'},
        {'key':'date',     'label':'วันที่',            'x':50,'y':82,'size':16,'bold':False,'color':'#666666'},
    ],
}

@app.route('/admin/templates')
@admin_required
def admin_templates():
    db = get_db()
    tmpls = db.execute(
        'SELECT id,name,cert_type,is_active,created_at FROM cert_templates ORDER BY cert_type,name'
    ).fetchall()
    db.close()
    return render_template('admin_templates.html', templates=tmpls,
                           type_labels={'student':'นักเรียน','coach':'ครูผู้ฝึกสอน','judge':'กรรมการ'})

@app.route('/admin/templates/upload', methods=['POST'])
@admin_required
def admin_upload_template():
    name      = request.form['name'].strip()
    cert_type = request.form['cert_type']
    f         = request.files.get('image')
    if not name or not cert_type or not f or not f.filename:
        flash('กรุณากรอกข้อมูลให้ครบและเลือกไฟล์รูป', 'warning')
        return redirect(url_for('admin_templates'))
    img_data = base64.b64encode(f.read()).decode('utf-8')
    mime     = f.content_type or 'image/png'
    config   = json.dumps(TEMPLATE_FIELDS.get(cert_type, TEMPLATE_FIELDS['student']))
    db = get_db()
    cur = db.execute(
        'INSERT INTO cert_templates (name,cert_type,image_data,image_mime,config,is_active) VALUES (%s,%s,%s,%s,%s,0) RETURNING id',
        [name, cert_type, img_data, mime, config]
    )
    tid = cur.fetchone()['id']
    db.commit()
    db.close()
    flash(f'อัปโหลด "{name}" สำเร็จ — จัดวางตำแหน่งข้อความได้เลย', 'success')
    return redirect(url_for('admin_template_edit', tid=tid))

@app.route('/admin/templates/<int:tid>/edit')
@admin_required
def admin_template_edit(tid):
    db = get_db()
    tmpl = db.execute('SELECT * FROM cert_templates WHERE id=%s', [tid]).fetchone()
    db.close()
    if not tmpl:
        flash('ไม่พบ template', 'danger')
        return redirect(url_for('admin_templates'))
    config = json.loads(tmpl['config'])
    return render_template('admin_template_editor.html', tmpl=tmpl, config=config,
                           type_labels={'student':'นักเรียน','coach':'ครูผู้ฝึกสอน','judge':'กรรมการ'})

@app.route('/admin/templates/<int:tid>/save', methods=['POST'])
@admin_required
def admin_save_template(tid):
    data   = request.get_json()
    config = json.dumps(data.get('config', []))
    db = get_db()
    db.execute('UPDATE cert_templates SET config=%s WHERE id=%s', [config, tid])
    db.commit()
    db.close()
    return jsonify({'ok': True})

@app.route('/admin/templates/<int:tid>/activate', methods=['POST'])
@admin_required
def admin_activate_template(tid):
    db = get_db()
    cert_type = db.execute('SELECT cert_type FROM cert_templates WHERE id=%s', [tid]).fetchone()['cert_type']
    db.execute('UPDATE cert_templates SET is_active=0 WHERE cert_type=%s', [cert_type])
    db.execute('UPDATE cert_templates SET is_active=1 WHERE id=%s', [tid])
    db.commit()
    db.close()
    flash('เปิดใช้งาน template แล้ว', 'success')
    return redirect(url_for('admin_templates'))

@app.route('/admin/templates/<int:tid>/deactivate', methods=['POST'])
@admin_required
def admin_deactivate_template(tid):
    db = get_db()
    db.execute('UPDATE cert_templates SET is_active=0 WHERE id=%s', [tid])
    db.commit()
    db.close()
    return redirect(url_for('admin_templates'))

@app.route('/admin/templates/<int:tid>/delete', methods=['POST'])
@admin_required
def admin_delete_template(tid):
    db = get_db()
    db.execute('DELETE FROM cert_templates WHERE id=%s', [tid])
    db.commit()
    db.close()
    flash('ลบ template แล้ว', 'info')
    return redirect(url_for('admin_templates'))

@app.get('/uploads/template/<int:tid>')
def serve_template_image(tid):
    db = get_db()
    row = db.execute('SELECT image_data, image_mime FROM cert_templates WHERE id=%s', [tid]).fetchone()
    db.close()
    if not row:
        return '', 404
    img_bytes = base64.b64decode(row['image_data'])
    return send_file(io.BytesIO(img_bytes), mimetype=row['image_mime'])

def get_active_templates():
    db = get_db()
    rows = db.execute(
        'SELECT id, cert_type, config FROM cert_templates WHERE is_active=1'
    ).fetchall()
    db.close()
    return {r['cert_type']: {'id': r['id'], 'config': json.loads(r['config'])} for r in rows}

# ─────────────────────────────────────────────────────────

init_db()

if __name__ == '__main__':
    print('✅  เปิดเบราว์เซอร์ที่  http://localhost:5000')
    app.run(debug=True, port=5000)
