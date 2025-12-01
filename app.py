import os
import sqlite3
import csv
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
import io

app = Flask(__name__)

# Security: Require SESSION_SECRET from environment
if not os.environ.get('SESSION_SECRET'):
    raise RuntimeError("SESSION_SECRET environment variable must be set for security. Please add it to Replit Secrets.")
app.secret_key = os.environ.get('SESSION_SECRET')

# Configuration
UPLOAD_FOLDER = 'uploads'
PDF_FOLDER = 'pdfs'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
DATABASE = 'database.db'

# Ensure folders exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs('static/logo', exist_ok=True)

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    """Connect to the database"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database with tables"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Students table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    
    # Fees table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    
    # Institute info table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS institute_info (
            id INTEGER PRIMARY KEY,
            logo_path TEXT,
            address TEXT,
            contact TEXT,
            signature_path TEXT
        )
    ''')
    
    # Insert default institute info if not exists
    cursor.execute('SELECT COUNT(*) FROM institute_info')
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO institute_info (id, address, contact) 
            VALUES (1, 'Chandmari Road Kankarbagh gali no. 06 ke thik saamne', '9296820840, 9153021229')
        ''')
    
    conn.commit()
    conn.close()

# Initialize database when module loads
init_db()

def generate_admission_number():
    """Generate unique admission number"""
    conn = get_db()
    cursor = conn.cursor()
    year = datetime.now().year
    cursor.execute('SELECT COUNT(*) FROM students WHERE admission_number LIKE ?', (f'SL{year}%',))
    count = cursor.fetchone()[0]
    conn.close()
    return f'SL{year}{str(count + 1).zfill(4)}'

def ensure_fee_records(student_id, admission_date, fee_per_month, discount=0.0):
    """Auto-generate fee records from admission date to current month"""
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
            SELECT id FROM fees WHERE student_id = ? AND month = ? AND year = ?
        ''', (student_id, month, year))
        
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO fees (student_id, month, year, fee_amount, is_paid)
                VALUES (?, ?, ?, ?, 0)
            ''', (student_id, month, year, net_fee))
        
        temp_dt = temp_dt.replace(day=1) + relativedelta(months=1)
    
    conn.commit()
    conn.close()

def get_unpaid_months_details(student_id):
    """Get details of all unpaid months for a student"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT month, year, fee_amount FROM fees 
        WHERE student_id = ? AND is_paid = 0
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
    """Build WhatsApp URL with encoded message"""
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if session.get('authenticated'):
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        admin_password = os.environ.get('MANAGER_PASSWORD', '')
        
        if not admin_password:
            flash('System configuration error. Please contact administrator.', 'error')
            return render_template('login.html')
        
        if password == admin_password:
            session['authenticated'] = True
            session.permanent = True
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Incorrect password. Please try again.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    """Main dashboard"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Get statistics
    cursor.execute('SELECT COUNT(*) FROM students')
    total_students = cursor.fetchone()[0]
    
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    cursor.execute('''
        SELECT COALESCE(SUM(fee_amount), 0) FROM fees 
        WHERE month = ? AND year = ? AND is_paid = 1
    ''', (current_month, current_year))
    total_paid_this_month = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COALESCE(SUM(f.fee_amount), 0) FROM fees f
        JOIN students s ON f.student_id = s.id
        WHERE f.month = ? AND f.year = ? AND f.is_paid = 0
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
    """Serve uploaded student photos"""
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/students')
@login_required
def list_students():
    """List all students"""
    search_query = request.args.get('search', '')
    search_type = request.args.get('search_type', 'name')
    
    conn = get_db()
    cursor = conn.cursor()
    
    if search_query:
        if search_type == 'admission':
            cursor.execute('SELECT * FROM students WHERE admission_number LIKE ? ORDER BY created_at DESC', 
                         (f'%{search_query}%',))
        elif search_type == 'father':
            cursor.execute('SELECT * FROM students WHERE father_name LIKE ? ORDER BY created_at DESC', 
                         (f'%{search_query}%',))
        else:  # name
            cursor.execute('SELECT * FROM students WHERE name LIKE ? ORDER BY created_at DESC', 
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
    """Add new student"""
    if request.method == 'POST':
        try:
            # Generate admission number
            admission_number = generate_admission_number()
            
            # Handle photo upload
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            
            student_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            ensure_fee_records(student_id, request.form.get('admission_date', datetime.now().strftime('%Y-%m-%d')),
                             float(request.form.get('fee_per_month', 0)),
                             float(request.form.get('discount', 0)))
            
            flash(f'Student added successfully! Admission Number: {admission_number}', 'success')
            return redirect(url_for('list_students'))
            
        except Exception as e:
            flash(f'Error adding student: {str(e)}', 'error')
            return redirect(url_for('add_student'))
    
    return render_template('add_student.html')

@app.route('/student/<int:student_id>')
@login_required
def view_student(student_id):
    """View student profile"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM students WHERE id = ?', (student_id,))
    student = cursor.fetchone()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('list_students'))
    
    ensure_fee_records(student_id, student['admission_date'], 
                      student['fee_per_month'] or 0, student['discount'] or 0)
    
    cursor.execute('''
        SELECT * FROM fees WHERE student_id = ? ORDER BY year, month
    ''', (student_id,))
    all_fee_records = cursor.fetchall()
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    unpaid_list, total_due = get_unpaid_months_details(student_id)
    
    demand_bill_url = url_for('generate_demand_bill', student_id=student_id, _external=True)
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
    """Edit student details"""
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        try:
            # Handle photo upload
            cursor.execute('SELECT photo_path FROM students WHERE id = ?', (student_id,))
            current_photo = cursor.fetchone()[0]
            photo_path = current_photo
            
            if 'photo' in request.files:
                file = request.files['photo']
                if file and file.filename and allowed_file(file.filename):
                    cursor.execute('SELECT admission_number FROM students WHERE id = ?', (student_id,))
                    admission_number = cursor.fetchone()[0]
                    filename = secure_filename(f"{admission_number}_{file.filename}")
                    filepath = os.path.join(UPLOAD_FOLDER, filename)
                    file.save(filepath)
                    photo_path = filepath
            
            cursor.execute('''
                UPDATE students SET
                    photo_path = ?, name = ?, father_name = ?, mother_name = ?,
                    dob = ?, gender = ?, class = ?, board = ?, medium = ?,
                    school_name = ?, address = ?, mobile1 = ?, mobile2 = ?,
                    fee_per_month = ?, discount = ?, admission_date = ?, other_details = ?
                WHERE id = ?
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
    
    cursor.execute('SELECT * FROM students WHERE id = ?', (student_id,))
    student = cursor.fetchone()
    conn.close()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('list_students'))
    
    return render_template('edit_student.html', student=student)

@app.route('/student/<int:student_id>/delete', methods=['POST'])
@login_required
def delete_student(student_id):
    """Delete student"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Get student photo to delete file
        cursor.execute('SELECT photo_path FROM students WHERE id = ?', (student_id,))
        result = cursor.fetchone()
        if result and result[0] and os.path.exists(result[0]):
            os.remove(result[0])
        
        cursor.execute('DELETE FROM students WHERE id = ?', (student_id,))
        conn.commit()
        conn.close()
        
        flash('Student deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting student: {str(e)}', 'error')
    
    return redirect(url_for('list_students'))

@app.route('/fees')
@login_required
def fee_management():
    """Fee management page"""
    conn = get_db()
    cursor = conn.cursor()
    
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
    """Manage fees for a specific student"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM students WHERE id = ?', (student_id,))
    student = cursor.fetchone()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('fee_management'))
    
    # Get all fee records
    cursor.execute('''
        SELECT * FROM fees WHERE student_id = ? ORDER BY year DESC, month DESC
    ''', (student_id,))
    fee_records = cursor.fetchall()
    
    # Calculate statistics
    cursor.execute('''
        SELECT COALESCE(SUM(fee_amount), 0) FROM fees 
        WHERE student_id = ? AND is_paid = 1
    ''', (student_id,))
    total_paid = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COALESCE(SUM(fee_amount), 0) FROM fees 
        WHERE student_id = ? AND is_paid = 0
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
    """Add or mark fee as paid"""
    try:
        month = int(request.form['month'])
        year = int(request.form['year'])
        fee_amount = float(request.form['fee_amount'])
        is_paid = 1 if request.form.get('is_paid') == 'on' else 0
        payment_date = request.form.get('payment_date', '')
        payment_mode = request.form.get('payment_mode', '')
        remarks = request.form.get('remarks', '')
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Check if record exists
        cursor.execute('''
            SELECT id FROM fees WHERE student_id = ? AND month = ? AND year = ?
        ''', (student_id, month, year))
        existing = cursor.fetchone()
        
        if existing:
            # Update existing record
            cursor.execute('''
                UPDATE fees SET fee_amount = ?, is_paid = ?, payment_date = ?,
                payment_mode = ?, remarks = ? WHERE id = ?
            ''', (fee_amount, is_paid, payment_date, payment_mode, remarks, existing[0]))
        else:
            # Insert new record
            cursor.execute('''
                INSERT INTO fees (student_id, month, year, fee_amount, is_paid, 
                                payment_date, payment_mode, remarks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    """Delete fee record"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Get student_id before deleting
        cursor.execute('SELECT student_id FROM fees WHERE id = ?', (fee_id,))
        result = cursor.fetchone()
        student_id = result[0] if result else None
        
        cursor.execute('DELETE FROM fees WHERE id = ?', (fee_id,))
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
    """Toggle fee payment status"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, is_paid FROM fees WHERE student_id = ? AND month = ? AND year = ?
        ''', (student_id, month, year))
        
        fee_record = cursor.fetchone()
        
        if fee_record:
            new_status = 0 if fee_record['is_paid'] else 1
            payment_date = datetime.now().strftime('%Y-%m-%d') if new_status else None
            
            cursor.execute('''
                UPDATE fees SET is_paid = ?, payment_date = ?, payment_mode = ?
                WHERE id = ?
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
    """Display students in month-wise grid view"""
    year = request.args.get('year', datetime.now().year, type=int)
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM students ORDER BY admission_number')
    students = cursor.fetchall()
    
    student_fees = {}
    for student in students:
        cursor.execute('''
            SELECT month, is_paid FROM fees 
            WHERE student_id = ? AND year = ?
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
    """Generate PDF receipt for a payment"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM students WHERE id = ?', (student_id,))
    student = cursor.fetchone()
    
    cursor.execute('SELECT * FROM fees WHERE id = ?', (fee_id,))
    fee = cursor.fetchone()
    
    cursor.execute('SELECT * FROM institute_info WHERE id = 1')
    institute = cursor.fetchone()
    
    conn.close()
    
    if not student or not fee:
        flash('Student or fee record not found', 'error')
        return redirect(url_for('fee_management'))
    
    # Generate PDF
    filename = f"receipt_{student['admission_number']}_{fee['month']}_{fee['year']}.pdf"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    
    # Add Logo
    logo_path = 'static/logo/logo.png'
    if os.path.exists(logo_path):
        try:
            logo_width = 80
            logo_height = 80
            c.drawImage(logo_path, (width - logo_width) / 2, height - 90, 
                       width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
        except:
            pass
    
    # Header
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height - 110, "SANSA LEARN")
    
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height - 130, institute['address'])
    c.drawCentredString(width/2, height - 145, f"Contact: {institute['contact']}")
    
    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 175, "FEE RECEIPT")
    
    # Receipt details
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
    
    # Student details
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
    
    # Payment details
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
    
    # Footer
    y = 150
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "For Sansa Learn")
    
    # Add Signature Image
    signature_path = 'static/logo/signature.jpg'
    if os.path.exists(signature_path):
        try:
            sig_width = 100
            sig_height = 40
            c.drawImage(signature_path, 50, y - 55, width=sig_width, height=sig_height, 
                       preserveAspectRatio=True)
        except:
            pass
    
    y -= 65
    c.drawString(50, y, "Management Signature")
    
    c.save()
    
    return send_file(filepath, as_attachment=True)

@app.route('/student/<int:student_id>/demand')
@login_required
def generate_demand_bill(student_id):
    """Generate PDF demand bill for unpaid fees"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM students WHERE id = ?', (student_id,))
    student = cursor.fetchone()
    
    cursor.execute('''
        SELECT * FROM fees WHERE student_id = ? AND is_paid = 0 
        ORDER BY year, month
    ''', (student_id,))
    unpaid_fees = cursor.fetchall()
    
    cursor.execute('SELECT * FROM institute_info WHERE id = 1')
    institute = cursor.fetchone()
    
    conn.close()
    
    if not student:
        flash('Student not found', 'error')
        return redirect(url_for('fee_management'))
    
    # Generate PDF
    filename = f"demand_{student['admission_number']}_{datetime.now().strftime('%Y%m%d')}.pdf"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    
    # Add Logo
    logo_path = 'static/logo/logo.png'
    if os.path.exists(logo_path):
        try:
            logo_width = 80
            logo_height = 80
            c.drawImage(logo_path, (width - logo_width) / 2, height - 90, 
                       width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
        except:
            pass
    
    # Header
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height - 110, "SANSA LEARN")
    
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height - 130, institute['address'])
    c.drawCentredString(width/2, height - 145, f"Contact: {institute['contact']}")
    
    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 175, "FEE DEMAND NOTICE")
    
    y = height - 215
    c.setFont("Helvetica", 11)
    c.drawRightString(width - 50, y, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    
    y -= 30
    c.line(50, y, width - 50, y)
    y -= 25
    
    # Student details
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
    
    # Unpaid fees details
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Pending Fee Details:")
    y -= 25
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    # Table header
    c.setFont("Helvetica-Bold", 10)
    c.drawString(70, y, "Month")
    c.drawString(200, y, "Year")
    c.drawRightString(width - 70, y, "Amount (Rs.)")
    y -= 18
    c.line(70, y, width - 50, y)
    y -= 5
    
    # Table rows
    c.setFont("Helvetica", 10)
    total_pending = 0
    for fee in unpaid_fees:
        y -= 15
        if y < 150:  # Page break if needed
            c.showPage()
            y = height - 50
        
        c.drawString(70, y, months[fee['month']])
        c.drawString(200, y, str(fee['year']))
        c.drawRightString(width - 70, y, f"{fee['fee_amount']:.2f}")
        total_pending += fee['fee_amount']
    
    y -= 20
    c.line(70, y, width - 50, y)
    y -= 20
    
    # Total
    c.setFont("Helvetica-Bold", 11)
    c.drawString(70, y, "Total Pending:")
    c.drawRightString(width - 70, y, f"Rs. {total_pending:.2f}")
    
    y -= 40
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "Kindly clear the above pending fees at the earliest.")
    
    # Footer
    y = 150
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "For Sansa Learn")
    
    # Add Signature Image
    signature_path = 'static/logo/signature.jpg'
    if os.path.exists(signature_path):
        try:
            sig_width = 100
            sig_height = 40
            c.drawImage(signature_path, 50, y - 55, width=sig_width, height=sig_height, 
                       preserveAspectRatio=True)
        except:
            pass
    
    y -= 65
    c.drawString(50, y, "Management Signature")
    
    c.save()
    
    return send_file(filepath, as_attachment=True)

@app.route('/export/students')
@login_required
def export_students():
    """Export all students to CSV"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students')
    students = cursor.fetchall()
    conn.close()
    
    # Create CSV
    filename = f"students_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        # Header
        writer.writerow([
            'ID', 'Admission Number', 'Name', 'Father Name', 'Mother Name',
            'DOB', 'Gender', 'Class', 'Board', 'Medium', 'School Name',
            'Address', 'Mobile 1', 'Mobile 2', 'Fee Per Month', 'Discount',
            'Admission Date', 'Other Details'
        ])
        # Data
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
    """Export all fees to CSV"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT f.*, s.admission_number, s.name 
        FROM fees f 
        JOIN students s ON f.student_id = s.id
        ORDER BY f.year DESC, f.month DESC
    ''')
    fees = cursor.fetchall()
    conn.close()
    
    # Create CSV
    filename = f"fees_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(PDF_FOLDER, filename)
    
    months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    
    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        # Header
        writer.writerow([
            'Fee ID', 'Student Admission No', 'Student Name', 'Month', 'Year',
            'Fee Amount', 'Is Paid', 'Payment Date', 'Payment Mode', 'Remarks'
        ])
        # Data
        for fee in fees:
            writer.writerow([
                fee['id'], fee['admission_number'], fee['name'],
                months[fee['month']], fee['year'], fee['fee_amount'],
                'Yes' if fee['is_paid'] else 'No', fee['payment_date'],
                fee['payment_mode'], fee['remarks']
            ])
    
    return send_file(filepath, as_attachment=True)

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
