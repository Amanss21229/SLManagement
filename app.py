import os
import psycopg2
import psycopg2.extras
import csv
import hashlib
import uuid
import json
import zipfile
import shutil
from datetime import datetime
from dateutil.relativedelta import relativedelta
from functools import wraps
from urllib.parse import quote
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, send_from_directory, jsonify, session
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image
from user_agents import parse as parse_user_agent
import io

app = Flask(__name__)

if not os.environ.get('SESSION_SECRET'):
    raise RuntimeError("SESSION_SECRET environment variable must be set for security. Please add it to Replit Secrets.")
app.secret_key = os.environ.get('SESSION_SECRET')

UPLOAD_FOLDER = 'uploads'
PDF_FOLDER = 'pdfs'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
DATABASE_URL = os.environ.get('DATABASE_URL')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs('static/logo', exist_ok=True)
os.makedirs('backups', exist_ok=True)
BACKUP_FOLDER = 'backups'

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def generate_pdf_token(admission_number):
    secret = os.environ.get('SESSION_SECRET', 'default')
    data = f"{admission_number}-{secret}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]

def verify_pdf_token(admission_number, token):
    expected_token = generate_pdf_token(admission_number)
    return token == expected_token

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id SERIAL PRIMARY KEY,
            admission_number TEXT UNIQUE NOT NULL,
            photo_path TEXT,
            name TEXT NOT NULL,
            father_name TEXT NOT NULL,
            mother_name TEXT,
            dob TEXT,
            gender TEXT,
            class TEXT,
            board TEXT,
            medium TEXT,
            school_name TEXT,
            address TEXT,
            mobile1 TEXT,
            mobile2 TEXT,
            fee_per_month REAL,
            discount REAL DEFAULT 0,
            admission_date TEXT,
            other_details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fees (
            id SERIAL PRIMARY KEY,
            student_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            fee_amount REAL NOT NULL,
            is_paid INTEGER DEFAULT 0,
            payment_date TEXT,
            payment_mode TEXT,
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            UNIQUE(student_id, month, year)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS institute_info (
            id INTEGER PRIMARY KEY,
            logo_path TEXT,
            address TEXT,
            contact TEXT,
            signature_path TEXT
        )
    ''')
    
    cursor.execute('SELECT COUNT(*) FROM institute_info')
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO institute_info (id, address, contact) 
            VALUES (1, 'Chandmari Road Kankarbagh gali no. 06 ke thik saamne', '9296820840, 9153021229')
        ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS manager_sessions (
            id SERIAL PRIMARY KEY,
            session_id TEXT UNIQUE NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            device_name TEXT,
            os TEXT,
            browser TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

def generate_admission_number():
    conn = get_db()
    cursor = conn.cursor()
    year = datetime.now().year
    cursor.execute('SELECT COUNT(*) FROM students WHERE admission_number LIKE %s', (f'SL{year}%',))
    count = cursor.fetchone()[0]
    conn.close()
    return f'SL{year}{str(count + 1).zfill(4)}'

def ensure_fee_records(student_id, admission_date, fee_per_month, discount=0.0):
    if not admission_date:
        return
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        admission_dt = datetime.strptime(admission_date, '%Y-%m-%d')
    except:
        conn.close()
        return
    
    current_dt = datetime.now()
    net_fee = fee_per_month - discount
    
    temp_dt = admission_dt
    while temp_dt <= current_dt:
        month = temp_dt.month
        year = temp_dt.year
        
        cursor.execute('''
            SELECT id FROM fees WHERE student_id = %s AND month = %s AND year = %s
        ''', (student_id, month, year))
        
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO fees (student_id, month, year, fee_amount, is_paid)
                VALUES (%s, %s, %s, %s, 0)
            ''', (student_id, month, year, net_fee))
        
        temp_dt = temp_dt.replace(day=1) + relativedelta(months=1)
    
    conn.commit()
    conn.close()

def get_unpaid_months_details(student_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('''
        SELECT month, year, fee_amount FROM fees 
        WHERE student_id = %s AND is_paid = FALSE
        ORDER BY year, month
    ''', (student_id,))
    
    unpaid = cursor.fetchall()
    conn.close()
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    unpaid_list = []
    total_due = 0
    
    for record in unpaid:
        month_name = months[record['month']]
        year = record['year']
        amount = record['fee_amount']
        unpaid_list.append(f"{month_name} {year} - Rs {amount:.2f}")
        total_due += amount
    
    return unpaid_list, total_due

def build_whatsapp_url(mobile, student_name, admission_no, unpaid_list, total_due, demand_bill_url=''):
    if not mobile:
        return ''
    
    mobile = mobile.strip().replace('+91', '').replace(' ', '').replace('-', '')
    
    message = f"""✨ *Greetings from SANSA LEARN* ✨

Dear Parent/Guardian,

This is a courteous reminder regarding the tuition fee status for your ward.

👤 *Student Details:*
Name: {student_name}
Admission No: {admission_no}

📋 *Outstanding Fee Details:*
"""
    
    if unpaid_list:
        for item in unpaid_list:
            message += f"\n• {item}"
        message += f"\n\n💰 *Total Amount Due: Rs {total_due:.2f}*"
    else:
        message += "\nAll fees are up to date! ✅"
    
    if demand_bill_url:
        message += f"\n\n📄 Download Demand Bill:\n{demand_bill_url}"
    
    message += "\n\nKindly clear the outstanding amount at your earliest convenience. For any queries, feel free to contact us.\n\n🙏 Thank you for your cooperation.\n\n*SANSA LEARN*\nChandmari Road Kankarbagh\n📞 9296820840, 9153021229"
    
    encoded_message = quote(message)
    whatsapp_url = f"https://wa.me/91{mobile}?text={encoded_message}"
    
    return whatsapp_url

def build_registration_whatsapp_url(mobile, student_name, admission_no, father_name, class_name, profile_pdf_url):
    if not mobile:
        return ''
    
    mobile = mobile.strip().replace('+91', '').replace(' ', '').replace('-', '')
    
    message = f"""🎉 *REGISTRATION SUCCESSFUL!* 🎉

━━━━━━━━━━━━━━━━━━━━━
     ✨ *SANSA LEARN* ✨
    _Where Learning Shines_
━━━━━━━━━━━━━━━━━━━━━

Dear Parents,

We are delighted to welcome *{student_name}* to the SANSA LEARN family! 🌟

📋 *Registration Details:*
┌─────────────────────────
│ 👤 Student: {student_name}
│ 🔢 Admission No: *{admission_no}*
│ 📚 Class: {class_name}
└─────────────────────────

📎 *Download Registration Card:*
{profile_pdf_url}

━━━━━━━━━━━━━━━━━━━━━

📍 *SANSA LEARN*
Chandmari Road, Kankarbagh
📞 9296820840 | 9153021229

_Thank you for trusting us with your child's education!_

🙏 *Best Wishes*
Team SANSA LEARN"""
    
    encoded_message = quote(message)
    whatsapp_url = f"https://wa.me/91{mobile}?text={encoded_message}"
    
    return whatsapp_url

def get_client_ip():
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr or 'Unknown'

def create_session_record():
    session_id = str(uuid.uuid4())
    user_agent_string = request.headers.get('User-Agent', '')
    user_agent = parse_user_agent(user_agent_string)
    
    device_name = str(user_agent.device.family) if user_agent.device.family else 'Unknown Device'
    os_name = f"{user_agent.os.family} {user_agent.os.version_string}".strip()
    browser_name = f"{user_agent.browser.family} {user_agent.browser.version_string}".strip()
    ip_address = get_client_ip()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO manager_sessions (session_id, ip_address, user_agent, device_name, os, browser)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (session_id, ip_address, user_agent_string, device_name, os_name, browser_name))
    conn.commit()
    conn.close()
    
    return session_id

