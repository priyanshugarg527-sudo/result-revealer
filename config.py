import os

class Config:
    SECRET_KEY = 'result-revealer-secret-key-2026-change-this-in-production'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///results.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True