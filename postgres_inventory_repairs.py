"""PostgreSQL-native inventory and repair data operations.

Staging implementation. Callers must supply a psycopg connection with dict_row.
All mutations are parameterized. The caller controls commit/rollback and
authorization; this module does not open network connections.
"""
from datetime import datetime, timezone, date

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def allocate_code(db, prefix):
    if prefix not in ("CC", "CR", "RP"):
        raise ValueError("Unknown inventory prefix")
    seq = db.execute(
        "INSERT INTO inventory_id_sequence(prefix, created_at) VALUES (%s,%s) RETURNING seq",
        (prefix, now()),
    ).fetchone()["seq"]
    code = f"{prefix}-{date.today():%m%d%y}-{seq:03d}"
    db.execute("UPDATE inventory_id_sequence SET generated_id=%s WHERE seq=%s", (code, seq))
    return code

def create_batch(db, item_count):
    return db.execute(
        "INSERT INTO receiving_batches(created_at,item_count) VALUES (%s,%s) RETURNING id",
        (now(), item_count),
    ).fetchone()["id"]

def receive_unit(db, row, code, batch_id, product_key):
    return db.execute(
        """INSERT INTO units
        (manufacturer,product_raw,product_key,serial,cost,dom,notes,
         location_raw,physical_location,status,condition,review,
         inventory_code,supplier,received_date,entry_batch_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id""",
        (row["manufacturer"],row["product"],product_key,row["serial"],
         row["cost_value"],"",row["notes"],row["location"],row["location"],
         "Active",row["condition"] or "Unspecified","",code,row["supplier"],
         row["received_date"] or date.today().isoformat(),batch_id),
    ).fetchone()["id"]

def receive_core(db, row, code):
    return db.execute(
        """INSERT INTO core_items
        (inventory_code,manufacturer,product_raw,serial,location,supplier,
         original_cost,notes,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (code,row["manufacturer"],row["product"],row["serial"],
         row["location"],row["supplier"],row["cost_value"],row["notes"],now()),
    ).fetchone()["id"]

def receive_customer_repair(db, row, code):
    timestamp = now()
    rid = db.execute(
        """INSERT INTO customer_repairs
        (rma,customer,manufacturer,model,part_number,serial,location,
         received_date,technician,notes,stage,stage_started_at,created_at,
         updated_at,inventory_code)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (row["rma"],row["customer"],row["manufacturer"],row["product"],"",
         row["serial"],row["location"],
         row["received_date"] or date.today().isoformat(),row["technician"],
         row["notes"],"Open Repairs",timestamp,timestamp,timestamp,code),
    ).fetchone()["id"]
    add_repair_history(db,rid,None,"Open Repairs","Received through New Inventory Entry")
    return rid

def add_repair_history(db, rid, previous, stage, note=""):
    db.execute(
        """INSERT INTO customer_repair_history
        (repair_id,old_stage,new_stage,changed_at,note)
        VALUES (%s,%s,%s,%s,%s)""",
        (rid,previous,stage,now(),note),
    )

def transfer_unit(db, uid, location, note):
    unit = db.execute(
        "SELECT physical_location,status FROM units WHERE id=%s FOR UPDATE",
        (uid,),
    ).fetchone()
    if not unit or unit["status"] == "Sold archive":
        raise ValueError("Unit cannot be transferred")
    db.execute(
        """INSERT INTO movements(unit_id,old_location,new_location,note)
        VALUES (%s,%s,%s,%s)""",
        (uid,unit["physical_location"],location,note),
    )
    db.execute("UPDATE units SET physical_location=%s,status='Active' WHERE id=%s",
               (location,uid))

def record_unit_repair(db, uid, repair_type, notes):
    unit = db.execute("SELECT status FROM units WHERE id=%s FOR UPDATE",(uid,)).fetchone()
    if not unit or unit["status"] == "Sold archive":
        raise ValueError("Unit cannot receive a repair")
    return db.execute(
        """INSERT INTO repair_orders(unit_id,repair_type,notes)
        VALUES (%s,%s,%s) RETURNING id""",
        (uid,repair_type,notes),
    ).fetchone()["id"]

def update_repair_stage(db, rid, stage, allowed_stages, fields, note=""):
    if stage not in allowed_stages:
        raise ValueError("Invalid repair stage")
    old = db.execute(
        "SELECT * FROM customer_repairs WHERE id=%s FOR UPDATE",(rid,)
    ).fetchone()
    if not old:
        raise ValueError("RMA not found")
    changed = stage != old["stage"]
    timestamp = now()
    complete = timestamp if changed and stage == "Completed" else (
        None if changed else old["completed_at"])
    db.execute(
        """UPDATE customer_repairs SET customer=%s,manufacturer=%s,model=%s,
        part_number=%s,serial=%s,location=%s,technician=%s,notes=%s,stage=%s,
        stage_started_at=%s,completed_at=%s,updated_at=%s WHERE id=%s""",
        (fields.get("customer") or old["customer"],fields.get("manufacturer") or old["manufacturer"],
         fields.get("model") or old["model"],fields.get("part_number") or old["part_number"],fields.get("serial") or old["serial"],
         fields.get("location") or old["location"],fields.get("technician") or old["technician"],fields.get("notes") or old["notes"],
         stage,timestamp if changed else old["stage_started_at"],complete,timestamp,rid),
    )
    if changed:
        add_repair_history(db,rid,old["stage"],stage,note)
    return changed

def convert_core_to_stock(db, core_id, normalize):
    core = db.execute(
        "SELECT * FROM core_items WHERE id=%s FOR UPDATE",(core_id,)
    ).fetchone()
    if not core or core["stage"] != "Ready for Stock" or core["qc_result"] != "Passed":
        raise ValueError("Core must pass QC before conversion")
    repair_cost = db.execute(
        """SELECT COALESCE(SUM(labor_cost+parts_cost+other_cost),0) AS cost
        FROM core_transactions WHERE core_id=%s""",(core_id,)
    ).fetchone()["cost"]
    code = allocate_code(db,"CC")
    total = round(float(core["original_cost"]) + float(repair_cost),2)
    uid = db.execute(
        """INSERT INTO units
        (manufacturer,product_raw,product_key,serial,cost,notes,location_raw,
         physical_location,status,condition,review,inventory_code,supplier,received_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (core["manufacturer"],core["product_raw"],normalize(core["product_raw"]),
         core["serial"],total,"Converted from "+core["inventory_code"]+"; "+
         (core["notes"] or ""),core["location"],core["location"],"Active",
         "Refurbished","",code,core["supplier"],date.today().isoformat()),
    ).fetchone()["id"]
    db.execute(
        "UPDATE core_items SET stage='Converted to Stock',converted_unit_id=%s WHERE id=%s",
        (uid,core_id),
    )
    return code,total,uid