@app.before_request
def check_session_validity():
    if session.get('authenticated') and session.get('session_record_id'):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''
            SELECT is_active FROM manager_sessions WHERE session_id = %s
        ''', (session.get('session_record_id'),))
        result = cursor.fetchone()
        
        if result and result['is_active'] == 1:
            cursor.execute('''
                UPDATE manager_sessions SET last_seen_at = CURRENT_TIMESTAMP WHERE session_id = %s
            ''', (session.get('session_record_id'),))
            conn.commit()
        elif result and result['is_active'] == 0:
            conn.close()
            session.clear()
            flash('Your session was terminated by the administrator.', 'warning')
            return redirect(url_for('login'))
        
        conn.close()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        admin_password = os.environ.get('MANAGER_PASSWORD', '')
        
        if not admin_password:
            flash('System configuration error. Please contact administrator.', 'error')
            return render_template('login.html')
        
        if password == admin_password:
            session_id = create_session_record()
            session['authenticated'] = True
            session['session_record_id'] = session_id
            session.permanent = True
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Incorrect password. Please try again.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    if session.get('session_record_id'):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE manager_sessions SET is_active = FALSE WHERE session_id = %s
        ''', (session.get('session_record_id'),))
        conn.commit()
        conn.close()
    session.clear()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM students')
    total_students = cursor.fetchone()[0]
    
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    cursor.execute('''
        SELECT COALESCE(SUM(fee_amount), 0) FROM fees 
        WHERE month = %s AND year = %s AND is_paid = TRUE
    ''', (current_month, current_year))
    total_paid_this_month = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COALESCE(SUM(f.fee_amount), 0) FROM fees f
        JOIN students s ON f.student_id = s.id
        WHERE f.month = %s AND f.year = %s AND f.is_paid = FALSE
    ''', (current_month, current_year))
    total_pending_this_month = cursor.fetchone()[0]
    
    conn.close()
    
    return render_template('dashboard.html', 
                         total_students=total_students,
                         total_paid=total_paid_this_month,
                         total_pending=total_pending_this_month)

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/students')
@login_required
def list_students():
    search_query = request.args.get('search', '')
    search_type = request.args.get('search_type', 'name')
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    if search_query:
        if search_type == 'admission':
            cursor.execute('SELECT * FROM students WHERE admission_number ILIKE %s ORDER BY created_at DESC', 
                         (f'%{search_query}%',))
        elif search_type == 'father':
            cursor.execute('SELECT * FROM students WHERE father_name ILIKE %s ORDER BY created_at DESC', 
                         (f'%{search_query}%',))
        else:
            cursor.execute('SELECT * FROM students WHERE name ILIKE %s ORDER BY created_at DESC', 
                         (f'%{search_query}%',))
    else:
        cursor.execute('SELECT * FROM students ORDER BY created_at DESC')
    
    students = cursor.fetchall()
    conn.close()
    
    return render_template('students.html', students=students, 
                         search_query=search_query, search_type=search_type)

@app.route('/student/add', methods=['GET', 'POST'])
@login_required
def add_student():
    if request.method == 'POST':
        try:
            admission_number = generate_admission_number()
            
            photo_path = None
            if 'photo' in request.files:
                file = request.files['photo']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(f"{admission_number}_{file.filename}")
                    filepath = os.path.join(UPLOAD_FOLDER, filename)
                    file.save(filepath)
                    photo_path = filepath
            
            conn = get_db()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO students (
                    admission_number, photo_path, name, father_name, mother_name,
                    dob, gender, class, board, medium, school_name, address,
                    mobile1, mobile2, fee_per_month, discount, admission_date, other_details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (
                admission_number,
                photo_path,
                request.form['name'],
                request.form['father_name'],
                request.form.get('mother_name', ''),
                request.form.get('dob', ''),
                request.form.get('gender', ''),
                request.form.get('class', ''),
                request.form.get('board', ''),
                request.form.get('medium', ''),
                request.form.get('school_name', ''),
                request.form.get('address', ''),
                request.form.get('mobile1', ''),
                request.form.get('mobile2', ''),
                float(request.form.get('fee_per_month', 0)),
                float(request.form.get('discount', 0)),
                request.form.get('admission_date', datetime.now().strftime('%Y-%m-%d')),
                request.form.get('other_details', '')
            ))
            
            student_id = cursor.fetchone()[0]
            conn.commit()
            conn.close()
            
            ensure_fee_records(student_id, request.form.get('admission_date', datetime.now().strftime('%Y-%m-%d')),
                             float(request.form.get('fee_per_month', 0)),
                             float(request.form.get('discount', 0)))
            
            return redirect(url_for('registration_success', student_id=student_id))
            
        except Exception as e:
            flash(f'Error adding student: {str(e)}', 'error')
            return redirect(url_for('add_student'))
    
    return render_template('add_student.html')

@app.route('/student/<int:student_id>/registration-success')
@login_required
def registration_success(student_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE id = %s', (student_id,))
    student = cursor.fetchone()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('list_students'))
    
    conn.close()
    
    token = generate_pdf_token(student['admission_number'])
    profile_pdf_url = url_for('public_student_profile', 
                              admission_number=student['admission_number'], 
                              token=token, 
                              _external=True)
    
    whatsapp_url = build_registration_whatsapp_url(
        student['mobile1'],
        student['name'],
        student['admission_number'],
        student['father_name'],
        student['class'] or 'N/A',
        profile_pdf_url
    )
    
    return render_template('registration_success.html', 
                          student=student, 
                          whatsapp_url=whatsapp_url,
                          profile_pdf_url=profile_pdf_url)

@app.route('/student/<int:student_id>')
@login_required
def view_student(student_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE id = %s', (student_id,))
    student = cursor.fetchone()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('list_students'))
    
    ensure_fee_records(student_id, student['admission_date'], 
                      student['fee_per_month'] or 0, student['discount'] or 0)
    
    cursor.execute('''
        SELECT * FROM fees WHERE student_id = %s ORDER BY year, month
    ''', (student_id,))
    all_fee_records = cursor.fetchall()
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    unpaid_list, total_due = get_unpaid_months_details(student_id)
    
    token = generate_pdf_token(student['admission_number'])
    demand_bill_url = url_for('public_demand_bill', 
                              admission_number=student['admission_number'], 
                              token=token, 
                              _external=True)
    whatsapp_url = build_whatsapp_url(
        student['mobile1'],
        student['name'],
        student['admission_number'],
        unpaid_list,
        total_due,
        demand_bill_url
    )
    
    conn.close()
    
    return render_template('view_student.html', 
                         student=student, 
                         fee_records=all_fee_records,
                         months=months,
                         whatsapp_url=whatsapp_url,
                         total_due=total_due)

@app.route('/student/<int:student_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_student(student_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    if request.method == 'POST':
        try:
            cursor.execute('SELECT photo_path FROM students WHERE id = %s', (student_id,))
            result = cursor.fetchone()
            current_photo = result['photo_path'] if result else None
            photo_path = current_photo
            
            if 'photo' in request.files:
                file = request.files['photo']
                if file and file.filename and allowed_file(file.filename):
                    cursor.execute('SELECT admission_number FROM students WHERE id = %s', (student_id,))
                    result = cursor.fetchone()
                    admission_number = result['admission_number'] if result else None
                    filename = secure_filename(f"{admission_number}_{file.filename}")
                    filepath = os.path.join(UPLOAD_FOLDER, filename)
                    file.save(filepath)
                    photo_path = filepath
            
            cursor.execute('''
                UPDATE students SET
                    photo_path = %s, name = %s, father_name = %s, mother_name = %s,
                    dob = %s, gender = %s, class = %s, board = %s, medium = %s,
                    school_name = %s, address = %s, mobile1 = %s, mobile2 = %s,
                    fee_per_month = %s, discount = %s, admission_date = %s, other_details = %s
                WHERE id = %s
            ''', (
                photo_path,
                request.form['name'],
                request.form['father_name'],
                request.form.get('mother_name', ''),
                request.form.get('dob', ''),
                request.form.get('gender', ''),
                request.form.get('class', ''),
                request.form.get('board', ''),
                request.form.get('medium', ''),
                request.form.get('school_name', ''),
                request.form.get('address', ''),
                request.form.get('mobile1', ''),
                request.form.get('mobile2', ''),
                float(request.form.get('fee_per_month', 0)),
                float(request.form.get('discount', 0)),
                request.form.get('admission_date', ''),
                request.form.get('other_details', ''),
                student_id
            ))
            
            conn.commit()
            flash('Student updated successfully!', 'success')
            return redirect(url_for('view_student', student_id=student_id))
            
        except Exception as e:
            flash(f'Error updating student: {str(e)}', 'error')
        finally:
            conn.close()
    
    cursor.execute('SELECT * FROM students WHERE id = %s', (student_id,))
    student = cursor.fetchone()
    conn.close()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('list_students'))
    
    return render_template('edit_student.html', student=student)

@app.route('/student/<int:student_id>/delete', methods=['POST'])
@login_required
def delete_student(student_id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('SELECT photo_path FROM students WHERE id = %s', (student_id,))
        result = cursor.fetchone()
        if result and result[0] and os.path.exists(result[0]):
            os.remove(result[0])
        
        cursor.execute('DELETE FROM students WHERE id = %s', (student_id,))
        conn.commit()
        conn.close()
        
        flash('Student deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting student: {str(e)}', 'error')
    
    return redirect(url_for('list_students'))

@app.route('/fees')
@login_required
def fee_management():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('''
        SELECT s.id, s.admission_number, s.name, s.father_name, s.fee_per_month, s.discount
        FROM students s
        ORDER BY s.name
    ''')
    students = cursor.fetchall()
    conn.close()
    
    return render_template('fee_management.html', students=students)

@app.route('/student/<int:student_id>/fees')
@login_required
def student_fees(student_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE id = %s', (student_id,))
    student = cursor.fetchone()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('fee_management'))
    
    cursor.execute('''
        SELECT * FROM fees WHERE student_id = %s ORDER BY year DESC, month DESC
    ''', (student_id,))
    fee_records = cursor.fetchall()
    
    cursor.execute('''
        SELECT COALESCE(SUM(fee_amount), 0) FROM fees 
        WHERE student_id = %s AND is_paid = TRUE
    ''', (student_id,))
    total_paid = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COALESCE(SUM(fee_amount), 0) FROM fees 
        WHERE student_id = %s AND is_paid = FALSE
    ''', (student_id,))
    total_pending = cursor.fetchone()[0]
    
    conn.close()
    
    months = ['January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    return render_template('student_fees.html', student=student, 
                         fee_records=fee_records, months=months,
                         total_paid=total_paid, total_pending=total_pending)

