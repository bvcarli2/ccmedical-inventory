"""Opt-in PostgreSQL employee authentication for local staging.

Registers before the staging dispatcher so no route can bypass authentication.
Not a production deployment approval: HTTPS, auditing, rate limiting and broader
security review remain necessary.
"""
import os
import secrets
import hmac
from datetime import timedelta
import psycopg
from psycopg.rows import dict_row
from flask import request, session, redirect, url_for, abort, render_template_string, g
from werkzeug.security import check_password_hash, generate_password_hash

LOGIN = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>CC Medical Login</title></head><body><h1>CC Medical Employee Login</h1>{% if error %}<p role="alert">{{error}}</p>{% endif %}<form method="post"><input type="hidden" name="_csrf_token" value="{{token}}"><label>Email <input type="email" name="email" required></label><label>Password <input type="password" name="password" required></label><button>Sign in</button></form></body></html>"""
WRITE_ROLES = {
 "inventory_new": {"admin","employee"},
 "transfer": {"admin","employee"},
 "repair": {"admin","employee"},
 "repair_update": {"admin","employee"},
 "core_detail": {"admin","employee"},
 "request_product": {"admin","employee"},
 "compatibility": {"admin","employee"},
 "add_oem_part": {"admin"},
 "edit_oem_part": {"admin"},
 "approve_request": {"admin"},
 "edit": {"admin"},
 "import_preview": {"admin"},
 "import_confirm": {"admin"},
 "product_admin": {"admin"},
}
# Unknown POST endpoints are forbidden, even for admins.
def register_pg_security(app, database_url):
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    def db():
        return psycopg.connect(database_url,row_factory=dict_row)

    @app.before_request
    def employee_security_gate():
        if request.endpoint=="static":
            return None
        if "_csrf_token" not in session:
            session["_csrf_token"]=secrets.token_urlsafe(32)
        if request.endpoint=="pg_employee_login":
            if request.method=="POST":
                token=request.form.get("_csrf_token","")
                if not hmac.compare_digest(token,session["_csrf_token"]):
                    abort(403)
            return None
        if request.endpoint=="pg_employee_logout":
            if request.method!="POST" or not hmac.compare_digest(
                request.form.get("_csrf_token",""),session["_csrf_token"]):
                abort(403)
            session.clear()
            return redirect(url_for("pg_employee_login"))
        user_id=session.get("employee_id")
        if not isinstance(user_id,int):
            return redirect(url_for("pg_employee_login"))
        with db() as conn:
            user=conn.execute("SELECT id,role FROM employee_users WHERE id=%s AND active=TRUE",(user_id,)).fetchone()
        if not user:
            session.clear()
            return redirect(url_for("pg_employee_login"))
        g.employee_role=user["role"]
        if user["role"]!="admin":
            session.pop("product_admin",None)
        else:
            session["product_admin"]=True
        if request.method not in ("GET","HEAD","OPTIONS"):
            roles=WRITE_ROLES.get(request.endpoint)
            if not roles or user["role"] not in roles:
                abort(403)
            if not hmac.compare_digest(request.form.get("_csrf_token",""),session["_csrf_token"]):
                abort(403)
        return None

    @app.route("/login",methods=["GET","POST"],endpoint="pg_employee_login")
    def login():
        error=None
        if request.method=="POST":
            email=request.form.get("email","").strip().lower()
            password=request.form.get("password","")
            with db() as conn:
                user=conn.execute("SELECT id,password_hash FROM employee_users WHERE LOWER(email)=%s AND active=TRUE",(email,)).fetchone()
            if user and check_password_hash(user["password_hash"],password):
                session.clear()
                session["employee_id"]=user["id"]
                session["_csrf_token"]=secrets.token_urlsafe(32)
                session.permanent=True
                return redirect(url_for("index"))
            error="Invalid email or password"
        return render_template_string(LOGIN,error=error,token=session["_csrf_token"])

    @app.post("/logout",endpoint="pg_employee_logout")
    def logout():
        return None

    @app.context_processor
    def csrf_context():
        return {"csrf_token":session.get("_csrf_token","")}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="same-origin"
        response.headers["Cache-Control"]="no-store"
        return response

    @app.cli.command("create-pg-admin")
    def create_admin():
        email=os.environ.get("CCM_BOOTSTRAP_EMAIL","").strip().lower()
        password=os.environ.get("CCM_BOOTSTRAP_PASSWORD","")
        if not email or len(password)<16:
            raise RuntimeError("Set CCM_BOOTSTRAP_EMAIL and CCM_BOOTSTRAP_PASSWORD (16+ characters)")
        with db() as conn:
            conn.execute("""INSERT INTO employee_users(email,password_hash,role)
              VALUES (%s,%s,'admin')
              ON CONFLICT (LOWER(email)) DO UPDATE
              SET password_hash=EXCLUDED.password_hash,role='admin',active=TRUE""",
              (email,generate_password_hash(password)))
        print("PostgreSQL admin provisioned. Clear bootstrap credentials from environment.")
