import math
from flask import Flask, render_template, redirect, url_for, flash, request, make_response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Student, Subject, Result, DownloadLog, BackPaper
from config import Config
from flask_wtf.csrf import CSRFProtect
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from datetime import datetime
import pandas as pd
from werkzeug.utils import secure_filename
import os
import io

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

BRANCHES = [
    "Information Technology",
    "Food Technology",
    "Agriculture Engineering",
    "Civil Engineering"
]

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def calculate_grade(total, max_marks):
    if max_marks == 0:
        return '-'
    percentage = (total / max_marks) * 100
    if percentage >= 90: return 'A+'
    elif percentage >= 80: return 'A'
    elif percentage >= 70: return 'B'
    elif percentage >= 60: return 'C'
    elif percentage >= 50: return 'D'
    else: return 'F'

def subject_max_total(subject):
    if subject.is_single_mark:
        return subject.single_max
    return subject.ese_max + subject.cfa_max

def calculate_point_scale(total, max_marks):
    if max_marks == 0:
        return 0
    value = (total / max_marks) * 10
    return math.floor(value * 10 + 0.5) / 10

def ensure_special_subjects(semester, branch):
    """Auto-create special subjects for Sem 7 and 8 per branch."""
    special_subjects = {
        7: [("Industrial Training", 3), ("Open Seminar Presentation", 2), ("Minor Project", 2)],
        8: [("Major Project", 6), ("Tech Seminar Presentation", 2)]
    }
    if semester not in special_subjects:
        return []

    created_or_existing = []
    for subj_name, subj_credit in special_subjects[semester]:
        existing = Subject.query.filter_by(
            name=subj_name, semester=semester, branch=branch).first()
        if not existing:
            new_subject = Subject(
                name=subj_name, semester=semester, branch=branch,
                credits=subj_credit, is_single_mark=True, single_max=100,
                ese_max=0, cfa_max=0)
            db.session.add(new_subject)
            db.session.flush()
            created_or_existing.append(new_subject)
        else:
            created_or_existing.append(existing)
    return created_or_existing

# ─── AUTH ──────────────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin':
        students = Student.query.all()
        total_students = len(students)
        total_subjects = Subject.query.count()
        recent_downloads = DownloadLog.query.order_by(
            DownloadLog.downloaded_at.desc()).limit(10).all()
        return render_template('admin/dashboard.html',
                               students=students,
                               total_students=total_students,
                               total_subjects=total_subjects,
                               recent_downloads=recent_downloads,
                               branches=BRANCHES)
    else:
        student = Student.query.filter_by(user_id=current_user.id).first()
        results = Result.query.join(Subject).filter(
            Result.student_id == student.id,
            Subject.semester == student.semester,
            Subject.branch == student.branch
        ).all()
        back_papers = BackPaper.query.filter_by(student_id=student.id).order_by(
            BackPaper.due_semester, BackPaper.attempt_number).all()
        return render_template('student/result.html', student=student,
                               results=results, back_papers=back_papers)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ─── STATS PAGES ───────────────────────────────────────────

@app.route('/admin/all-semesters')
@login_required
def all_semesters():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    sem_data = []
    for i in range(1, 9):
        count = Student.query.filter_by(semester=i).count()
        sub_count = Subject.query.filter_by(semester=i).count()
        sem_data.append({'sem': i, 'students': count, 'subjects': sub_count})
    return render_template('admin/all_semesters.html', sem_data=sem_data)

@app.route('/admin/active-records')
@login_required
def active_records():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    students = Student.query.all()
    records = []
    for student in students:
        result_count = Result.query.filter_by(student_id=student.id).count()
        records.append({'student': student, 'result_count': result_count})
    return render_template('admin/active_records.html', records=records)

@app.route('/admin/download-history')
@login_required
def download_history():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    downloads = DownloadLog.query.order_by(DownloadLog.downloaded_at.desc()).all()
    return render_template('admin/download_history.html', downloads=downloads)

# ─── SUBJECTS ──────────────────────────────────────────────

@app.route('/admin/subjects')
@login_required
def manage_subjects():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    selected_branch = request.args.get('branch', BRANCHES[0])
    selected_sem = request.args.get('sem', type=int)

    query = Subject.query.filter_by(branch=selected_branch)
    if selected_sem:
        query = query.filter_by(semester=selected_sem)
    subjects = query.all()

    return render_template('admin/manage_subjects.html',
                           subjects=subjects,
                           selected_branch=selected_branch,
                           selected_sem=selected_sem,
                           branches=BRANCHES)

@app.route('/admin/add-subject', methods=['GET', 'POST'])
@login_required
def add_subject():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    selected_branch = request.args.get('branch', BRANCHES[0])

    if request.method == 'POST':
        name = request.form.get('name')
        semester = int(request.form.get('semester'))
        branch = request.form.get('branch', BRANCHES[0])
        credits = int(request.form.get('credits', 4))
        is_single_mark = request.form.get('is_single_mark') == 'on'

        if is_single_mark:
            single_max = int(request.form.get('single_max', 100))
            subject = Subject(name=name, semester=semester, branch=branch,
                              credits=credits, is_single_mark=True,
                              single_max=single_max, ese_max=0, cfa_max=0)
        else:
            ese_max = int(request.form.get('ese_max', 80))
            cfa_max = int(request.form.get('cfa_max', 20))
            subject = Subject(name=name, semester=semester, branch=branch,
                              credits=credits, ese_max=ese_max, cfa_max=cfa_max,
                              is_single_mark=False)

        db.session.add(subject)
        db.session.flush()

        students_in_semester = Student.query.filter_by(
            semester=semester, branch=branch).all()
        for student in students_in_semester:
            if not Result.query.filter_by(
                    student_id=student.id, subject_id=subject.id).first():
                db.session.add(Result(ese_marks=0, cfa_marks=0, lab_marks=0,
                                      total_marks=0, grade='-',
                                      student_id=student.id, subject_id=subject.id))
        db.session.commit()
        flash(f'Subject "{name}" added to {branch}! Auto-assigned to {len(students_in_semester)} student(s).', 'success')
        return redirect(url_for('manage_subjects', branch=branch))

    return render_template('admin/add_subject.html',
                           branches=BRANCHES,
                           selected_branch=selected_branch)