@app.route('/student/<int:student_id>/fees/add', methods=['POST'])
@login_required
def add_fee_record(student_id):
    try:
        month = int(request.form['month'])
        year = int(request.form['year'])
        fee_amount = float(request.form['fee_amount'])
        is_paid = True if request.form.get('is_paid') == 'on' else False
        payment_date = request.form.get('payment_date', '')
        payment_mode = request.form.get('payment_mode', '')
        remarks = request.form.get('remarks', '')
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id FROM fees WHERE student_id = %s AND month = %s AND year = %s
        ''', (student_id, month, year))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute('''
                UPDATE fees SET fee_amount = %s, is_paid = %s, payment_date = %s,
                payment_mode = %s, remarks = %s WHERE id = %s
            ''', (fee_amount, is_paid, payment_date, payment_mode, remarks, existing[0]))
        else:
            cursor.execute('''
                INSERT INTO fees (student_id, month, year, fee_amount, is_paid, 
                                payment_date, payment_mode, remarks)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (student_id, month, year, fee_amount, is_paid, 
                  payment_date, payment_mode, remarks))
        
        conn.commit()
        conn.close()
        
        flash('Fee record saved successfully!', 'success')
    except Exception as e:
        flash(f'Error saving fee record: {str(e)}', 'error')
    
    return redirect(url_for('student_fees', student_id=student_id))

