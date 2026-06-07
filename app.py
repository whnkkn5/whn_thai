from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import sqlite3
import os
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

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

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'school',
            school_id INTEGER REFERENCES schools(id) ON DELETE SET NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
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
        CREATE TABLE IF NOT EXISTS cert_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            cert_type TEXT NOT NULL,
            image_data TEXT NOT NULL,
            image_mime TEXT DEFAULT 'image/png',
            config TEXT DEFAULT '[]',
            is_active INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    ''')

    # Create default admin if none exists
    admin = db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
    if not admin:
        db.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                   ['admin', generate_password_hash('admin1234', method='pbkdf2:sha256'), 'admin'])
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
        'SELECT id, score FROM participants WHERE event_id=? AND score IS NOT NULL ORDER BY score DESC',
        [event_id]
    ).fetchall()
    for i, row in enumerate(rows):
        rank = i + 1
        score = row['score']
        if rank == 1 and score >= 80:   award = 'เหรียญทอง ชนะเลิศ'
        elif rank == 2 and score >= 80: award = 'เหรียญทอง รองชนะเลิศอันดับ 1'
        elif rank == 3 and score >= 80: award = 'เหรียญทอง รองชนะเลิศอันดับ 2'
        elif score >= 80: award = 'เหรียญทอง'
        elif score >= 70: award = 'เหรียญเงิน'
        elif score >= 60: award = 'เหรียญทองแดง'
        else:             award = 'เข้าร่วม'
        db.execute('UPDATE participants SET rank_pos=?, award=? WHERE id=?', [rank, award, row['id']])
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
        user = db.execute('SELECT * FROM users WHERE username=?', [username]).fetchone()
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
        GROUP BY e.id ORDER BY e.level, e.name
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
    school   = db.execute('SELECT * FROM schools WHERE id=?', [sid]).fetchone() if sid else None
    students = db.execute('SELECT * FROM students WHERE school_id=? ORDER BY name', [sid]).fetchall() if sid else []
    teachers = db.execute('SELECT * FROM teachers WHERE school_id=? ORDER BY name', [sid]).fetchall() if sid else []
    events   = db.execute('SELECT * FROM events ORDER BY level, name').fetchall() if sid else []

    parts_by_event   = {}
    coaches_by_event = {}
    if sid:
        for p in db.execute('''
            SELECT p.id, p.score, p.rank_pos, p.award, p.event_id,
                   st.name as student_name, st.id as student_id, st.class_level
            FROM participants p JOIN students st ON st.id=p.student_id
            WHERE st.school_id=?
            ORDER BY p.event_id, COALESCE(p.rank_pos,999), st.name
        ''', [sid]).fetchall():
            parts_by_event.setdefault(p['event_id'], []).append(p)

        for c in db.execute('''
            SELECT c.id, c.event_id, t.name as teacher_name, t.id as teacher_id, t.position
            FROM coaches c JOIN teachers t ON t.id=c.teacher_id
            WHERE t.school_id=?
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
        db.execute('INSERT INTO users (username, password_hash, role, school_id) VALUES (?,?,?,?)',
                   [username, generate_password_hash(password, method='pbkdf2:sha256'), role, school_id])
        db.commit()
        flash(f'เพิ่มผู้ใช้ "{username}" แล้ว', 'success')
    except Exception:
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
    db.execute('DELETE FROM users WHERE id=?', [uid])
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
    db.execute('UPDATE users SET password_hash=? WHERE id=?',
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
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       [key, request.form.get(key, '')])
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
        name  = request.form['name'].strip()
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
@admin_required
def delete_event(eid):
    db = get_db()
    db.execute('DELETE FROM events WHERE id=?', [eid])
    db.commit()
    db.close()
    flash('ลบกิจกรรมแล้ว', 'info')
    return redirect(url_for('events'))

