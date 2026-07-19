from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(10), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    roll_number = db.Column(db.String(20), unique=True, nullable=False)
    branch = db.Column(db.String(50), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    results = db.relationship('Result', backref='student', lazy=True, cascade='all, delete-orphan')
    downloads = db.relationship('DownloadLog', backref='student', lazy=True, cascade='all, delete-orphan')
    back_papers = db.relationship('BackPaper', backref='student', lazy=True, cascade='all, delete-orphan')

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    branch = db.Column(db.String(50), nullable=False, default='Information Technology')
    credits = db.Column(db.Integer, default=4)
    ese_max = db.Column(db.Integer, default=80)
    cfa_max = db.Column(db.Integer, default=20)
    is_single_mark = db.Column(db.Boolean, default=False)
    single_max = db.Column(db.Integer, default=100)
    results = db.relationship('Result', backref='subject_ref', lazy=True, cascade='all, delete-orphan')

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ese_marks = db.Column(db.Integer, nullable=False, default=0)
    cfa_marks = db.Column(db.Integer, nullable=False, default=0)
    lab_marks = db.Column(db.Integer, nullable=True, default=0)
    total_marks = db.Column(db.Integer, nullable=False, default=0)
    grade = db.Column(db.String(5), nullable=False, default='-')
    is_absent = db.Column(db.Boolean, default=False)
    ese_absent = db.Column(db.Boolean, default=False)
    cfa_absent = db.Column(db.Boolean, default=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False)

class BackPaper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False)
    original_semester = db.Column(db.Integer, nullable=False)
    attempt_number = db.Column(db.Integer, nullable=False, default=1)
    due_semester = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    failed_component = db.Column(db.String(10), default='both')  # 'ese', 'cfa', or 'both'
    ese_marks = db.Column(db.Integer, default=0)
    cfa_marks = db.Column(db.Integer, default=0)
    total_marks = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now)

    subject = db.relationship('Subject', backref='back_papers', lazy=True)

class DownloadLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    downloaded_by = db.Column(db.String(80), nullable=False)
    downloaded_at = db.Column(db.DateTime, default=datetime.now)