@app.route('/fees/<int:fee_id>/delete', methods=['POST'])
@login_required
def delete_fee_record(fee_id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('SELECT student_id FROM fees WHERE id = %s', (fee_id,))
        result = cursor.fetchone()
        student_id = result[0] if result else None
        
        cursor.execute('DELETE FROM fees WHERE id = %s', (fee_id,))
        conn.commit()
        conn.close()
        
        flash('Fee record deleted successfully!', 'success')
        
        if student_id:
            return redirect(url_for('student_fees', student_id=student_id))
    except Exception as e:
        flash(f'Error deleting fee record: {str(e)}', 'error')
    
    return redirect(url_for('fee_management'))

@app.route('/student/<int:student_id>/fee/<int:month>/<int:year>/toggle', methods=['POST'])
@login_required
def toggle_fee_status(student_id, month, year):
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        cursor.execute('''
            SELECT id, is_paid FROM fees WHERE student_id = %s AND month = %s AND year = %s
        ''', (student_id, month, year))
        
        fee_record = cursor.fetchone()
        
        if fee_record:
            new_status = 0 if fee_record['is_paid'] else 1
            payment_date = datetime.now().strftime('%Y-%m-%d') if new_status else None
            
            cursor.execute('''
                UPDATE fees SET is_paid = %s, payment_date = %s, payment_mode = %s
                WHERE id = %s
            ''', (new_status, payment_date, 'Cash' if new_status else None, fee_record['id']))
            
            conn.commit()
            flash(f'Fee marked as {"paid" if new_status else "unpaid"}!', 'success')
        
        conn.close()
    except Exception as e:
        flash(f'Error updating fee status: {str(e)}', 'error')
    
    return redirect(request.referrer or url_for('view_student', student_id=student_id))

@app.route('/students/grid')
@login_required
def students_grid():
    year = request.args.get('year', datetime.now().year, type=int)
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students ORDER BY admission_number')
    students = cursor.fetchall()
    
    student_fees = {}
    for student in students:
        cursor.execute('''
            SELECT month, is_paid FROM fees 
            WHERE student_id = %s AND year = %s
        ''', (student['id'], year))
        
        fees = {row['month']: row['is_paid'] for row in cursor.fetchall()}
        student_fees[student['id']] = fees
    
    available_years = []
    cursor.execute('SELECT DISTINCT year FROM fees ORDER BY year DESC')
    available_years = [row['year'] for row in cursor.fetchall()]
    
    if year not in available_years and available_years:
        available_years.append(year)
        available_years.sort(reverse=True)
    
    conn.close()
    
    months = ['January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    return render_template('students_grid.html', 
                         students=students,
                         student_fees=student_fees,
                         months=months,
                         current_year=year,
                         available_years=available_years)

@app.route('/student/<int:student_id>/receipt/<int:fee_id>')
@login_required
def generate_receipt(student_id, fee_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE id = %s', (student_id,))
    student = cursor.fetchone()
    
    cursor.execute('SELECT * FROM fees WHERE id = %s', (fee_id,))
    fee = cursor.fetchone()
    
    cursor.execute('SELECT * FROM institute_info WHERE id = 1')
    institute = cursor.fetchone()
    
    conn.close()
    
    if not student or not fee:
        flash('Student or fee record not found', 'error')
        return redirect(url_for('fee_management'))
    
    filename = f"receipt_{student['admission_number']}_{fee['month']}_{fee['year']}.pdf"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    
    logo_path = 'static/logo/logo.png'
    if os.path.exists(logo_path):
        try:
            logo_width = 80
            logo_height = 80
            c.drawImage(logo_path, (width - logo_width) / 2, height - 90, 
                       width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
        except:
            pass
    
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height - 110, "SANSA LEARN")
    
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height - 130, institute['address'])
    c.drawCentredString(width/2, height - 145, f"Contact: {institute['contact']}")
    
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 175, "FEE RECEIPT")
    
    y = height - 215
    c.setFont("Helvetica", 11)
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    receipt_no = f"REC{fee['year']}{str(fee['month']).zfill(2)}{str(fee['id']).zfill(4)}"
    c.drawString(50, y, f"Receipt No: {receipt_no}")
    c.drawRightString(width - 50, y, f"Date: {fee['payment_date']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Student Details:")
    y -= 20
    
    c.setFont("Helvetica", 10)
    c.drawString(70, y, f"Admission No: {student['admission_number']}")
    y -= 18
    c.drawString(70, y, f"Name: {student['name']}")
    y -= 18
    c.drawString(70, y, f"Father's Name: {student['father_name']}")
    y -= 18
    c.drawString(70, y, f"Class: {student['class']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Payment Details:")
    y -= 20
    
    c.setFont("Helvetica", 10)
    c.drawString(70, y, f"Fee Month: {months[fee['month']]} {fee['year']}")
    y -= 18
    c.drawString(70, y, f"Amount Paid: Rs. {fee['fee_amount']:.2f}")
    y -= 18
    c.drawString(70, y, f"Payment Mode: {fee['payment_mode']}")
    if fee['remarks']:
        y -= 18
        c.drawString(70, y, f"Remarks: {fee['remarks']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    
    y = 150
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "For Sansa Learn")
    
    signature_path = 'static/logo/signature.jpg'
    if os.path.exists(signature_path):
        try:
            sig_width = 150
            sig_height = 50
            c.drawImage(signature_path, 50, y - 60, width=sig_width, height=sig_height)
        except:
            pass
    
    y -= 70
    c.drawString(50, y, "Management Signature")
    
    c.save()
    
    return send_file(filepath, as_attachment=True)

