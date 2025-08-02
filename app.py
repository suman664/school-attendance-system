from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import qrcode
from PIL import Image
import io
import base64
import uuid
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'

# Database setup
def get_db_connection():
    conn = sqlite3.connect('attendance.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS schools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            principal TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            employee_code TEXT UNIQUE NOT NULL,
            school_id INTEGER,
            qr_code_data TEXT,
            FOREIGN KEY (school_id) REFERENCES schools (id)
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            date TEXT NOT NULL,
            check_in_time TEXT,
            check_out_time TEXT,
            status TEXT,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Generate QR code as base64
def generate_qr_base64(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    img_str = base64.b64encode(buffer.getvalue()).decode()
    return img_str

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        school_name = request.form['school_name']
        password = request.form['password']
        
        try:
            conn = get_db_connection()
            conn.execute("INSERT INTO schools (name, principal, email, password) VALUES (?, ?, ?, ?)",
                        (school_name, name, email, password))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error="Email already registered")
        except Exception as e:
            return render_template('register.html', error="Registration failed")
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM schools WHERE email=? AND password=?", 
                           (email, password)).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['user_name'] = user['principal']
            session['school_name'] = user['name']
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid credentials")
    
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Get employee count
    employee_count = conn.execute("SELECT COUNT(*) FROM employees WHERE school_id=?", 
                                 (session['user_id'],)).fetchone()[0]
    
    # Get today's attendance
    today = datetime.now().strftime("%Y-%m-%d")
    present_today = conn.execute("""
        SELECT COUNT(*) FROM attendance a 
        JOIN employees e ON a.employee_id = e.id 
        WHERE e.school_id=? AND a.date=?
    """, (session['user_id'], today)).fetchone()[0]
    
    conn.close()
    
    return render_template('dashboard.html', 
                         employee_count=employee_count, 
                         present_today=present_today,
                         user_name=session['user_name'],
                         school_name=session['school_name'])

@app.route('/employees')
def employees():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    employees = conn.execute("SELECT * FROM employees WHERE school_id=? ORDER BY name", 
                            (session['user_id'],)).fetchall()
    conn.close()
    
    return render_template('employees.html', employees=employees)

@app.route('/add_employee', methods=['POST'])
def add_employee():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    name = request.form['name']
    employee_code = str(uuid.uuid4())[:8].upper()
    
    conn = get_db_connection()
    
    # Insert employee
    cursor = conn.execute("INSERT INTO employees (name, employee_code, school_id) VALUES (?, ?, ?)",
                         (name, employee_code, session['user_id']))
    employee_id = cursor.lastrowid
    conn.commit()
    
    # Generate QR code data
    qr_data = f"employee:{employee_id}:{employee_code}"
    
    # Update employee with QR data
    conn.execute("UPDATE employees SET qr_code_data=? WHERE id=?", (qr_data, employee_id))
    conn.commit()
    conn.close()
    
    # Generate QR code for response
    qr_base64 = generate_qr_base64(qr_data)
    
    return jsonify({
        'success': True,
        'employee_code': employee_code,
        'qr_code': qr_base64
    })

@app.route('/get_employee_qr/<int:employee_id>')
def get_employee_qr(employee_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    result = conn.execute("SELECT qr_code_data, name FROM employees WHERE id=? AND school_id=?", 
                         (employee_id, session['user_id'])).fetchone()
    conn.close()
    
    if result:
        qr_base64 = generate_qr_base64(result['qr_code_data'])
        return jsonify({
            'qr_code': qr_base64,
            'employee_name': result['name']
        })
    else:
        return jsonify({'error': 'Employee not found'}), 404

@app.route('/scanner')
def scanner():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    return render_template('scanner.html')

@app.route('/process_qr_scan', methods=['POST'])
def process_qr_scan():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    qr_data = data.get('qr_data', '')
    
    if not qr_data.startswith('employee:'):
        return jsonify({'error': 'Invalid QR code'}), 400
    
    try:
        # Parse QR  employee:id:code
        parts = qr_data.split(':')
        employee_id = int(parts[1])
        employee_code = parts[2]
        
        conn = get_db_connection()
        
        # Verify employee belongs to this school
        employee = conn.execute("SELECT name FROM employees WHERE id=? AND employee_code=? AND school_id=?", 
                               (employee_id, employee_code, session['user_id'])).fetchone()
        
        if not employee:
            conn.close()
            return jsonify({'error': 'Employee not found'}), 404
        
        employee_name = employee['name']
        today = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%H:%M:%S")
        
        # Check if attendance record exists for today
        existing_record = conn.execute("SELECT * FROM attendance WHERE employee_id=? AND date=?", 
                                      (employee_id, today)).fetchone()
        
        if not existing_record:
            # First scan of the day - check-in
            conn.execute("INSERT INTO attendance (employee_id, date, check_in_time, status) VALUES (?, ?, ?, ?)",
                        (employee_id, today, current_time, "Present"))
            conn.commit()
            message = f"✅ {employee_name} checked in at {current_time}"
        elif not existing_record['check_out_time']:  # check_out_time is None
            # Second scan - check-out
            conn.execute("UPDATE attendance SET check_out_time=? WHERE id=?",
                        (current_time, existing_record['id']))
            conn.commit()
            message = f"✅ {employee_name} checked out at {current_time}"
        else:
            # Already checked out
            message = f"⚠️ {employee_name} has already checked out today"
        
        conn.close()
        
        return jsonify({
            'success': True,
            'message': message
        })
        
    except Exception as e:
        return jsonify({'error': 'Invalid QR code format'}), 400

@app.route('/reports')
def reports():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    records = conn.execute("""
        SELECT a.date, e.name, a.check_in_time, a.check_out_time, 
               CASE WHEN a.check_in_time IS NOT NULL THEN 'Present' ELSE 'Absent' END as status
        FROM attendance a
        JOIN employees e ON a.employee_id = e.id
        WHERE e.school_id=? 
        ORDER BY a.date DESC, e.name
    """, (session['user_id'],)).fetchall()
    
    conn.close()
    
    return render_template('reports.html', records=records)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# Initialize database when app starts
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=False)