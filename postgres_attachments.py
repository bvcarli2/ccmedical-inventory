"""Private repair attachments for opt-in PostgreSQL staging only.

No public file URLs. Files are stored outside the static directory, with
random names, checked signatures, and per-repair authorization.
"""
import hashlib
import os
import secrets
from pathlib import Path
from flask import abort, g, request, send_file
import psycopg
from psycopg.rows import dict_row

MAX_FILE = 5 * 1024 * 1024
TYPES = {"image/jpeg": (".jpg", (bytes.fromhex("ffd8ff"),)),
         "image/png": (".png", (bytes.fromhex("89504e470d0a1a0a"),)),
         "application/pdf": (".pdf", (b"%PDF-",))}

def identify(data):
    if not data or len(data) > MAX_FILE:
        raise ValueError("File is empty or exceeds 5 MB")
    for mime, (ext, signatures) in TYPES.items():
        if any(data.startswith(sig) for sig in signatures):
            if mime == "application/pdf" and b"%%EOF" not in data[-2048:]:
                raise ValueError("Invalid PDF trailer")
            return mime, ext
    raise ValueError("Only JPEG, PNG, and PDF files are accepted")

def register_attachment_routes(app, database_url):
    base = Path(os.environ.get("CCM_PRIVATE_UPLOAD_DIR", "/tmp/ccmedical-private-staging")).resolve()
    if base.is_relative_to(Path(app.static_folder).resolve()):
        raise RuntimeError("Private uploads cannot be stored under Flask static files")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(base, 0o700)

    def connect():
        return psycopg.connect(database_url, row_factory=dict_row)

    @app.post("/repairs/<int:rid>/attachments", endpoint="attachment_upload")
    def attachment_upload(rid):
        item = request.files.get("file")
        if not item:
            return "File required", 400
        data = item.read(MAX_FILE + 1)
        try:
            mime, ext = identify(data)
        except ValueError as exc:
            return str(exc), 400
        original = (item.filename or "attachment").replace("\\", "/").split("/")[-1][:180]
        token = secrets.token_hex(24)
        target = base / (token + ext)
        try:
            with connect() as db:
                exists = db.execute("SELECT id FROM customer_repairs WHERE id=%s", (rid,)).fetchone()
                if not exists:
                    abort(404)
                with target.open("xb") as handle:
                    os.chmod(target, 0o600)
                    handle.write(data)
                result = db.execute(
                    """INSERT INTO repair_attachments
                    (repair_id,stored_name,original_name,mime_type,byte_count,sha256,uploaded_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (rid, target.name, original, mime, len(data),
                     hashlib.sha256(data).hexdigest(), g.employee_id),
                ).fetchone()
            return {"attachment_id": result["id"], "name": original}, 201
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @app.get("/repairs/<int:rid>/attachments/<int:aid>", endpoint="attachment_download")
    def attachment_download(rid, aid):
        with connect() as db:
            record = db.execute(
                """SELECT stored_name,original_name,mime_type FROM repair_attachments
                   WHERE id=%s AND repair_id=%s""", (aid, rid)).fetchone()
        if not record:
            abort(404)
        target = (base / record["stored_name"]).resolve()
        if target.parent != base or not target.is_file():
            abort(404)
        response = send_file(target, mimetype=record["mime_type"],
                             as_attachment=True, download_name=record["original_name"])
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