@app.route('/student/<int:student_id>/demand')
@login_required
def generate_demand_bill(student_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE id = %s', (student_id,))
    student = cursor.fetchone()
    
    cursor.execute('''
        SELECT * FROM fees WHERE student_id = %s AND is_paid = FALSE 
        ORDER BY year, month
    ''', (student_id,))
    unpaid_fees = cursor.fetchall()
    
    cursor.execute('SELECT * FROM institute_info WHERE id = 1')
    institute = cursor.fetchone()
    
    conn.close()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('fee_management'))
    
    filename = f"demand_{student['admission_number']}_{datetime.now().strftime('%Y%m%d')}.pdf"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    
    logo_path = 'static/logo/logo.png'
    if os.path.exists(logo_path):
        try:
            logo_width = 80
            logo_height = 80
            c.drawImage(logo_path, (width - logo_width) / 2, height - 90, 
                       width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
        except:
            pass
    
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height - 110, "SANSA LEARN")
    
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height - 130, institute['address'])
    c.drawCentredString(width/2, height - 145, f"Contact: {institute['contact']}")
    
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 175, "FEE DEMAND NOTICE")
    
    y = height - 215
    c.setFont("Helvetica", 11)
    c.drawRightString(width - 50, y, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Student Details:")
    y -= 20
    
    c.setFont("Helvetica", 10)
    c.drawString(70, y, f"Admission No: {student['admission_number']}")
    y -= 18
    c.drawString(70, y, f"Name: {student['name']}")
    y -= 18
    c.drawString(70, y, f"Father's Name: {student['father_name']}")
    y -= 18
    c.drawString(70, y, f"Class: {student['class']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Pending Fee Details:")
    y -= 25
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(70, y, "Month")
    c.drawString(200, y, "Year")
    c.drawRightString(width - 70, y, "Amount (Rs.)")
    y -= 18
    c.line(70, y, width - 50, y)
    y -= 5
    
    c.setFont("Helvetica", 10)
    total_pending = 0
    for fee in unpaid_fees:
        y -= 15
        if y < 150:
            c.showPage()
            y = height - 50
        
        c.drawString(70, y, months[fee['month']])
        c.drawString(200, y, str(fee['year']))
        c.drawRightString(width - 70, y, f"{fee['fee_amount']:.2f}")
        total_pending += fee['fee_amount']
    
    y -= 20
    c.line(70, y, width - 50, y)
    y -= 20
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(70, y, "Total Pending:")
    c.drawRightString(width - 70, y, f"Rs. {total_pending:.2f}")
    
    y -= 40
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "Kindly clear the above pending fees at the earliest.")
    
    y = 150
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "For Sansa Learn")
    
    signature_path = 'static/logo/signature.jpg'
    if os.path.exists(signature_path):
        try:
            sig_width = 150
            sig_height = 50
            c.drawImage(signature_path, 50, y - 60, width=sig_width, height=sig_height)
        except:
            pass
    
    y -= 70
    c.drawString(50, y, "Management Signature")
    
    c.save()
    
    return send_file(filepath, as_attachment=True)

@app.route('/public/demand/<admission_number>/<token>')
def public_demand_bill(admission_number, token):
    if not verify_pdf_token(admission_number, token):
        return "Invalid or expired link", 403
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE admission_number = %s', (admission_number,))
    student = cursor.fetchone()
    
    if not student:
        conn.close()
        return "Student not found", 404
    
    cursor.execute('''
        SELECT * FROM fees WHERE student_id = %s AND is_paid = FALSE 
        ORDER BY year, month
    ''', (student['id'],))
    unpaid_fees = cursor.fetchall()
    
    cursor.execute('SELECT * FROM institute_info WHERE id = 1')
    institute = cursor.fetchone()
    
    conn.close()
    
    filename = f"demand_{admission_number}_{datetime.now().strftime('%Y%m%d')}.pdf"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    
    logo_path = 'static/logo/logo.png'
    if os.path.exists(logo_path):
        try:
            logo_width = 80
            logo_height = 80
            c.drawImage(logo_path, (width - logo_width) / 2, height - 90, 
                       width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
        except:
            pass
    
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height - 110, "SANSA LEARN")
    
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height - 130, institute['address'] if institute else '')
    c.drawCentredString(width/2, height - 145, f"Contact: {institute['contact']}" if institute else '')
    
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 175, "FEE DEMAND NOTICE")
    
    y = height - 215
    c.setFont("Helvetica", 11)
    c.drawRightString(width - 50, y, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Student Details:")
    y -= 20
    
    c.setFont("Helvetica", 10)
    c.drawString(70, y, f"Admission No: {student['admission_number']}")
    y -= 18
    c.drawString(70, y, f"Name: {student['name']}")
    y -= 18
    c.drawString(70, y, f"Father's Name: {student['father_name']}")
    y -= 18
    c.drawString(70, y, f"Class: {student['class']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Pending Fee Details:")
    y -= 25
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(70, y, "Month")
    c.drawString(200, y, "Year")
    c.drawRightString(width - 70, y, "Amount (Rs.)")
    y -= 18
    c.line(70, y, width - 50, y)
    y -= 5
    
    c.setFont("Helvetica", 10)
    total_pending = 0
    for fee in unpaid_fees:
        y -= 15
        if y < 150:
            c.showPage()
            y = height - 50
        
        c.drawString(70, y, months[fee['month']])
        c.drawString(200, y, str(fee['year']))
        c.drawRightString(width - 70, y, f"{fee['fee_amount']:.2f}")
        total_pending += fee['fee_amount']
    
    y -= 20
    c.line(70, y, width - 50, y)
    y -= 20
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(70, y, "Total Pending:")
    c.drawRightString(width - 70, y, f"Rs. {total_pending:.2f}")
    
    y -= 40
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "Kindly clear the above pending fees at the earliest.")
    
    y = 150
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "For Sansa Learn")
    
    signature_path = 'static/logo/signature.jpg'
    if os.path.exists(signature_path):
        try:
            sig_width = 150
            sig_height = 50
            c.drawImage(signature_path, 50, y - 60, width=sig_width, height=sig_height)
        except:
            pass
    
    y -= 70
    c.drawString(50, y, "Management Signature")
    
    c.save()
    
    return send_file(filepath, as_attachment=True)

@app.route('/public/receipt/<admission_number>/<int:fee_id>/<token>')
def public_receipt(admission_number, fee_id, token):
    if not verify_pdf_token(admission_number, token):
        return "Invalid or expired link", 403
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE admission_number = %s', (admission_number,))
    student = cursor.fetchone()
    
    if not student:
        conn.close()
        return "Student not found", 404
    
    cursor.execute('SELECT * FROM fees WHERE id = %s AND student_id = %s', (fee_id, student['id']))
    fee = cursor.fetchone()
    
    if not fee:
        conn.close()
        return "Receipt not found", 404
    
    cursor.execute('SELECT * FROM institute_info WHERE id = 1')
    institute = cursor.fetchone()
    
    conn.close()
    
    filename = f"receipt_{admission_number}_{fee['month']}_{fee['year']}.pdf"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    
    logo_path = 'static/logo/logo.png'
    if os.path.exists(logo_path):
        try:
            logo_width = 80
            logo_height = 80
            c.drawImage(logo_path, (width - logo_width) / 2, height - 90, 
                       width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
        except:
            pass
    
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height - 110, "SANSA LEARN")
    
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height - 130, institute['address'] if institute else '')
    c.drawCentredString(width/2, height - 145, f"Contact: {institute['contact']}" if institute else '')
    
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 175, "FEE RECEIPT")
    
    y = height - 215
    c.setFont("Helvetica", 11)
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    receipt_no = f"REC{fee['year']}{str(fee['month']).zfill(2)}{str(fee['id']).zfill(4)}"
    c.drawString(50, y, f"Receipt No: {receipt_no}")
    c.drawRightString(width - 50, y, f"Date: {fee['payment_date']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Student Details:")
    y -= 20
    
    c.setFont("Helvetica", 10)
    c.drawString(70, y, f"Admission No: {student['admission_number']}")
    y -= 18
    c.drawString(70, y, f"Name: {student['name']}")
    y -= 18
    c.drawString(70, y, f"Father's Name: {student['father_name']}")
    y -= 18
    c.drawString(70, y, f"Class: {student['class']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Payment Details:")
    y -= 20
    
    c.setFont("Helvetica", 10)
    c.drawString(70, y, f"Fee Month: {months[fee['month']]} {fee['year']}")
    y -= 18
    c.drawString(70, y, f"Amount Paid: Rs. {fee['fee_amount']:.2f}")
    y -= 18
    c.drawString(70, y, f"Payment Mode: {fee['payment_mode']}")
    if fee['remarks']:
        y -= 18
        c.drawString(70, y, f"Remarks: {fee['remarks']}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    
    y = 150
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "For Sansa Learn")
    
    signature_path = 'static/logo/signature.jpg'
    if os.path.exists(signature_path):
        try:
            sig_width = 150
            sig_height = 50
            c.drawImage(signature_path, 50, y - 60, width=sig_width, height=sig_height)
        except:
            pass
    
    y -= 70
    c.drawString(50, y, "Management Signature")
    
    c.save()
    
    return send_file(filepath, as_attachment=True)