@app.route('/event/<int:eid>')
@admin_required
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
        JOIN schools  s  ON s.id=st.school_id
        WHERE p.event_id=? ORDER BY COALESCE(p.rank_pos,999), st.name
    ''', [eid]).fetchall()
    coaches = db.execute('''
        SELECT c.id, t.name as teacher_name, t.position, s.name as school_name
        FROM coaches c
        JOIN teachers t ON t.id=c.teacher_id
        JOIN schools  s ON s.id=t.school_id
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
@login_required
def add_participant(eid):
    student_id = request.form.get('student_id')
    is_school  = session.get('role') == 'school'
    back       = url_for('school_home') if is_school else url_for('event_detail', eid=eid)
    if student_id:
        db = get_db()
        if is_school:
            st = db.execute('SELECT school_id FROM students WHERE id=?', [student_id]).fetchone()
            if not st or st['school_id'] != session.get('school_id'):
                flash('ไม่มีสิทธิ์ลงทะเบียนนักเรียนของโรงเรียนอื่น', 'danger')
                db.close()
                return redirect(back)
        try:
            db.execute('INSERT INTO participants (event_id,student_id) VALUES (?,?)', [eid, student_id])
            db.commit()
        except Exception:
            flash('นักเรียนคนนี้ลงทะเบียนในกิจกรรมนี้แล้ว', 'warning')
        db.close()
    return redirect(back)

@app.route('/participants/<int:pid>/delete', methods=['POST'])
@login_required
def delete_participant(pid):
    db  = get_db()
    row = db.execute('''SELECT p.event_id, st.school_id
                        FROM participants p JOIN students st ON st.id=p.student_id
                        WHERE p.id=?''', [pid]).fetchone()
    eid       = row['event_id']
    is_school = session.get('role') == 'school'
    if is_school and row['school_id'] != session.get('school_id'):
        flash('ไม่มีสิทธิ์', 'danger')
        db.close()
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM participants WHERE id=?', [pid])
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
        if is_school:
            t = db.execute('SELECT school_id FROM teachers WHERE id=?', [teacher_id]).fetchone()
            if not t or t['school_id'] != session.get('school_id'):
                flash('ไม่มีสิทธิ์', 'danger')
                db.close()
                return redirect(back)
        try:
            db.execute('INSERT INTO coaches (event_id,teacher_id) VALUES (?,?)', [eid, teacher_id])
            db.commit()
        except Exception:
            flash('ครูคนนี้ลงทะเบียนในกิจกรรมนี้แล้ว', 'warning')
        db.close()
    return redirect(back)

@app.route('/coaches/<int:cid>/delete', methods=['POST'])
@login_required
def delete_coach(cid):
    db  = get_db()
    row = db.execute('''SELECT c.event_id, t.school_id
                        FROM coaches c JOIN teachers t ON t.id=c.teacher_id
                        WHERE c.id=?''', [cid]).fetchone()
    eid       = row['event_id']
    is_school = session.get('role') == 'school'
    if is_school and row['school_id'] != session.get('school_id'):
        flash('ไม่มีสิทธิ์', 'danger')
        db.close()
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM coaches WHERE id=?', [cid])
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
            db.execute('INSERT INTO event_judges (event_id,judge_id) VALUES (?,?)', [eid, judge_id])
            db.commit()
        except Exception:
            pass
        db.close()
    return redirect(url_for('event_detail', eid=eid))

@app.route('/event_judges/<int:ejid>/delete', methods=['POST'])
@admin_required
def delete_event_judge(ejid):
    db = get_db()
    row = db.execute('SELECT event_id FROM event_judges WHERE id=?', [ejid]).fetchone()
    eid = row['event_id']
    db.execute('DELETE FROM event_judges WHERE id=?', [ejid])
    db.commit()
    db.close()
    return redirect(url_for('event_detail', eid=eid))

# ── Scores ────────────────────────────────────────────────