@app.route('/admin/edit-subject/<int:subject_id>', methods=['GET', 'POST'])
@login_required
def edit_subject(subject_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    subject = Subject.query.get_or_404(subject_id)
    if request.method == 'POST':
        subject.name = request.form.get('name')
        subject.semester = int(request.form.get('semester'))
        subject.branch = request.form.get('branch', subject.branch)
        subject.credits = int(request.form.get('credits', 4))
        subject.is_single_mark = request.form.get('is_single_mark') == 'on'

        if subject.is_single_mark:
            subject.single_max = int(request.form.get('single_max', 100))
            subject.ese_max = 0
            subject.cfa_max = 0
        else:
            subject.ese_max = int(request.form.get('ese_max', 80))
            subject.cfa_max = int(request.form.get('cfa_max', 20))

        results = Result.query.filter_by(subject_id=subject.id).all()
        for result in results:
            max_total = subject_max_total(subject)
            if subject.is_single_mark:
                result.total_marks = result.ese_marks
            else:
                result.total_marks = result.ese_marks + result.cfa_marks
            result.grade = calculate_grade(result.total_marks, max_total)
        db.session.commit()
        flash(f'Subject updated! {len(results)} result(s) recalculated.', 'success')
        return redirect(url_for('manage_subjects', branch=subject.branch))
    return render_template('admin/edit_subject.html',
                           subject=subject, branches=BRANCHES)

@app.route('/admin/delete-subject/<int:subject_id>', methods=['POST'])
@login_required
def delete_subject(subject_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    subject = Subject.query.get_or_404(subject_id)
    branch = subject.branch
    db.session.delete(subject)
    db.session.commit()
    flash('Subject deleted!', 'success')
    return redirect(url_for('manage_subjects', branch=branch))

# ─── STUDENTS ──────────────────────────────────────────────

@app.route('/admin/manage-students')
@login_required
def manage_students():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    selected_sem = request.args.get('sem', type=int)
    selected_branch = request.args.get('branch', '')

    query = Student.query
    if selected_branch:
        query = query.filter_by(branch=selected_branch)
    if selected_sem:
        query = query.filter_by(semester=selected_sem)
    students = query.all()

    return render_template('admin/manage_students.html',
                           students=students,
                           selected_sem=selected_sem,
                           selected_branch=selected_branch,
                           branches=BRANCHES)

@app.route('/admin/promote-semester/<int:semester>', methods=['POST'])
@login_required
def promote_semester(semester):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    branch = request.form.get('branch', '')
    query = Student.query.filter_by(semester=semester)
    if branch:
        query = query.filter_by(branch=branch)
    students = query.all()

    if not students:
        flash(f'No students found in Semester {semester} to promote.', 'danger')
        return redirect(url_for('manage_students'))

    if semester == 8:
        count = len(students)
        for student in students:
            user = User.query.get(student.user_id)
            db.session.delete(student)
            db.session.flush()
            if user:
                db.session.delete(user)
        db.session.commit()
        flash(f'{count} student(s) graduated!', 'success')
        return redirect(url_for('manage_students'))

    new_semester = semester + 1
    count = 0
    for student in students:
        ensure_special_subjects(new_semester, student.branch)
        db.session.flush()

        old_results = Result.query.filter_by(student_id=student.id).all()
        for old_result in old_results:
            db.session.delete(old_result)
        db.session.flush()

        student.semester = new_semester
        db.session.flush()

        new_subjects = Subject.query.filter_by(
            semester=new_semester, branch=student.branch).all()
        for subject in new_subjects:
            db.session.add(Result(ese_marks=0, cfa_marks=0, lab_marks=0,
                                  total_marks=0, grade='-',
                                  student_id=student.id, subject_id=subject.id))
        count += 1

    db.session.commit()
    flash(f'{count} student(s) promoted from Semester {semester} to Semester {new_semester}!', 'success')
    return redirect(url_for('manage_students'))

@app.route('/admin/promote-all', methods=['POST'])
@login_required
def promote_all():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    total_promoted = 0
    total_graduated = 0

    for sem in range(8, 0, -1):
        students = Student.query.filter_by(semester=sem).all()
        if not students:
            continue

        if sem == 8:
            for student in students:
                user = User.query.get(student.user_id)
                db.session.delete(student)
                db.session.flush()
                if user:
                    db.session.delete(user)
                total_graduated += 1
            continue

        new_semester = sem + 1
        for student in students:
            ensure_special_subjects(new_semester, student.branch)
            db.session.flush()

            old_results = Result.query.filter_by(student_id=student.id).all()
            for old_result in old_results:
                db.session.delete(old_result)
            db.session.flush()

            student.semester = new_semester
            db.session.flush()

            new_subjects = Subject.query.filter_by(
                semester=new_semester, branch=student.branch).all()
            for subject in new_subjects:
                db.session.add(Result(ese_marks=0, cfa_marks=0, lab_marks=0,
                                      total_marks=0, grade='-',
                                      student_id=student.id, subject_id=subject.id))
            total_promoted += 1

    db.session.commit()
    flash(f'Promotion complete! {total_promoted} promoted, {total_graduated} graduated.', 'success')
    return redirect(url_for('manage_students'))

@app.route('/admin/add-student', methods=['GET', 'POST'])
@login_required
def add_student():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        name = request.form.get('name')
        roll = request.form.get('roll_number')
        branch = request.form.get('branch')
        semester = int(request.form.get('semester'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists!', 'danger')
            return redirect(url_for('add_student'))

        new_user = User(username=username, role='student')
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.flush()

        new_student = Student(name=name, roll_number=roll, branch=branch,
                              semester=semester, user_id=new_user.id)
        db.session.add(new_student)
        db.session.flush()

        ensure_special_subjects(semester, branch)
        db.session.flush()

        subjects = Subject.query.filter_by(
            semester=semester, branch=branch).all()
        for subject in subjects:
            if not Result.query.filter_by(
                    student_id=new_student.id, subject_id=subject.id).first():
                db.session.add(Result(ese_marks=0, cfa_marks=0, lab_marks=0,
                                      total_marks=0, grade='-',
                                      student_id=new_student.id,
                                      subject_id=subject.id))
        db.session.commit()
        flash(f'Student {name} added with {len(subjects)} subject(s)!', 'success')
        return redirect(url_for('manage_students'))
    return render_template('admin/add_student.html', branches=BRANCHES)

@app.route('/admin/delete-student/<int:student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    student = Student.query.get_or_404(student_id)
    user = User.query.get(student.user_id)
    db.session.delete(student)
    db.session.flush()
    if user:
        db.session.delete(user)
    db.session.commit()
    flash('Student deleted!', 'success')
    return redirect(url_for('manage_students'))
@app.route('/admin/bulk-upload-students', methods=['GET', 'POST'])
@login_required
def bulk_upload_students():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        file = request.files.get('excel_file')
        branch = request.form.get('branch')
        semester = int(request.form.get('semester'))

        if not file or file.filename == '':
            flash('Please select an Excel file.', 'danger')
            return redirect(url_for('bulk_upload_students'))

        try:
            df = pd.read_excel(file)
        except Exception as e:
            flash(f'Error reading Excel file: {str(e)}', 'danger')
            return redirect(url_for('bulk_upload_students'))

        required_cols = {'name', 'roll_number', 'username', 'password'}
        df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]

        if not required_cols.issubset(set(df.columns)):
            flash(f'Excel must have columns: Name, Roll Number, Username, Password. Found: {", ".join(df.columns)}', 'danger')
            return redirect(url_for('bulk_upload_students'))

        ensure_special_subjects(semester, branch)
        db.session.flush()
        subjects = Subject.query.filter_by(semester=semester, branch=branch).all()

        added, skipped = 0, 0
        skipped_rows = []

        for idx, row in df.iterrows():
            name = str(row['name']).strip()
            roll = str(row['roll_number']).strip()
            username = str(row['username']).strip()
            password = str(row['password']).strip()

            if not name or not roll or not username or not password or name == 'nan':
                skipped += 1
                continue

            if User.query.filter_by(username=username).first():
                skipped += 1
                skipped_rows.append(f"{name} ({username} already exists)")
                continue

            if Student.query.filter_by(roll_number=roll).first():
                skipped += 1
                skipped_rows.append(f"{name} (roll {roll} already exists)")
                continue

            new_user = User(username=username, role='student')
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.flush()

            new_student = Student(name=name, roll_number=roll, branch=branch,
                                  semester=semester, user_id=new_user.id)
            db.session.add(new_student)
            db.session.flush()

            for subject in subjects:
                db.session.add(Result(ese_marks=0, cfa_marks=0, lab_marks=0,
                                      total_marks=0, grade='-',
                                      student_id=new_student.id, subject_id=subject.id))
            added += 1

        db.session.commit()

        msg = f'{added} student(s) added successfully to {branch} Sem {semester}!'
        if skipped:
            msg += f' {skipped} row(s) skipped.'
        flash(msg, 'success' if added else 'danger')

        if skipped_rows:
            flash('Skipped: ' + ', '.join(skipped_rows[:5]) +
                 (f' and {len(skipped_rows)-5} more...' if len(skipped_rows) > 5 else ''), 'warning')

        return redirect(url_for('manage_students'))

    return render_template('admin/bulk_upload_students.html', branches=BRANCHES)


@app.route('/admin/bulk-upload-subjects', methods=['GET', 'POST'])
@login_required
def bulk_upload_subjects():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        file = request.files.get('excel_file')
        branch = request.form.get('branch')

        if not file or file.filename == '':
            flash('Please select an Excel file.', 'danger')
            return redirect(url_for('bulk_upload_subjects'))

        try:
            df = pd.read_excel(file)
        except Exception as e:
            flash(f'Error reading Excel file: {str(e)}', 'danger')
            return redirect(url_for('bulk_upload_subjects'))

        df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]
        required_cols = {'name', 'semester', 'credits'}

        if not required_cols.issubset(set(df.columns)):
            flash(f'Excel must have columns: Name, Semester, Credits (+ ESE Max, CFA Max OR Single Max). Found: {", ".join(df.columns)}', 'danger')
            return redirect(url_for('bulk_upload_subjects'))

        added, skipped = 0, 0

        for idx, row in df.iterrows():
            name = str(row['name']).strip()
            if not name or name == 'nan':
                skipped += 1
                continue

            try:
                semester = int(row['semester'])
                credits = int(row['credits'])
            except (ValueError, KeyError):
                skipped += 1
                continue

            is_single = str(row.get('is_single_mark', '')).strip().lower() in ['yes', 'true', '1']

            if Subject.query.filter_by(name=name, semester=semester, branch=branch).first():
                skipped += 1
                continue

            if is_single:
                single_max = int(row.get('single_max', 100)) if pd.notna(row.get('single_max')) else 100
                subject = Subject(name=name, semester=semester, branch=branch,
                                  credits=credits, is_single_mark=True,
                                  single_max=single_max, ese_max=0, cfa_max=0)
            else:
                ese_max = int(row.get('ese_max', 80)) if pd.notna(row.get('ese_max')) else 80
                cfa_max = int(row.get('cfa_max', 20)) if pd.notna(row.get('cfa_max')) else 20
                subject = Subject(name=name, semester=semester, branch=branch,
                                  credits=credits, ese_max=ese_max, cfa_max=cfa_max,
                                  is_single_mark=False)

            db.session.add(subject)
            db.session.flush()

            students_in_sem = Student.query.filter_by(semester=semester, branch=branch).all()
            for student in students_in_sem:
                if not Result.query.filter_by(student_id=student.id, subject_id=subject.id).first():
                    db.session.add(Result(ese_marks=0, cfa_marks=0, lab_marks=0,
                                          total_marks=0, grade='-',
                                          student_id=student.id, subject_id=subject.id))
            added += 1

        db.session.commit()
        flash(f'{added} subject(s) added to {branch}! {skipped} row(s) skipped.', 'success')
        return redirect(url_for('manage_subjects', branch=branch))

    return render_template('admin/bulk_upload_subjects.html', branches=BRANCHES)


@app.route('/admin/download-student-template')
@login_required
def download_student_template():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    df = pd.DataFrame({
        'Name': ['Rahul Sharma', 'Priya Verma'],
        'Roll Number': ['224501', '224502'],
        'Username': ['rahul224501', 'priya224502'],
        'Password': ['student123', 'student123']
    })

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, sheet_name='Students')
    buffer.seek(0)

    response = make_response(buffer.read())
    response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    response.headers['Content-Disposition'] = 'attachment; filename=student_upload_template.xlsx'
    return response


@app.route('/admin/download-subject-template')
@login_required
def download_subject_template():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    df = pd.DataFrame({
        'Name': ['Data Structures', 'DS Lab', 'Minor Project'],
        'Semester': [3, 3, 7],
        'Credits': [4, 2, 2],
        'ESE_Max': [80, 80, ''],
        'CFA_Max': [20, 20, ''],
        'Is_Single_Mark': ['No', 'No', 'Yes'],
        'Single_Max': ['', '', 100]
    })

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, sheet_name='Subjects')
    buffer.seek(0)

    response = make_response(buffer.read())
    response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    response.headers['Content-Disposition'] = 'attachment; filename=subject_upload_template.xlsx'
    return response

@app.route('/admin/edit-student/<int:student_id>', methods=['GET', 'POST'])
@login_required
def edit_student(student_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    student = Student.query.get_or_404(student_id)
    if request.method == 'POST':
        student.name = request.form.get('name')
        student.roll_number = request.form.get('roll_number')
        student.branch = request.form.get('branch', student.branch)
        new_semester = int(request.form.get('semester'))
        student.semester = new_semester
        db.session.flush()

        ensure_special_subjects(new_semester, student.branch)
        db.session.flush()

        subjects = Subject.query.filter_by(
            semester=new_semester, branch=student.branch).all()
        for subject in subjects:
            if not Result.query.filter_by(
                    student_id=student.id, subject_id=subject.id).first():
                db.session.add(Result(ese_marks=0, cfa_marks=0, lab_marks=0,
                                      total_marks=0, grade='-',
                                      student_id=student.id,
                                      subject_id=subject.id))
        db.session.commit()
        flash('Student updated!', 'success')
        return redirect(url_for('manage_students'))
    return render_template('admin/edit_student.html',
                           student=student, branches=BRANCHES)

# ─── MARKS ─────────────────────────────────────────────────

@app.route('/admin/upload-marks/<int:student_id>', methods=['GET', 'POST'])
@login_required
def upload_marks(student_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    student = Student.query.get_or_404(student_id)
    subjects = Subject.query.filter_by(
        semester=student.semester, branch=student.branch).all()

    if request.method == 'POST':
        for subject in subjects:
            is_absent = request.form.get(f'absent_{subject.id}') == 'on'

            if is_absent:
                ese, cfa, lab, total = 0, 0, 0, 0
                ese_absent = True
                cfa_absent = True
            elif subject.is_single_mark:
                ese = min(int(request.form.get(f'single_{subject.id}', 0)), subject.single_max)
                cfa, lab = 0, 0
                total = ese
                ese_absent = False
                cfa_absent = False
            else:
                ese_absent = request.form.get(f'ese_absent_{subject.id}') == 'on'
                cfa_absent = request.form.get(f'cfa_absent_{subject.id}') == 'on'
                ese = 0 if ese_absent else min(int(request.form.get(f'ese_{subject.id}', 0)), subject.ese_max)
                cfa = 0 if cfa_absent else min(int(request.form.get(f'cfa_{subject.id}', 0)), subject.cfa_max)
                lab = 0
                total = ese + cfa

            grade = 'Ab' if is_absent else calculate_grade(
                total, subject_max_total(subject))

            result = Result.query.filter_by(
                student_id=student.id, subject_id=subject.id).first()
            ese_absent_val = locals().get('ese_absent', False)
            cfa_absent_val = locals().get('cfa_absent', False)

            if result:
                result.ese_marks = ese
                result.cfa_marks = cfa
                result.lab_marks = lab
                result.total_marks = total
                result.grade = grade
                result.is_absent = is_absent
                result.ese_absent = ese_absent_val
                result.cfa_absent = cfa_absent_val
            else:
                db.session.add(Result(ese_marks=ese, cfa_marks=cfa, lab_marks=lab,
                                      total_marks=total, grade=grade, is_absent=is_absent,
                                      ese_absent=ese_absent_val, cfa_absent=cfa_absent_val,
                                      student_id=student.id, subject_id=subject.id))
        db.session.flush()

        # Auto-create back papers for failed/absent subjects
        for subject in subjects:
            result = Result.query.filter_by(
                student_id=student.id, subject_id=subject.id).first()
            if result:
                if subject.is_single_mark:
                    if result.grade == 'F' or result.is_absent:
                        create_back_paper(student, subject, student.semester, 'both')
                else:
                    ese_pass = not result.ese_absent and (
                            result.ese_marks / subject.ese_max * 100 >= 35) if subject.ese_max else True
                    cfa_pass = not result.cfa_absent and (
                            result.cfa_marks / subject.cfa_max * 100 >= 35) if subject.cfa_max else True
                    if not ese_pass and not cfa_pass:
                        create_back_paper(student, subject, student.semester, 'both')
                    elif not ese_pass:
                        create_back_paper(student, subject, student.semester, 'ese')
                    elif not cfa_pass:
                        create_back_paper(student, subject, student.semester, 'cfa')

        db.session.commit()
        flash('Marks uploaded! Back papers auto-created for failed subjects.', 'success')
        return redirect(url_for('manage_students'))

    existing_results = {
        r.subject_id: r for r in
        Result.query.filter_by(student_id=student.id).all()
    }
    return render_template('admin/upload_marks.html', student=student,
                           subjects=subjects, existing_results=existing_results)



@app.route('/admin/bulk-upload-marks', methods=['GET', 'POST'])
@login_required
def bulk_upload_marks():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        file = request.files.get('excel_file')
        branch = request.form.get('branch')
        semester = int(request.form.get('semester'))

        if not file or file.filename == '':
            flash('Please select an Excel file.', 'danger')
            return redirect(url_for('bulk_upload_marks'))

        try:
            import openpyxl
            wb = openpyxl.load_workbook(file)
            ws = wb.active
        except Exception as e:
            flash(f'Error reading Excel file: {str(e)}', 'danger')
            return redirect(url_for('bulk_upload_marks'))

        # ── Read header rows ──
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 3:
            flash('Excel file must have at least 3 rows (Subject names, ESE/CFA headers, data).', 'danger')
            return redirect(url_for('bulk_upload_marks'))

        subject_row = rows[0]   # Row 1: subject names
        type_row = rows[1]      # Row 2: ESE / CFA / SINGLE
        data_rows = rows[2:]    # Row 3+: roll number + marks

        # ── Build column map: {col_index: (subject_name, mark_type)} ──
        col_map = {}
        current_subject = None
        for col_idx, val in enumerate(subject_row):
            if col_idx == 0:
                continue  # Skip Roll No column
            if val is not None:
                current_subject = str(val).strip()
            mark_type = str(type_row[col_idx]).strip().upper() if type_row[col_idx] else None
            if current_subject and mark_type and mark_type != 'NONE':
                col_map[col_idx] = (current_subject, mark_type)

        if not col_map:
            flash('Could not read subject columns from Excel. Check format.', 'danger')
            return redirect(url_for('bulk_upload_marks'))

        # ── Get subjects for this branch/semester ──
        subjects = Subject.query.filter_by(
            semester=semester, branch=branch).all()
        subject_lookup = {s.name.strip().upper(): s for s in subjects}

        # ── Get students for this branch/semester ──
        students = Student.query.filter_by(
            semester=semester, branch=branch).all()

        # Build roll number lookup
        # Excel uses row index (1,2,3...) as roll identifier
        # Map by position OR by actual roll number
        student_by_roll = {s.roll_number.strip(): s for s in students}
        student_by_index = {i+1: s for i, s in enumerate(students)}

        updated = 0
        skipped = 0
        errors = []

        for row_idx, data_row in enumerate(data_rows):
            if not data_row or data_row[0] is None:
                continue

            roll_val = str(data_row[0]).strip()
            if not roll_val or roll_val == 'None':
                continue

            # Try to find student by roll number first, then by row index
            student = student_by_roll.get(roll_val)
            if not student:
                # Try as row index number
                try:
                    idx = int(float(roll_val))
                    student = student_by_index.get(idx)
                except (ValueError, TypeError):
                    pass

            if not student:
                skipped += 1
                errors.append(f'Row {row_idx+3}: Roll "{roll_val}" not found in {branch} Sem {semester}')
                continue

            # Group marks by subject name
            subject_marks = {}  # {subject_name: {ESE: val, CFA: val, SINGLE: val}}
            for col_idx, (subj_name, mark_type) in col_map.items():
                val = data_row[col_idx] if col_idx < len(data_row) else None
                if subj_name not in subject_marks:
                    subject_marks[subj_name] = {}
                subject_marks[subj_name][mark_type] = val

            # Save marks for each subject
            for subj_name, marks in subject_marks.items():
                subject = subject_lookup.get(subj_name.upper())
                if not subject:
                    # Try partial match
                    for key, s in subject_lookup.items():
                        if subj_name.upper() in key or key in subj_name.upper():
                            subject = s
                            break

                if not subject:
                    errors.append(f'Subject "{subj_name}" not found in {branch} Sem {semester}')
                    continue

                result = Result.query.filter_by(
                    student_id=student.id, subject_id=subject.id).first()

                if subject.is_single_mark:
                    single_val = marks.get('SINGLE')
                    is_absent = single_val is None or str(single_val).strip() == ''
                    ese = 0 if is_absent else min(int(float(single_val)), subject.single_max)
                    cfa, lab = 0, 0
                    total = ese
                else:
                    ese_val = marks.get('ESE')
                    cfa_val = marks.get('CFA')
                    ese_absent = ese_val is None or str(ese_val).strip() == ''
                    cfa_absent = cfa_val is None or str(cfa_val).strip() == ''
                    is_absent = ese_absent and cfa_absent
                    ese = 0 if ese_absent else min(int(float(ese_val)), subject.ese_max)
                    cfa = 0 if cfa_absent else min(int(float(cfa_val)), subject.cfa_max)
                    lab = 0
                    total = ese + cfa

                grade = 'Ab' if is_absent else calculate_grade(total, subject_max_total(subject))

                if result:
                    result.ese_marks = ese
                    result.cfa_marks = cfa
                    result.lab_marks = lab
                    result.total_marks = total
                    result.grade = grade
                    result.is_absent = is_absent
                    result.ese_absent = ese_absent if not subject.is_single_mark else False
                    result.cfa_absent = cfa_absent if not subject.is_single_mark else False
                else:
                    db.session.add(Result(
                        ese_marks=ese, cfa_marks=cfa, lab_marks=lab,
                        total_marks=total, grade=grade, is_absent=is_absent,
                        ese_absent=ese_absent if not subject.is_single_mark else False,
                        cfa_absent=cfa_absent if not subject.is_single_mark else False,
                        student_id=student.id, subject_id=subject.id
                    ))

            # Auto-create back papers
            db.session.flush()
            all_results = Result.query.filter_by(student_id=student.id).all()
            for r in all_results:
                subj = r.subject_ref
                if subj.semester != semester or subj.branch != branch:
                    continue
                if subj.is_single_mark:
                    if r.grade == 'F' or r.is_absent:
                        create_back_paper(student, subj, semester, 'both')
                else:
                    ese_p = not r.ese_absent and (r.ese_marks / subj.ese_max * 100 >= 35) if subj.ese_max else True
                    cfa_p = not r.cfa_absent and (r.cfa_marks / subj.cfa_max * 100 >= 35) if subj.cfa_max else True
                    if not ese_p and not cfa_p:
                        create_back_paper(student, subj, semester, 'both')
                    elif not ese_p:
                        create_back_paper(student, subj, semester, 'ese')
                    elif not cfa_p:
                        create_back_paper(student, subj, semester, 'cfa')

            updated += 1

        db.session.commit()

        flash(f'Marks uploaded for {updated} student(s)! {skipped} skipped.', 'success')
        if errors:
            unique_errors = list(set(errors))[:5]
            flash('Issues: ' + ' | '.join(unique_errors), 'warning')

        return redirect(url_for('manage_students', branch=branch))

    return render_template('admin/bulk_upload_marks.html', branches=BRANCHES)

# ─── BACK PAPERS ───────────────────────────────────────────
def create_back_paper(student, subject, original_semester, failed_component='both'):
    """
    University back paper rules:
    - Every student gets exactly 3 chances per failed subject
    - 1st Back: original_semester + 2 (capped at 8)
    - 2nd Back: original_semester + 4 (capped at 8)
    - 3rd/Special: always Sem 8
    - If already cleared in any attempt → no more attempts
    - Never create duplicate attempt numbers
    """

    # If subject already cleared in any attempt — stop
    cleared = BackPaper.query.filter_by(
        student_id=student.id,
        subject_id=subject.id,
        status='cleared'
    ).first()
    if cleared:
        return

    # Count total attempts so far
    attempts = BackPaper.query.filter_by(
        student_id=student.id,
        subject_id=subject.id
    ).count()

    # Max 3 attempts
    if attempts >= 3:
        return

    attempt_number = attempts + 1

    # Calculate due semester
    if attempt_number == 1:
        due_semester = min(original_semester + 2, 8)
    elif attempt_number == 2:
        due_semester = min(original_semester + 4, 8)
    else:
        due_semester = 8

    # Status: special if it's attempt 3 OR if due_semester is 8 and attempt > 1
    if attempt_number == 3:
        status = 'special'
    elif attempt_number == 2 and due_semester == 8:
        status = 'special'
    else:
        status = 'pending'

    # Prevent duplicate attempt numbers
    already_exists = BackPaper.query.filter_by(
        student_id=student.id,
        subject_id=subject.id,
        attempt_number=attempt_number
    ).first()
    if already_exists:
        return

    back_paper = BackPaper(
        student_id=student.id,
        subject_id=subject.id,
        original_semester=original_semester,
        attempt_number=attempt_number,
        due_semester=due_semester,
        status=status,
        failed_component=failed_component
    )
    db.session.add(back_paper)


@app.route('/admin/back-papers/<int:student_id>')
@login_required
def view_back_papers(student_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    student = Student.query.get_or_404(student_id)
    back_papers = BackPaper.query.filter_by(student_id=student_id).order_by(
        BackPaper.status, BackPaper.due_semester).all()
    return render_template('admin/back_papers.html',
                           student=student, back_papers=back_papers)


@app.route('/admin/upload-back-marks/<int:back_paper_id>', methods=['GET', 'POST'])
@login_required
def upload_back_marks(back_paper_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    bp = BackPaper.query.get_or_404(back_paper_id)
    subject = bp.subject

    if request.method == 'POST':
        failed_component = bp.failed_component
        passed = False

        # ── Calculate marks and check if passed ──
        if subject.is_single_mark:
            is_absent = request.form.get('absent') == 'on'
            if is_absent:
                bp.ese_marks = 0
                bp.total_marks = 0
                bp.status = 'absent'
            else:
                marks = min(int(request.form.get('single_marks', 0)), subject.single_max)
                bp.ese_marks = marks
                bp.total_marks = marks
                passed = (marks / subject.single_max * 100) >= 35 if subject.single_max else False

        elif failed_component == 'ese':
            is_absent = request.form.get('ese_absent') == 'on'
            if is_absent:
                bp.ese_marks = 0
                bp.status = 'absent'
            else:
                ese = min(int(request.form.get('ese_marks', 0)), subject.ese_max)
                bp.ese_marks = ese
                bp.total_marks = ese
                passed = (ese / subject.ese_max * 100) >= 35 if subject.ese_max else False

        elif failed_component == 'cfa':
            is_absent = request.form.get('cfa_absent') == 'on'
            if is_absent:
                bp.cfa_marks = 0
                bp.status = 'absent'
            else:
                cfa = min(int(request.form.get('cfa_marks', 0)), subject.cfa_max)
                bp.cfa_marks = cfa
                bp.total_marks = cfa
                passed = (cfa / subject.cfa_max * 100) >= 35 if subject.cfa_max else False

        else:  # both
            ese_absent = request.form.get('ese_absent') == 'on'
            cfa_absent = request.form.get('cfa_absent') == 'on'
            ese = 0 if ese_absent else min(int(request.form.get('ese_marks', 0)), subject.ese_max)
            cfa = 0 if cfa_absent else min(int(request.form.get('cfa_marks', 0)), subject.cfa_max)
            bp.ese_marks = ese
            bp.cfa_marks = cfa
            bp.total_marks = ese + cfa

            ese_pass = (ese / subject.ese_max * 100 >= 35) if (subject.ese_max and not ese_absent) else False
            cfa_pass = (cfa / subject.cfa_max * 100 >= 35) if (subject.cfa_max and not cfa_absent) else False

            if ese_absent or cfa_absent:
                bp.status = 'absent'
            else:
                passed = ese_pass and cfa_pass

        bp.updated_at = datetime.now()

        # ── Decide outcome ──
        if passed:
            # PASSED — clear it, no more attempts needed
            bp.status = 'cleared'
            flash(f'✅ Back paper CLEARED! {subject.name}', 'success')

        elif bp.status == 'absent':
            # ABSENT — uses one attempt, create next if chances remaining
            total_attempts = BackPaper.query.filter_by(
                student_id=bp.student_id,
                subject_id=bp.subject_id
            ).count()

            db.session.flush()  # flush first so count is accurate

            if total_attempts < 3:
                create_back_paper(
                    bp.student, subject,
                    bp.original_semester, bp.failed_component
                )
                next_attempt = total_attempts + 1
                flash(f'Marked absent. Attempt {next_attempt} created for {subject.name}.', 'warning')
            else:
                flash(f'All 3 chances exhausted for {subject.name}. No more attempts.', 'danger')

        else:
            # FAILED — create next attempt if chances remaining
            bp.status = 'failed'
            total_attempts = BackPaper.query.filter_by(
                student_id=bp.student_id,
                subject_id=bp.subject_id
            ).count()

            db.session.flush()

            if total_attempts < 3:
                create_back_paper(
                    bp.student, subject,
                    bp.original_semester, bp.failed_component
                )
                next_attempt = total_attempts + 1
                flash(f'Failed. Attempt {next_attempt} created for {subject.name}.', 'warning')
            else:
                flash(f'All 3 chances exhausted for {subject.name}. No more attempts.', 'danger')

        db.session.commit()
        return redirect(url_for('view_back_papers', student_id=bp.student_id))

    return render_template('admin/upload_back_marks.html', bp=bp, subject=subject)


@app.route('/admin/all-back-papers')
@login_required
def all_back_papers():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    selected_branch = request.args.get('branch', '')
    selected_sem = request.args.get('sem', type=int)
    selected_status = request.args.get('status', 'pending')

    query = BackPaper.query.join(Student)
    if selected_branch:
        query = query.filter(Student.branch == selected_branch)
    if selected_sem:
        query = query.filter(BackPaper.due_semester == selected_sem)
    if selected_status:
        query = query.filter(BackPaper.status == selected_status)

    back_papers = query.order_by(BackPaper.due_semester, Student.name).all()

    return render_template('admin/all_back_papers.html',
                           back_papers=back_papers,
                           selected_branch=selected_branch,
                           selected_sem=selected_sem,
                           selected_status=selected_status,
                           branches=BRANCHES)

#fix back paper_________________________
@app.route('/admin/fix-back-papers')
@login_required
def fix_back_papers():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    # Get all absent/failed back papers
    all_bps = BackPaper.query.filter(
        BackPaper.status.in_(['absent', 'failed'])
    ).order_by(BackPaper.student_id, BackPaper.subject_id, BackPaper.attempt_number).all()

    created = 0
    for bp in all_bps:
        # Skip if subject already cleared in any attempt
        cleared = BackPaper.query.filter_by(
            student_id=bp.student_id,
            subject_id=bp.subject_id,
            status='cleared'
        ).first()
        if cleared:
            continue

        # Check if next attempt already exists
        next_exists = BackPaper.query.filter_by(
            student_id=bp.student_id,
            subject_id=bp.subject_id,
            attempt_number=bp.attempt_number + 1
        ).first()

        if not next_exists:
            total_attempts = BackPaper.query.filter_by(
                student_id=bp.student_id,
                subject_id=bp.subject_id
            ).count()

            if total_attempts < 3:
                create_back_paper(
                    bp.student,
                    bp.subject,
                    bp.original_semester,
                    bp.failed_component
                )
                db.session.flush()
                created += 1

    db.session.commit()
    flash(f'Fix complete! {created} missing attempt(s) created.', 'success')
    return redirect(url_for('all_back_papers'))

# ─── ADMINS ────────────────────────────────────────────────

@app.route('/admin/manage-admins')
@login_required
def manage_admins():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    admins = User.query.filter_by(role='admin').all()
    return render_template('admin/manage_admins.html', admins=admins)

@app.route('/admin/add-admin', methods=['GET', 'POST'])
@login_required
def add_admin():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Username already exists!', 'danger')
            return redirect(url_for('add_admin'))
        new_admin = User(username=username, role='admin')
        new_admin.set_password(password)
        db.session.add(new_admin)
        db.session.commit()
        flash(f'Admin {username} created!', 'success')
        return redirect(url_for('manage_admins'))
    return render_template('admin/add_admin.html')

@app.route('/admin/delete-admin/<int:admin_id>', methods=['POST'])
@login_required
def delete_admin(admin_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if admin_id == current_user.id:
        flash('You cannot delete yourself!', 'danger')
        return redirect(url_for('manage_admins'))
    if User.query.filter_by(role='admin').count() <= 1:
        flash('Cannot delete! At least 1 admin must exist.', 'danger')
        return redirect(url_for('manage_admins'))
    admin = User.query.get_or_404(admin_id)
    db.session.delete(admin)
    db.session.commit()
    flash('Admin deleted!', 'success')
    return redirect(url_for('manage_admins'))

# ─── PDF DOWNLOAD ───────────────────────────────────────────

@app.route('/download-result/<int:student_id>')
@login_required
def download_result(student_id):
    student = Student.query.get_or_404(student_id)
    if current_user.role == 'student' and student.user_id != current_user.id:
        flash('Access denied!', 'danger')
        return redirect(url_for('dashboard'))

    log = DownloadLog(student_id=student.id,
                      downloaded_by=current_user.username,
                      downloaded_at=datetime.now())
    db.session.add(log)
    db.session.commit()

    results = Result.query.join(Subject).filter(
        Result.student_id == student.id,
        Subject.semester == student.semester,
        Subject.branch == student.branch
    ).all()

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    dark_text = HexColor('#0f172a')
    gray_text = HexColor('#475569')
    border_color = HexColor('#1e293b')
    header_bg = HexColor('#e2e8f0')
    light_row = HexColor('#f8fafc')
    red = HexColor('#dc2626')
    green = HexColor('#059669')

    sem_words = {1: "First", 2: "Second", 3: "Third", 4: "Fourth",
                 5: "Fifth", 6: "Sixth", 7: "Seventh", 8: "Eighth"}

    p.setFillColor(dark_text)
    p.setFont("Helvetica-Bold", 16)
    p.drawCentredString(width / 2, height - 55, "RESULT REVEALER")
    p.setFont("Helvetica", 9)
    p.drawCentredString(width / 2, height - 70, "Student Result Management System")
    p.setFont("Helvetica-Bold", 13)
    p.drawCentredString(width / 2, height - 92, f"B.Tech. ({student.branch})")
    p.setFont("Helvetica-Bold", 13)
    p.drawCentredString(width / 2, height - 110,
                        f"{sem_words.get(student.semester, '')} Semester Examination "
                        f"{datetime.now().year}-{str(datetime.now().year + 1)[2:]}")
    p.setFont("Helvetica-Bold", 14)
    p.drawCentredString(width / 2, height - 130, "MARK-SHEET")

    p.setStrokeColor(border_color)
    p.setLineWidth(1.2)
    p.line(50, height - 140, width - 50, height - 140)

    detail_y = height - 162
    p.setFont("Helvetica-Bold", 10)
    p.setFillColor(dark_text)
    p.drawString(50, detail_y, f"Name : {student.name.upper()}")
    p.drawString(330, detail_y, f"Roll No. : {student.roll_number}")
    detail_y -= 18
    p.drawString(50, detail_y, f"Branch : {student.branch.upper()}")
    detail_y -= 18
    p.drawString(50, detail_y, f"Semester : {student.semester}")

    table_top = detail_y - 22
    col_sn = 45
    col_subject = 70
    col_credit = 270
    col_ese = 320
    col_cfa = 370
    col_total = 420
    col_scale = 470
    col_gp = 520
    table_right = width - 45
    header_h = 30
    row_h = 20

    p.setFillColor(header_bg)
    p.rect(col_sn, table_top - header_h, table_right - col_sn, header_h, fill=1, stroke=0)
    p.setStrokeColor(border_color)
    p.setLineWidth(1)
    p.rect(col_sn, table_top - header_h, table_right - col_sn, header_h, fill=0, stroke=1)

    for x in [col_subject, col_credit, col_ese, col_cfa, col_total, col_scale, col_gp]:
        p.line(x, table_top, x, table_top - header_h)

    p.setFont("Helvetica-Bold", 7.5)
    p.setFillColor(dark_text)
    p.drawCentredString((col_sn + col_subject) / 2, table_top - 12, "S.N.")
    p.drawCentredString((col_subject + col_credit) / 2, table_top - 12, "SUBJECT / PAPER")
    p.drawCentredString((col_credit + col_ese) / 2, table_top - 17, "CREDIT")
    p.drawCentredString((col_ese + col_cfa) / 2, table_top - 12, "ESE")
    p.drawCentredString((col_cfa + col_total) / 2, table_top - 12, "CFA")
    p.drawCentredString((col_total + col_scale) / 2, table_top - 12, "TOTAL")
    p.drawCentredString((col_scale + col_gp) / 2, table_top - 12, "POINT")
    p.drawCentredString((col_scale + col_gp) / 2, table_top - 22, "SCALE")
    p.drawCentredString((col_gp + table_right) / 2, table_top - 12, "GRADE")
    p.drawCentredString((col_gp + table_right) / 2, table_top - 22, "POINT")

    y_top = table_top - header_h
    total_credits = 0
    total_grade_points = 0
    has_failure = False

    for i, r in enumerate(results):
        subj = r.subject_ref
        row_top = y_top - (i * row_h)
        row_bottom = row_top - row_h

        if i % 2 == 0:
            p.setFillColor(light_row)
            p.rect(col_sn, row_bottom, table_right - col_sn, row_h, fill=1, stroke=0)

        p.setStrokeColor(border_color)
        p.setLineWidth(0.6)
        p.rect(col_sn, row_bottom, table_right - col_sn, row_h, fill=0, stroke=1)
        for x in [col_subject, col_credit, col_ese, col_cfa, col_total, col_scale, col_gp]:
            p.line(x, row_top, x, row_bottom)

        max_total = subject_max_total(subj)
        point_scale = calculate_point_scale(r.total_marks, max_total) if not r.is_absent else 0
        grade_point = round(point_scale * subj.credits, 1)

        if r.is_absent or r.grade == 'F':
            has_failure = True

        p.setFillColor(dark_text)
        p.setFont("Helvetica", 8)
        p.drawCentredString((col_sn + col_subject) / 2, row_bottom + 6, str(i + 1))
        p.drawString(col_subject + 4, row_bottom + 6, subj.name[:32])
        p.drawCentredString((col_credit + col_ese) / 2, row_bottom + 6, str(subj.credits))

        if r.is_absent:
            p.setFillColor(red)
            p.drawCentredString((col_ese + col_cfa) / 2, row_bottom + 6, "Ab")
            p.setFillColor(gray_text)
            p.drawCentredString((col_cfa + col_total) / 2, row_bottom + 6,
                                "-" if subj.is_single_mark else "Ab")
            p.setFillColor(red)
            p.drawCentredString((col_total + col_scale) / 2, row_bottom + 6, "Ab")
            p.setFillColor(dark_text)
        else:
            p.drawCentredString((col_ese + col_cfa) / 2, row_bottom + 6, str(r.ese_marks))
            p.drawCentredString((col_cfa + col_total) / 2, row_bottom + 6,
                                str(r.cfa_marks) if not subj.is_single_mark else "-")
            p.drawCentredString((col_total + col_scale) / 2, row_bottom + 6, str(r.total_marks))

        p.drawCentredString((col_scale + col_gp) / 2, row_bottom + 6, f"{point_scale}")
        p.setFont("Helvetica-Bold", 8)
        p.drawCentredString((col_gp + table_right) / 2, row_bottom + 6, f"{grade_point}")
        p.setFont("Helvetica", 8)

        total_credits += subj.credits
        total_grade_points += grade_point

    table_bottom = y_top - (len(results) * row_h)

    tgp_row_top = table_bottom
    tgp_row_bottom = tgp_row_top - row_h
    p.setFillColor(header_bg)
    p.rect(col_sn, tgp_row_bottom, table_right - col_sn, row_h, fill=1, stroke=0)
    p.setStrokeColor(border_color)
    p.setLineWidth(1)
    p.rect(col_sn, tgp_row_bottom, table_right - col_sn, row_h, fill=0, stroke=1)
    for x in [col_credit, col_gp]:
        p.line(x, tgp_row_top, x, tgp_row_bottom)

    p.setFont("Helvetica-Bold", 8)
    p.setFillColor(dark_text)
    p.drawCentredString((col_sn + col_credit) / 2, tgp_row_bottom + 6,
                        f"* TGP {sem_words.get(student.semester, '')} Semester")
    p.drawCentredString((col_credit + col_ese) / 2, tgp_row_bottom + 6, str(total_credits))
    p.setFont("Helvetica-Bold", 9)
    p.drawCentredString((col_gp + table_right) / 2, tgp_row_bottom + 6,
                        f"{round(total_grade_points, 1)}")

    gpa = round(total_grade_points / total_credits, 2) if total_credits else 0
    gpa_row_top = tgp_row_bottom
    gpa_row_bottom = gpa_row_top - row_h
    p.setStrokeColor(border_color)
    p.setLineWidth(1)
    p.rect(col_sn, gpa_row_bottom, table_right - col_sn, row_h, fill=0, stroke=1)
    p.line(col_credit, gpa_row_top, col_credit, gpa_row_bottom)
    p.line(col_gp, gpa_row_top, col_gp, gpa_row_bottom)

    p.setFont("Helvetica-Bold", 8)
    p.drawCentredString((col_sn + col_credit) / 2, gpa_row_bottom + 6, "** GPA")
    p.setFont("Helvetica-Bold", 10)
    p.drawCentredString((col_gp + table_right) / 2, gpa_row_bottom + 6, f"{gpa}")

    status_y = gpa_row_bottom - 25
    if has_failure:
        p.setFillColor(HexColor('#f59e0b'))
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, status_y, "RESULT — PROMOTED")
    else:
        p.setFillColor(green)
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, status_y, "RESULT — PASSED")

    note_y = status_y - 35
    p.setFont("Helvetica-Oblique", 7)
    p.setFillColor(gray_text)
    p.drawString(50, note_y, "NOTE :")
    p.drawString(85, note_y, "1. The Minimum Pass Marks - For Theory - CFA = 35%, ESE = 35% in each paper.")
    p.drawString(85, note_y - 11, "2. CFA - Continuous Formative Assessment, ESE - End Semester Examination")
    p.drawString(85, note_y - 22, "3. TGP = Total Grade Point, GPA = Grade Point Average.")

    sig_y = 70
    p.setFont("Helvetica", 8)
    p.setFillColor(dark_text)
    p.drawString(50, sig_y, f"Date : {datetime.now().strftime('%d %b %Y')}")
    p.drawString(50, sig_y - 14, "Prepared by :")
    p.drawCentredString(width / 2, sig_y - 14, "Checked by :")
    p.drawRightString(width - 50, sig_y - 14, "Asstt. Registrar (Exam.)")

    p.setStrokeColor(border_color)
    p.setLineWidth(0.5)
    p.line(50, sig_y - 30, width - 50, sig_y - 30)
    p.setFont("Helvetica", 6.5)
    p.setFillColor(gray_text)
    p.drawString(50, sig_y - 42,
                 f"Downloaded by: {current_user.username} ({current_user.role})  |  "
                 f"{datetime.now().strftime('%d %b %Y, %I:%M %p')}  |  Computer-generated document")

    p.save()
    buffer.seek(0)
    response = make_response(buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = \
        f'attachment; filename={student.roll_number}_marksheet.pdf'
    return response

# ─── INIT DB ───────────────────────────────────────────────

def create_admin():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("✅ Admin created: username=admin, password=admin123")

if __name__ == '__main__':
    create_admin()
    app.run(debug=True)