@app.route('/public/profile/<admission_number>/<token>')
def public_student_profile(admission_number, token):
    if not verify_pdf_token(admission_number, token):
        return "Invalid or expired link", 403
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT * FROM students WHERE admission_number = %s', (admission_number,))
    student = cursor.fetchone()
    
    if not student:
        conn.close()
        return "Student not found", 404
    
    cursor.execute('SELECT * FROM institute_info WHERE id = 1')
    institute = cursor.fetchone()
    
    conn.close()
    
    filename = f"profile_{admission_number}.pdf"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    
    logo_path = 'static/logo/logo.png'
    if os.path.exists(logo_path):
        try:
            logo_width = 70
            logo_height = 70
            c.drawImage(logo_path, 50, height - 80, 
                       width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
        except:
            pass
    
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width/2, height - 50, "SANSA LEARN")
    
    c.setFont("Helvetica", 10)
    if institute:
        c.drawCentredString(width/2, height - 68, institute['address'] or '')
        c.drawCentredString(width/2, height - 82, f"Contact: {institute['contact']}" if institute['contact'] else '')
    
    y = height - 110
    c.setStrokeColorRGB(0.2, 0.4, 0.6)
    c.setLineWidth(2)
    c.line(50, y, width - 50, y)
    
    c.setFont("Helvetica-Bold", 16)
    c.setFillColorRGB(0.2, 0.4, 0.6)
    c.drawCentredString(width/2, y - 25, "STUDENT REGISTRATION CARD")
    c.setFillColorRGB(0, 0, 0)
    
    y -= 35
    c.setLineWidth(1)
    c.line(50, y, width - 50, y)
    
    photo_x = width - 150
    photo_y = y - 130
    photo_width = 100
    photo_height = 120
    
    if student['photo_path'] and os.path.exists(student['photo_path']):
        try:
            c.drawImage(student['photo_path'], photo_x, photo_y, 
                       width=photo_width, height=photo_height, preserveAspectRatio=True)
            c.rect(photo_x, photo_y, photo_width, photo_height)
        except:
            c.rect(photo_x, photo_y, photo_width, photo_height)
            c.setFont("Helvetica", 8)
            c.drawCentredString(photo_x + photo_width/2, photo_y + photo_height/2, "Photo")
    else:
        c.rect(photo_x, photo_y, photo_width, photo_height)
        c.setFont("Helvetica", 9)
        c.drawCentredString(photo_x + photo_width/2, photo_y + photo_height/2, "No Photo")
    
    y -= 30
    left_margin = 60
    label_x = left_margin
    value_x = left_margin + 120
    
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.2, 0.4, 0.6)
    c.drawString(left_margin, y, "STUDENT INFORMATION")
    c.setFillColorRGB(0, 0, 0)
    
    y -= 25
    details = [
        ("Admission No:", student['admission_number']),
        ("Name:", student['name']),
        ("Father's Name:", student['father_name']),
        ("Mother's Name:", student['mother_name'] or 'N/A'),
        ("Date of Birth:", student['dob'] or 'N/A'),
        ("Gender:", student['gender'] or 'N/A'),
    ]
    
    c.setFont("Helvetica", 10)
    for label, value in details:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(label_x, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(value_x, y, str(value))
        y -= 18
    
    y -= 15
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.2, 0.4, 0.6)
    c.drawString(left_margin, y, "ACADEMIC DETAILS")
    c.setFillColorRGB(0, 0, 0)
    
    y -= 25
    academic_details = [
        ("Class:", student['class'] or 'N/A'),
        ("Board:", student['board'] or 'N/A'),
        ("Medium:", student['medium'] or 'N/A'),
        ("School Name:", student['school_name'] or 'N/A'),
        ("Admission Date:", student['admission_date'] or 'N/A'),
    ]
    
    for label, value in academic_details:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(label_x, y, label)
        c.setFont("Helvetica", 10)
        display_value = str(value)[:40] + "..." if len(str(value)) > 40 else str(value)
        c.drawString(value_x, y, display_value)
        y -= 18
    
    y -= 15
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.2, 0.4, 0.6)
    c.drawString(left_margin, y, "CONTACT INFORMATION")
    c.setFillColorRGB(0, 0, 0)
    
    y -= 25
    contact_details = [
        ("Mobile 1:", student['mobile1'] or 'N/A'),
        ("Mobile 2:", student['mobile2'] or 'N/A'),
        ("Address:", student['address'] or 'N/A'),
    ]
    
    for label, value in contact_details:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(label_x, y, label)
        c.setFont("Helvetica", 10)
        if label == "Address:" and value and len(value) > 50:
            lines = [value[i:i+50] for i in range(0, len(value), 50)]
            c.drawString(value_x, y, lines[0])
            for line in lines[1:]:
                y -= 15
                c.drawString(value_x, y, line)
        else:
            c.drawString(value_x, y, str(value))
        y -= 18
    
    y -= 15
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.2, 0.4, 0.6)
    c.drawString(left_margin, y, "FEE DETAILS")
    c.setFillColorRGB(0, 0, 0)
    
    y -= 25
    fee_per_month = student['fee_per_month'] or 0
    discount = student['discount'] or 0
    net_fee = fee_per_month - discount
    
    fee_details = [
        ("Fee Per Month:", f"Rs. {fee_per_month:.2f}"),
        ("Discount:", f"Rs. {discount:.2f}"),
        ("Net Fee:", f"Rs. {net_fee:.2f}"),
    ]
    
    for label, value in fee_details:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(label_x, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(value_x, y, str(value))
        y -= 18
    
    if student['other_details']:
        y -= 10
        c.setFont("Helvetica-Bold", 10)
        c.drawString(label_x, y, "Other Details:")
        y -= 15
        c.setFont("Helvetica", 9)
        other_text = student['other_details'][:200]
        c.drawString(label_x + 10, y, other_text)
    
    y = 100
    c.setStrokeColorRGB(0.2, 0.4, 0.6)
    c.setLineWidth(1)
    c.line(50, y, width - 50, y)
    
    y -= 20
    c.setFont("Helvetica", 9)
    c.drawString(50, y, f"Generated on: {datetime.now().strftime('%d-%m-%Y %H:%M')}")
    c.drawString(50, y - 15, "For Sansa Learn")
    
    signature_path = 'static/logo/signature.jpg'
    if os.path.exists(signature_path):
        try:
            sig_width = 120
            sig_height = 40
            c.drawImage(signature_path, width - 180, y - 45, width=sig_width, height=sig_height)
        except:
            pass
    
    c.drawRightString(width - 50, y - 55, "Authorized Signature")
    
    y -= 75
    c.setFont("Helvetica-Oblique", 10)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.drawCentredString(width/2, y, "Welcome to SANSA LEARN Family! We wish you success in your learning journey.")
    
    c.save()
    
    return send_file(filepath, as_attachment=True)

@app.route('/export/students')
@login_required
def export_students():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('SELECT * FROM students')
    students = cursor.fetchall()
    conn.close()
    
    filename = f"students_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            'ID', 'Admission Number', 'Name', 'Father Name', 'Mother Name',
            'DOB', 'Gender', 'Class', 'Board', 'Medium', 'School Name',
            'Address', 'Mobile 1', 'Mobile 2', 'Fee Per Month', 'Discount',
            'Admission Date', 'Other Details'
        ])
        for student in students:
            writer.writerow([
                student['id'], student['admission_number'], student['name'],
                student['father_name'], student['mother_name'], student['dob'],
                student['gender'], student['class'], student['board'],
                student['medium'], student['school_name'], student['address'],
                student['mobile1'], student['mobile2'], student['fee_per_month'],
                student['discount'], student['admission_date'], student['other_details']
            ])
    
    return send_file(filepath, as_attachment=True)

