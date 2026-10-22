"""Transaction-scoped inventory audit records."""
from flask import g, has_request_context

def audit(db, action, kind, record_id, detail=""):
    actor = getattr(g, "employee_id", None) if has_request_context() else None
    db.execute(
        "INSERT INTO activity_log (employee_id, action, entity_type, entity_id, detail) VALUES (%s,%s,%s,%s,%s)",
        (actor, action, kind, str(record_id), detail[:240]),
    )