@app.route('/event/<int:eid>/scores', methods=['GET', 'POST'])
@admin_required
def event_scores(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=?', [eid]).fetchone()
    if request.method == 'POST':
        rows = db.execute('SELECT id FROM participants WHERE event_id=?', [eid]).fetchall()
        for row in rows:
            val = request.form.get(f'score_{row["id"]}', '').strip()
            if val:
                try:
                    db.execute('UPDATE participants SET score=? WHERE id=?', [float(val), row['id']])
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
        WHERE p.event_id=? ORDER BY st.name
    ''', [eid]).fetchall()
    db.close()
    return render_template('scores.html', event=event, participants=participants)

@app.route('/event/<int:eid>/results')
@admin_required
def event_results(eid):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=?', [eid]).fetchone()
    participants = db.execute('''
        SELECT p.*, st.name as student_name, s.name as school_name, s.id as school_id
        FROM participants p
        JOIN students st ON st.id=p.student_id
        JOIN schools  s  ON s.id=st.school_id
        WHERE p.event_id=? ORDER BY COALESCE(p.rank_pos,999)
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
        LEFT JOIN teachers t  ON t.school_id=s.id
        GROUP BY s.id ORDER BY s.name
    ''').fetchall()
    db.close()
    return render_template('schools.html', schools=rows)

@app.route('/schools/<int:sid>/delete', methods=['POST'])
@admin_required
def delete_school(sid):
    db = get_db()
    db.execute('DELETE FROM schools WHERE id=?', [sid])
    db.commit()
    db.close()
    flash('ลบโรงเรียนแล้ว', 'info')
    return redirect(url_for('schools'))

@app.route('/schools/<int:sid>')
@login_required
def school_detail(sid):
    # School users can only access their own school
    if session.get('role') == 'school' and session.get('school_id') != sid:
        return redirect(url_for('school_home'))
    db = get_db()
    school   = db.execute('SELECT * FROM schools WHERE id=?', [sid]).fetchone()
    students = db.execute('SELECT * FROM students WHERE school_id=? ORDER BY name', [sid]).fetchall()
    teachers = db.execute('SELECT * FROM teachers WHERE school_id=? ORDER BY name', [sid]).fetchall()
    db.close()
    return render_template('school_detail.html', school=school,
                           students=students, teachers=teachers)

@app.route('/schools/<int:sid>/students/add', methods=['POST'])
@login_required
def add_student(sid):
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        return redirect(url_for('school_home'))
    name        = request.form['name'].strip()
    class_level = request.form.get('class_level', '').strip()
    if name:
        db = get_db()
        db.execute('INSERT INTO students (name,school_id,class_level) VALUES (?,?,?)',
                   [name, sid, class_level])
        db.commit()
        db.close()
    back = url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid)
    return redirect(back)

@app.route('/students/<int:stid>/delete', methods=['POST'])
@login_required
def delete_student(stid):
    db = get_db()
    row = db.execute('SELECT school_id FROM students WHERE id=?', [stid]).fetchone()
    sid = row['school_id']
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM students WHERE id=?', [stid])
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
        db.execute('INSERT INTO teachers (name,school_id,position) VALUES (?,?,?)',
                   [name, sid, position])
        db.commit()
        db.close()
    back = url_for('school_home') if session.get('role') == 'school' else url_for('school_detail', sid=sid)
    return redirect(back)

@app.route('/teachers/<int:tid>/delete', methods=['POST'])
@login_required
def delete_teacher(tid):
    db = get_db()
    row = db.execute('SELECT school_id FROM teachers WHERE id=?', [tid]).fetchone()
    sid = row['school_id']
    if session.get('role') == 'school' and session.get('school_id') != sid:
        flash('ไม่มีสิทธิ์', 'danger')
        return redirect(url_for('school_home'))
    db.execute('DELETE FROM teachers WHERE id=?', [tid])
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
            db.execute('INSERT INTO judges (name,position) VALUES (?,?)', [name, position])
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
    db.execute('DELETE FROM judges WHERE id=?', [jid])
    db.commit()
    db.close()
    return redirect(url_for('judges'))

# ── Certificates ──────────────────────────────────────────

@app.route('/certificates')
@login_required
def certificates():
    db = get_db()
    settings = get_settings()

    # School users are restricted to their own school
    is_school = session.get('role') == 'school'
    school_id = session.get('school_id') if is_school else request.args.get('school_id', type=int)
    event_id  = request.args.get('event_id', type=int)
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
    if school_id: sq += ' AND s.id=?'; sp.append(school_id)
    if event_id:  sq += ' AND e.id=?'; sp.append(event_id)
    sq += ' ORDER BY e.level,e.name,p.rank_pos'
    student_certs = db.execute(sq, sp).fetchall()

    # Coach certificates
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
    if school_id: cq += ' AND s.id=?'; cp.append(school_id)
    if event_id:  cq += ' AND e.id=?'; cp.append(event_id)
    cq += ' ORDER BY e.level,e.name,t.name'
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
    events_list = db.execute('SELECT * FROM events ORDER BY level,name').fetchall()
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

# ── API ───────────────────────────────────────────────────

@app.get('/api/schools/<int:sid>/students')
@login_required
def api_students(sid):
    db = get_db()
    rows = db.execute(
        'SELECT id,name,class_level FROM students WHERE school_id=? ORDER BY name', [sid]
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.get('/api/schools/<int:sid>/teachers')
@login_required
def api_teachers(sid):
    db = get_db()
    rows = db.execute(
        'SELECT id,name,position FROM teachers WHERE school_id=? ORDER BY name', [sid]
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

import json, base64
from flask import send_file
import io

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
    img_data  = base64.b64encode(f.read()).decode('utf-8')
    mime      = f.content_type or 'image/png'
    config    = json.dumps(TEMPLATE_FIELDS.get(cert_type, TEMPLATE_FIELDS['student']))
    db = get_db()
    db.execute(
        'INSERT INTO cert_templates (name,cert_type,image_data,image_mime,config,is_active) VALUES (?,?,?,?,?,0)',
        [name, cert_type, img_data, mime, config]
    )
    db.commit()
    tid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.close()
    flash(f'อัปโหลด "{name}" สำเร็จ — จัดวางตำแหน่งข้อความได้เลย', 'success')
    return redirect(url_for('admin_template_edit', tid=tid))

@app.route('/admin/templates/<int:tid>/edit')
@admin_required
def admin_template_edit(tid):
    db = get_db()
    tmpl = db.execute('SELECT * FROM cert_templates WHERE id=?', [tid]).fetchone()
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
    db.execute('UPDATE cert_templates SET config=? WHERE id=?', [config, tid])
    db.commit()
    db.close()
    return jsonify({'ok': True})

@app.route('/admin/templates/<int:tid>/activate', methods=['POST'])
@admin_required
def admin_activate_template(tid):
    db = get_db()
    cert_type = db.execute('SELECT cert_type FROM cert_templates WHERE id=?', [tid]).fetchone()['cert_type']
    db.execute('UPDATE cert_templates SET is_active=0 WHERE cert_type=?', [cert_type])
    db.execute('UPDATE cert_templates SET is_active=1 WHERE id=?', [tid])
    db.commit()
    db.close()
    flash('เปิดใช้งาน template แล้ว', 'success')
    return redirect(url_for('admin_templates'))

@app.route('/admin/templates/<int:tid>/deactivate', methods=['POST'])
@admin_required
def admin_deactivate_template(tid):
    db = get_db()
    db.execute('UPDATE cert_templates SET is_active=0 WHERE id=?', [tid])
    db.commit()
    db.close()
    return redirect(url_for('admin_templates'))

@app.route('/admin/templates/<int:tid>/delete', methods=['POST'])
@admin_required
def admin_delete_template(tid):
    db = get_db()
    db.execute('DELETE FROM cert_templates WHERE id=?', [tid])
    db.commit()
    db.close()
    flash('ลบ template แล้ว', 'info')
    return redirect(url_for('admin_templates'))

@app.get('/uploads/template/<int:tid>')
def serve_template_image(tid):
    db = get_db()
    row = db.execute('SELECT image_data, image_mime FROM cert_templates WHERE id=?', [tid]).fetchone()
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