@app.route('/export/fees')
@login_required
def export_fees():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('''
        SELECT f.*, s.admission_number, s.name 
        FROM fees f 
        JOIN students s ON f.student_id = s.id
        ORDER BY f.year DESC, f.month DESC
    ''')
    fees = cursor.fetchall()
    conn.close()
    
    filename = f"fees_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            'Fee ID', 'Student Admission No', 'Student Name', 'Month', 'Year',
            'Fee Amount', 'Is Paid', 'Payment Date', 'Payment Mode', 'Remarks'
        ])
        for fee in fees:
            writer.writerow([
                fee['id'], fee['admission_number'], fee['name'],
                months[fee['month']], fee['year'], fee['fee_amount'],
                'Yes' if fee['is_paid'] else 'No', fee['payment_date'],
                fee['payment_mode'], fee['remarks']
            ])
    
    return send_file(filepath, as_attachment=True)

@app.route('/sessions')
@login_required
def manage_sessions():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('''
        SELECT * FROM manager_sessions 
        WHERE is_active = TRUE
        ORDER BY last_seen_at DESC
    ''')
    active_sessions = cursor.fetchall()
    
    cursor.execute('''
        SELECT * FROM manager_sessions 
        WHERE is_active = FALSE
        ORDER BY last_seen_at DESC
        LIMIT 10
    ''')
    inactive_sessions = cursor.fetchall()
    
    conn.close()
    
    current_session_id = session.get('session_record_id')
    
    return render_template('sessions.html', 
                          active_sessions=active_sessions,
                          inactive_sessions=inactive_sessions,
                          current_session_id=current_session_id)

@app.route('/sessions/revoke/<int:session_id>', methods=['POST'])
@login_required
def revoke_session(session_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute('SELECT session_id, is_active FROM manager_sessions WHERE id = %s', (session_id,))
    result = cursor.fetchone()
    
    if result:
        target_session_id = result['session_id']
        current_session_id = session.get('session_record_id')
        
        if result['is_active'] == 0:
            flash('Session is already logged out.', 'info')
        elif target_session_id == current_session_id:
            flash('You cannot revoke your own current session.', 'warning')
        else:
            cursor.execute('''
                UPDATE manager_sessions SET is_active = FALSE WHERE id = %s AND is_active = TRUE
            ''', (session_id,))
            if cursor.rowcount > 0:
                conn.commit()
                flash('Session has been revoked successfully. That device will be logged out.', 'success')
            else:
                flash('Could not revoke session.', 'error')
    else:
        flash('Session not found.', 'error')
    
    conn.close()
    return redirect(url_for('manage_sessions'))

@app.route('/sessions/revoke-all', methods=['POST'])
@login_required
def revoke_all_sessions():
    current_session_id = session.get('session_record_id')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE manager_sessions SET is_active = FALSE 
        WHERE session_id != %s AND is_active = TRUE
    ''', (current_session_id,))
    revoked_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    flash(f'{revoked_count} session(s) have been revoked. All other devices will be logged out.', 'success')
    return redirect(url_for('manage_sessions'))

@app.route('/sessions/cleanup', methods=['POST'])
@login_required
def cleanup_sessions():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM manager_sessions 
        WHERE is_active = FALSE AND last_seen_at < NOW() - INTERVAL '30 days'
    ''')
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    flash(f'{deleted_count} old session(s) have been cleaned up.', 'success')
    return redirect(url_for('manage_sessions'))

@app.route('/backup')
@login_required
def backup_page():
    backup_files = []
    if os.path.exists(BACKUP_FOLDER):
        for f in os.listdir(BACKUP_FOLDER):
            if f.endswith('.zip'):
                filepath = os.path.join(BACKUP_FOLDER, f)
                size = os.path.getsize(filepath)
                modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                backup_files.append({
                    'name': f,
                    'size': f"{size / 1024:.1f} KB" if size < 1024*1024 else f"{size / (1024*1024):.1f} MB",
                    'date': modified.strftime('%d-%m-%Y %H:%M')
                })
    backup_files.sort(key=lambda x: x['date'], reverse=True)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM students')
    total_students = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM fees')
    total_fees = cursor.fetchone()[0]
    conn.close()
    
    return render_template('backup.html', 
                          backup_files=backup_files,
                          total_students=total_students,
                          total_fees=total_fees)

