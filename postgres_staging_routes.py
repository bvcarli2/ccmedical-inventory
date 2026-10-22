"""Local-only PostgreSQL staging route bridge.

Intercepts selected existing Flask endpoints; all other requests fail closed.
Do not deploy this bridge on Render or expose the staging server to a network.
"""
import os
from datetime import date
from flask import request, redirect, url_for, flash, abort
import psycopg
from psycopg.rows import dict_row
from psycopg.errors import UniqueViolation
from postgres_inventory_repairs import (
    allocate_code, create_batch, receive_unit, receive_core,
    receive_customer_repair, transfer_unit, record_unit_repair,
    update_repair_stage, convert_core_to_stock,
)

ALLOWED = {"inventory_new", "transfer", "repair", "repair_update", "core_detail"}
STAGES = ["Open Repairs", "Evaled", "In Repair", "QC", "Ready to Ship", "Completed"]

def connect():
    url = os.environ.get("CCM_STAGING_DATABASE_URL")
    if not url:
        raise RuntimeError("CCM_STAGING_DATABASE_URL is required in staging mode")
    return psycopg.connect(url, row_factory=dict_row)

def install_staging_routes(app, norm, classify, amount):
    @app.before_request
    def pg_staging_dispatch():
        # Staging only, never a cloud entrypoint. Flask app also blocks Render.
        # Product routes use the PostgreSQL adapter registered by app.py.
        # Staging is local-only and remains blocked on Render.
        if request.method == "GET" and request.endpoint in {"index","unit","cores","repairs","repair_detail"}:
            return None
        if request.path == "/products" or request.path.startswith("/products/"):
            return None
        if request.endpoint not in ALLOWED or request.method != "POST":
            abort(404, "Endpoint not available in PostgreSQL staging mode")
        if request.endpoint == "inventory_new":
            return receive(request.form, norm, amount)
        if request.endpoint == "transfer":
            return transfer(request.view_args["uid"], classify, norm)
        if request.endpoint == "repair":
            return repair(request.view_args["uid"])
        if request.endpoint == "repair_update":
            return update(request.view_args["rid"])
        if request.endpoint == "core_detail":
            return core(request.view_args["cid"], norm, amount)

def receive(form, norm, amount):
    fields = ("type","manufacturer","product","serial","location","supplier",
              "cost","condition","notes","customer","rma","technician","received_date")
    length = max((len(form.getlist(k+"[]")) for k in fields), default=0)
    rows = []
    for i in range(length):
        row = {k:(form.getlist(k+"[]")[i].strip() if i < len(form.getlist(k+"[]")) else "") for k in fields}
        if not any(row.values()):
            continue
        if row["type"] not in ("CC","CR","RP") or not row["product"]:
            return "Valid type and product are required",400
        if row["type"] == "RP" and (not row["customer"] or not row["rma"]):
            return "Customer and RMA are required",400
        try:
            if row["received_date"]:
                date.fromisoformat(row["received_date"])
            row["cost_value"] = amount(row["cost"], "Purchase cost")
        except ValueError as e:
            return str(e),400
        rows.append(row)
    if not rows:
        return "Enter at least one item",400
    try:
        with connect() as db:
            batch = create_batch(db,len(rows))
            codes = []
            for row in rows:
                code = allocate_code(db,row["type"])
                codes.append(code)
                if row["type"] == "CC":
                    receive_unit(db,row,code,batch,norm(row["product"]))
                elif row["type"] == "CR":
                    receive_core(db,row,code)
                else:
                    receive_customer_repair(db,row,code)
        return {"received":len(codes),"inventory_codes":codes},201
    except UniqueViolation:
        return "Duplicate inventory code or RMA",409

def transfer(uid, classify, norm):
    status, location = classify(norm(request.form.get("location")))
    if status != "Active":
        return "A recognized physical location is required",400
    try:
        with connect() as db:
            transfer_unit(db,uid,location,request.form.get("note",""))
        return {"transferred":uid,"location":location}
    except ValueError as e:
        return str(e),400

def repair(uid):
    try:
        with connect() as db:
            repair_id = record_unit_repair(
                db,uid,request.form.get("type"),request.form.get("notes"))
        return {"repair_id":repair_id},201
    except ValueError as e:
        return str(e),400

def update(rid):
    stage = request.form.get("stage","")
    fields = {k:request.form.get(k,"").strip() for k in
              ("customer","manufacturer","model","part_number","serial",
               "location","technician","notes")}
    try:
        with connect() as db:
            changed = update_repair_stage(
                db,rid,stage,STAGES,fields,request.form.get("change_note",""))
        return {"repair_id":rid,"stage_changed":changed}
    except ValueError as e:
        return str(e),400

def core(cid, norm, amount):
    form = request.form
    action = form.get("action","")
    try:
        with connect() as db:
            item = db.execute(
                "SELECT * FROM core_items WHERE id=%s FOR UPDATE",(cid,)
            ).fetchone()
            if not item:
                raise ValueError("Core not found")
            if item["stage"] == "Converted to Stock":
                raise ValueError("Core already converted")
            if action == "convert":
                code,cost,uid = convert_core_to_stock(db,cid,norm)
                return {"inventory_code":code,"cost":cost,"unit_id":uid},201
            if action == "stage":
                stage = form.get("stage","")
                if stage not in ("Needs Repair","In Repair","QC"):
                    raise ValueError("Invalid core stage")
                db.execute(
                    "UPDATE core_items SET stage=%s,qc_result=NULL WHERE id=%s",
                    (stage,cid))
                return {"core_id":cid,"stage":stage}
            if action == "qc":
                result = form.get("result","")
                technician = form.get("technician","").strip()
                if item["stage"] != "QC" or result not in ("Passed","Failed") or not technician:
                    raise ValueError("QC requires QC stage, result and technician")
                stage = "Ready for Stock" if result == "Passed" else "In Repair"
                db.execute(
                    """UPDATE core_items SET qc_result=%s,qc_technician=%s,
                    qc_date=%s,qc_notes=%s,stage=%s WHERE id=%s""",
                    (result,technician,date.today().isoformat(),
                     form.get("notes","").strip(),stage,cid))
                return {"core_id":cid,"stage":stage}
            if action == "transaction":
                method = form.get("labor_method","manual")
                if method not in ("manual","hourly"):
                    raise ValueError("Invalid labor method")
                hours = amount(form.get("hours"),"Hours")
                rate = amount(form.get("rate"),"Hourly rate")
                labor = amount(form.get("labor"),"Labor cost") if method == "manual" else round(hours*rate,2)
                parts = amount(form.get("parts"),"Parts cost")
                other = amount(form.get("other"),"Other cost")
                description = form.get("description","").strip()
                if not description:
                    raise ValueError("Description required")
                db.execute(
                    """INSERT INTO core_transactions
                    (core_id,performed_at,technician,description,labor_method,
                     labor_hours,labor_rate,labor_cost,parts_cost,other_cost)
                    VALUES (%s,CURRENT_TIMESTAMP,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (cid,form.get("technician","").strip(),description,method,
                     hours,rate,labor,parts,other))
                return {"core_id":cid,"transaction_recorded":True},201
            raise ValueError("Unknown core action")
    except ValueError as e:
        return str(e),400
