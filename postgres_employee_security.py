"""Opt-in PostgreSQL employee authentication for local staging.

Registers before the staging dispatcher so no route can bypass authentication.
Not a production deployment approval: HTTPS, auditing, rate limiting and broader
security review remain necessary.
"""
import os
import secrets
import hmac
import re
from markupsafe import escape
from datetime import timedelta, datetime, timezone
import psycopg
from psycopg.rows import dict_row
from flask import request, session, redirect, url_for, abort, render_template_string, g
from werkzeug.security import check_password_hash, generate_password_hash

LOGIN = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>CC Medical Login</title></head><body><h1>CC Medical Employee Login</h1>{% if error %}<p role="alert">{{error}}</p>{% endif %}<form method="post"><input type="hidden" name="_csrf_token" value="{{token}}"><label>Email <input type="email" name="email" required></label><label>Password <input type="password" name="password" required></label><button>Sign in</button></form></body></html>"""
WRITE_ROLES = {
 "inventory_new": {"admin","employee"},
 "attachment_upload": {"admin","employee"},
 "transfer": {"admin","employee"},
 "repair": {"admin","employee"},
 "repair_update": {"admin","employee"},
 "repair_new": {"admin","employee"},
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
    from staging_security_defaults import configure_staging_limits, apply_staging_headers
    configure_staging_limits(app)
    app.after_request(apply_staging_headers)
    require_https=os.environ.get("CCM_REQUIRE_HTTPS")=="1"
    app.config["SESSION_COOKIE_SECURE"]=require_https
    if require_https:
        app.config["PREFERRED_URL_SCHEME"]="https"
    def db():
        return psycopg.connect(database_url,row_factory=dict_row)

    @app.before_request
    def employee_security_gate():
        if require_https and not request.is_secure:
            abort(403, "HTTPS is required")
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
        if isinstance(user_id,int):
            now=int(datetime.now(timezone.utc).timestamp())
            last=session.get("last_seen")
            if not isinstance(last,int) or now-last>1800 or now-last<0:
                session.clear()
                return redirect(url_for("pg_employee_login"))
            session["last_seen"]=now
        if not isinstance(user_id,int):
            return redirect(url_for("pg_employee_login"))
        with db() as conn:
            user=conn.execute("SELECT id,role FROM employee_users WHERE id=%s AND active=TRUE",(user_id,)).fetchone()
        if not user:
            session.clear()
            return redirect(url_for("pg_employee_login"))
        g.employee_role=user["role"]
        g.employee_id=user["id"]
        if request.path.startswith("/products/admin") and user["role"]!="admin":
            abort(403)
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
            client_ip=request.remote_addr or "unknown"
            with db() as conn:
                ip_attempt=conn.execute("SELECT blocked_until FROM employee_login_ip_attempts WHERE ip_address=%s",(client_ip,)).fetchone()
                if ip_attempt and ip_attempt["blocked_until"] and ip_attempt["blocked_until"]>datetime.now(timezone.utc):
                    return render_template_string(LOGIN,error="Too many attempts. Try again later.",token=session["_csrf_token"]),429
                attempt=conn.execute("SELECT blocked_until FROM employee_login_attempts WHERE email=%s",(email,)).fetchone()
                if attempt and attempt["blocked_until"]:
                    if attempt["blocked_until"]>datetime.now(timezone.utc):
                        return render_template_string(LOGIN,error="Too many attempts. Try again later.",token=session["_csrf_token"]),429
            with db() as conn:
                user=conn.execute("SELECT id,password_hash FROM employee_users WHERE LOWER(email)=%s AND active=TRUE",(email,)).fetchone()
            if user and check_password_hash(user["password_hash"],password):
                with db() as conn:
                    conn.execute("DELETE FROM employee_login_attempts WHERE email=%s",(email,))
                session.clear()
                session["employee_id"]=user["id"]
                session["last_seen"]=int(datetime.now(timezone.utc).timestamp())
                session["_csrf_token"]=secrets.token_urlsafe(32)
                session.permanent=True
                return redirect(url_for("index"))
            with db() as conn:
                conn.execute("""INSERT INTO employee_login_attempts(email,failures,last_failure)
                    VALUES (%s,1,CURRENT_TIMESTAMP)
                    ON CONFLICT(email) DO UPDATE SET
                    failures=CASE WHEN employee_login_attempts.last_failure < CURRENT_TIMESTAMP-INTERVAL '15 minutes'
                      THEN 1 ELSE employee_login_attempts.failures+1 END,
                    last_failure=CURRENT_TIMESTAMP,
                    blocked_until=CASE WHEN employee_login_attempts.failures>=4
                      AND employee_login_attempts.last_failure >= CURRENT_TIMESTAMP-INTERVAL '15 minutes'
                      THEN CURRENT_TIMESTAMP+INTERVAL '15 minutes' ELSE NULL END""",(email,))
            error="Invalid email or password"
        return render_template_string(LOGIN,error=error,token=session["_csrf_token"])

    @app.post("/logout",endpoint="pg_employee_logout")
    def logout():
        return None

    @app.context_processor
    def csrf_context():
        return {"csrf_token":session.get("_csrf_token","")}

    @app.after_request
    def attach_csrf(response):
        # Existing prototype templates lack hidden CSRF fields. Add them to
        # ordinary POST forms without modifying the SQLite templates.
        if response.mimetype=="text/html" and session.get("_csrf_token"):
            html=response.get_data(as_text=True)
            token=str(escape(session["_csrf_token"]))
            html=re.sub(r'(<form\b[^>]*\bmethod\s*=\s*["\\\']?post["\\\']?[^>]*>)',
                        lambda m:m.group(1)+'<input type="hidden" name="_csrf_token" value="'+token+'">',
                        html,flags=re.I)
            response.set_data(html)
        return response

    @app.after_request
    def security_headers(response):
        if require_https and request.is_secure:
            response.headers["Strict-Transport-Security"]="max-age=31536000"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="same-origin"
        response.headers["Cache-Control"]="no-store"
        return response

    @app.cli.command("provision-pg-employee")
    def provision_employee():
        email=os.environ.get("CCM_EMPLOYEE_EMAIL","").strip().lower()
        password=os.environ.get("CCM_EMPLOYEE_PASSWORD","")
        role=os.environ.get("CCM_EMPLOYEE_ROLE","viewer")
        if not email or "@" not in email or len(password)<16 or role not in ("admin","employee","viewer"):
            raise RuntimeError("Set CCM_EMPLOYEE_EMAIL, CCM_EMPLOYEE_PASSWORD (16+ chars), and CCM_EMPLOYEE_ROLE")
        with db() as conn:
            conn.execute("""INSERT INTO employee_users(email,password_hash,role,active)
                VALUES (%s,%s,%s,TRUE)
                ON CONFLICT (LOWER(email)) DO UPDATE SET
                password_hash=EXCLUDED.password_hash,role=EXCLUDED.role,active=TRUE""",
                (email,generate_password_hash(password),role))
        print("Employee provisioned. Clear CCM_EMPLOYEE_PASSWORD immediately.")

    @app.cli.command("deactivate-pg-employee")
    def deactivate_employee():
        email=os.environ.get("CCM_EMPLOYEE_EMAIL","").strip().lower()
        if not email:
            raise RuntimeError("Set CCM_EMPLOYEE_EMAIL")
        with db() as conn:
            result=conn.execute("UPDATE employee_users SET active=FALSE WHERE LOWER(email)=%s",(email,))
        print("Accounts deactivated:",result.rowcount)

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