@app.route('/backup/create', methods=['POST'])
@login_required
def create_backup():
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"sansa_learn_backup_{timestamp}"
        backup_dir = os.path.join(BACKUP_FOLDER, backup_name)
        os.makedirs(backup_dir, exist_ok=True)
        
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        cursor.execute('SELECT * FROM students')
        students = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('SELECT * FROM fees')
        fees = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('SELECT * FROM institute_info')
        institute_info = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('SELECT * FROM manager_sessions')
        sessions_data = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        backup_data = {
            'backup_date': datetime.now().isoformat(),
            'backup_version': '1.0',
            'students': students,
            'fees': fees,
            'institute_info': institute_info,
            'manager_sessions': sessions_data,
            'statistics': {
                'total_students': len(students),
                'total_fees': len(fees)
            }
        }
        
        json_path = os.path.join(backup_dir, 'data.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False, default=str)
        
        uploads_backup_dir = os.path.join(backup_dir, 'uploads')
        if os.path.exists(UPLOAD_FOLDER) and os.listdir(UPLOAD_FOLDER):
            shutil.copytree(UPLOAD_FOLDER, uploads_backup_dir)
        else:
            os.makedirs(uploads_backup_dir, exist_ok=True)
        
        logo_backup_dir = os.path.join(backup_dir, 'logo')
        if os.path.exists('static/logo'):
            shutil.copytree('static/logo', logo_backup_dir)
        
        zip_path = os.path.join(BACKUP_FOLDER, f"{backup_name}.zip")
        shutil.make_archive(os.path.join(BACKUP_FOLDER, backup_name), 'zip', backup_dir)
        
        shutil.rmtree(backup_dir)
        
        flash(f'Backup created successfully! File: {backup_name}.zip', 'success')
        
    except Exception as e:
        flash(f'Error creating backup: {str(e)}', 'error')
    
    return redirect(url_for('backup_page'))

@app.route('/backup/download/<filename>')
@login_required
def download_backup(filename):
    if not filename.endswith('.zip'):
        flash('Invalid backup file.', 'error')
        return redirect(url_for('backup_page'))
    
    filepath = os.path.join(BACKUP_FOLDER, secure_filename(filename))
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        flash('Backup file not found.', 'error')
        return redirect(url_for('backup_page'))

@app.route('/backup/delete/<filename>', methods=['POST'])
@login_required
def delete_backup(filename):
    if not filename.endswith('.zip'):
        flash('Invalid backup file.', 'error')
        return redirect(url_for('backup_page'))
    
    filepath = os.path.join(BACKUP_FOLDER, secure_filename(filename))
    if os.path.exists(filepath):
        os.remove(filepath)
        flash(f'Backup {filename} deleted successfully.', 'success')
    else:
        flash('Backup file not found.', 'error')
    
    return redirect(url_for('backup_page'))

@app.route('/backup/restore', methods=['POST'])
@login_required
def restore_backup():
    if 'backup_file' not in request.files:
        flash('No backup file uploaded.', 'error')
        return redirect(url_for('backup_page'))
    
    file = request.files['backup_file']
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('backup_page'))
    
    if not file.filename.endswith('.zip'):
        flash('Please upload a valid backup ZIP file.', 'error')
        return redirect(url_for('backup_page'))
    
    try:
        temp_dir = os.path.join(BACKUP_FOLDER, 'temp_restore')
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)
        
        zip_path = os.path.join(temp_dir, 'backup.zip')
        file.save(zip_path)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        json_path = None
        for root, dirs, files in os.walk(temp_dir):
            if 'data.json' in files:
                json_path = os.path.join(root, 'data.json')
                restore_root = root
                break
        
        if not json_path:
            raise Exception('Invalid backup file: data.json not found')
        
        with open(json_path, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM fees')
        cursor.execute('DELETE FROM students')
        cursor.execute('DELETE FROM manager_sessions')
        
        for student in backup_data.get('students', []):
            cursor.execute('''
                INSERT INTO students (id, admission_number, photo_path, name, father_name, mother_name,
                    dob, gender, class, board, medium, school_name, address, mobile1, mobile2,
                    fee_per_month, discount, admission_date, other_details, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                student.get('id'), student.get('admission_number'), student.get('photo_path'),
                student.get('name'), student.get('father_name'), student.get('mother_name'),
                student.get('dob'), student.get('gender'), student.get('class'),
                student.get('board'), student.get('medium'), student.get('school_name'),
                student.get('address'), student.get('mobile1'), student.get('mobile2'),
                student.get('fee_per_month'), student.get('discount'), student.get('admission_date'),
                student.get('other_details'), student.get('created_at')
            ))
        
        for fee in backup_data.get('fees', []):
            cursor.execute('''
                INSERT INTO fees (id, student_id, month, year, fee_amount, is_paid,
                    payment_date, payment_mode, remarks, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                fee.get('id'), fee.get('student_id'), fee.get('month'), fee.get('year'),
                fee.get('fee_amount'), fee.get('is_paid'), fee.get('payment_date'),
                fee.get('payment_mode'), fee.get('remarks'), fee.get('created_at')
            ))
        
        for sess in backup_data.get('manager_sessions', []):
            cursor.execute('''
                INSERT INTO manager_sessions (id, session_id, ip_address, user_agent,
                    device_name, os, browser, is_active, created_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                sess.get('id'), sess.get('session_id'), sess.get('ip_address'),
                sess.get('user_agent'), sess.get('device_name'), sess.get('os'),
                sess.get('browser'), sess.get('is_active'), sess.get('created_at'),
                sess.get('last_seen_at')
            ))
        
        cursor.execute("SELECT setval('students_id_seq', COALESCE((SELECT MAX(id) FROM students), 1))")
        cursor.execute("SELECT setval('fees_id_seq', COALESCE((SELECT MAX(id) FROM fees), 1))")
        cursor.execute("SELECT setval('manager_sessions_id_seq', COALESCE((SELECT MAX(id) FROM manager_sessions), 1))")
        
        conn.commit()
        conn.close()
        
        uploads_backup = os.path.join(restore_root, 'uploads')
        if os.path.exists(uploads_backup):
            if os.path.exists(UPLOAD_FOLDER):
                shutil.rmtree(UPLOAD_FOLDER)
            shutil.copytree(uploads_backup, UPLOAD_FOLDER)
        
        logo_backup = os.path.join(restore_root, 'logo')
        if os.path.exists(logo_backup):
            if os.path.exists('static/logo'):
                shutil.rmtree('static/logo')
            shutil.copytree(logo_backup, 'static/logo')
        
        shutil.rmtree(temp_dir)
        
        stats = backup_data.get('statistics', {})
        flash(f'Backup restored successfully! Restored {stats.get("total_students", 0)} students and {stats.get("total_fees", 0)} fee records.', 'success')
        
    except Exception as e:
        flash(f'Error restoring backup: {str(e)}', 'error')
    
    return redirect(url_for('backup_page'))
