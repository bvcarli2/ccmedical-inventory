"""Employee login guard for the CC Medical prototype.

This is a staging-only SQLite implementation. PostgreSQL migration is required
before deploying the application on Render.
"""
import os
import sqlite3
from functools import wraps
from flask import request, session, redirect, url_for, render_template_string, abort
from werkzeug.security import generate_password_hash, check_password_hash

LOGIN_HTML = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>CC Medical Sign In</title></head><body style="font:16px Arial;max-width:360px;margin:10vh auto;padding:20px"><h2>CC Medical — Employee Sign In</h2>{% if error %}<p role="alert" style="color:#b00">{{error}}</p>{% endif %}<form method="post"><label>Email<br><input name="email" type="email" required autocomplete="username"></label><p><label>Password<br><input name="password" type="password" required autocomplete="current-password"></label></p><button>Sign in</button></form></body></html>"""

USER_SCHEMA = """CREATE TABLE IF NOT EXISTS employee_users (
 id INTEGER PRIMARY KEY,
 email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','employee','viewer')),
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);"""

def register_employee_auth(app, connect):
    def init_users():
        with connect() as db:
            db.executescript(USER_SCHEMA)

    # Existing SQLite connection is only appropriate for local development.
    init_users()

    @app.before_request
    def employee_gate():
        if request.endpoint in ('employee_login', 'static'):
            return None
        if not session.get('employee_id'):
            return redirect(url_for('employee_login', next=request.path))
        with connect() as db:
            user = db.execute('SELECT id,role FROM employee_users WHERE id=? AND active=1', (session['employee_id'],)).fetchone()
        if not user:
            session.clear()
            return redirect(url_for('employee_login'))
        if request.method not in ('GET','HEAD','OPTIONS'):
            # Until per-route permissions and CSRF are complete, block all
            # write operations rather than exposing unsafe mutations.
            abort(403, 'Write operations are disabled pending security review.')
        return None

    @app.route('/login', methods=['GET','POST'])
    def employee_login():
        error = None
        if request.method == 'POST':
            email = request.form.get('email','').strip().lower()
            password = request.form.get('password','')
            with connect() as db:
                user = db.execute('SELECT id,password_hash,active FROM employee_users WHERE email=?', (email,)).fetchone()
            if user and user['active'] and check_password_hash(user['password_hash'], password):
                session.clear()
                session['employee_id'] = user['id']
                session.permanent = True
                return redirect(url_for('index'))
            error = 'Invalid email or password'
        return render_template_string(LOGIN_HTML, error=error)

    @app.cli.command('create-local-admin')
    def create_local_admin():
        """Provision an admin from environment variables, never a public route."""
        email = os.environ.get('CCM_BOOTSTRAP_EMAIL','').strip().lower()
        password = os.environ.get('CCM_BOOTSTRAP_PASSWORD','')
        if not email or len(password) < 16:
            raise RuntimeError('Set CCM_BOOTSTRAP_EMAIL and a 16+ character CCM_BOOTSTRAP_PASSWORD in your local environment.')
        with connect() as db:
            db.execute('INSERT INTO employee_users(email,password_hash,role,active) VALUES (?,?,?,1) ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash,role=excluded.role,active=1',
                       (email,generate_password_hash(password),'admin'))
            db.commit()
        print('Local admin created; clear CCM_BOOTSTRAP_PASSWORD from the environment.')
