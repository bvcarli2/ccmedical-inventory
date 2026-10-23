"""Read-only PostgreSQL dashboard/reporting bridge for local staging.

No SQLite connections are used. Writes are handled separately.
Render deployment remains blocked by app.py.
"""
from flask import request, render_template_string
import psycopg
from psycopg.rows import dict_row

READ_ENDPOINTS={"index","unit","cores","repairs","repair_detail"}

def install_dashboard_routes(app, database_url, inventory_template,
                             core_template, repair_template, stages, repair_context):
    def connect():
        return psycopg.connect(database_url,row_factory=dict_row)

    @app.before_request
    def dashboard_dispatch():
        if request.endpoint not in READ_ENDPOINTS or request.method != "GET":
            return None
        if request.endpoint=="index":
            view=request.args.get("view","Active")
            q=request.args.get("q","").strip()
            try:
                page=max(1,int(request.args.get("page","1")))
            except ValueError:
                return "Invalid page",400
            where=[];args=[]
            if view=="Active":
                where.append("status IN ('Active','Rental')")
            elif view!="All":
                where.append("status=%s");args.append(view)
            if q:
                where.append("(product_raw ILIKE %s OR serial ILIKE %s OR notes ILIKE %s OR location_raw ILIKE %s)")
                args.extend(["%"+q+"%"]*4)
            clause=" WHERE "+" AND ".join(where) if where else ""
            with connect() as db:
                stats=db.execute("SELECT COUNT(*) n,COUNT(cost) priced,COALESCE(SUM(cost),0) val FROM units"+clause,args).fetchone()
                overview={r["status"]:r["n"] for r in db.execute("SELECT status,COUNT(*) n FROM units GROUP BY status")}
                rows=db.execute("SELECT * FROM units"+clause+" ORDER BY id LIMIT 300 OFFSET %s",args+[(page-1)*300]).fetchall()
            return render_template_string(inventory_template,view=view,q=q,page=page,total=stats["n"],
                priced=stats["priced"],value=stats["val"],units=rows,unit=None,
                overview=overview,all_count=sum(overview.values()))
        if request.endpoint=="unit":
            with connect() as db:
                unit=db.execute("SELECT * FROM units WHERE id=%s",(request.view_args["uid"],)).fetchone()
            if not unit:
                return "Not found",404
            return render_template_string(inventory_template,view=unit["status"],unit=unit,
                                          overview={},all_count=0)
        if request.endpoint=="cores":
            with connect() as db:
                rows=db.execute("""SELECT c.*,COALESCE((SELECT SUM(labor_cost+parts_cost+other_cost)
                    FROM core_transactions WHERE core_id=c.id),0) repair_cost
                    FROM core_items c ORDER BY c.id DESC""").fetchall()
            return render_template_string(core_template,rows=rows,detail=None,transactions=[],total=0)
        if request.endpoint=="repairs":
            stage=request.args.get("stage","")
            q=request.args.get("q","").strip()
            if stage and stage not in stages:
                stage=""
            where=[];args=[]
            if stage:
                where.append("stage=%s");args.append(stage)
            if q:
                where.append("(rma ILIKE %s OR customer ILIKE %s OR serial ILIKE %s OR location ILIKE %s OR model ILIKE %s OR part_number ILIKE %s)")
                args.extend(["%"+q+"%"]*6)
            clause=" WHERE "+" AND ".join(where) if where else ""
            with connect() as db:
                rows=db.execute("""SELECT * FROM customer_repairs"""+clause+
                    " ORDER BY CASE WHEN stage='Completed' THEN 1 ELSE 0 END, stage_started_at ASC, id DESC",args).fetchall()
                counts={r["stage"]:r["n"] for r in db.execute("SELECT stage,COUNT(*) n FROM customer_repairs GROUP BY stage")}
            return render_template_string(repair_template,mode="list",rows=[repair_context(r) for r in rows],
                                          counts=counts,stages=stages,selected=stage,q=q)
        if request.endpoint=="repair_detail":
            rid=request.view_args["rid"]
            with connect() as db:
                repair=db.execute("SELECT * FROM customer_repairs WHERE id=%s",(rid,)).fetchone()
                if repair is None:
                    return "RMA not found",404
                history=db.execute("SELECT * FROM customer_repair_history WHERE repair_id=%s ORDER BY id DESC",(rid,)).fetchall()
            return render_template_string(repair_template,mode="detail",r=repair_context(repair),
                                          history=history,stages=stages)
