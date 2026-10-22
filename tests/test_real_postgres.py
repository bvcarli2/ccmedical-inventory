"""Real PostgreSQL integration tests using disposable CI database only.

The database must be empty. Requires CCM_TEST_DATABASE_URL. Never point at Render.
"""
import os
import unittest
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from postgres_inventory_repairs import (
 allocate_code,create_batch,receive_unit,receive_core,receive_customer_repair,
 transfer_unit,record_unit_repair,update_repair_stage,convert_core_to_stock,
)
from postgres_staging_routes import STAGES

URL=os.environ.get("CCM_TEST_DATABASE_URL")
@unittest.skipUnless(URL,"CCM_TEST_DATABASE_URL not set")
class RealPostgresTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.db=psycopg.connect(URL,row_factory=dict_row)
  tables=cls.db.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public'").fetchone()["n"]
  if tables:
   raise RuntimeError("Refusing to run integration tests against a nonempty schema")
  cls.db.execute(Path("migration/staging_schema.sql").read_text())
  cls.db.execute(Path("migration/product_schema.sql").read_text())
  cls.db.commit()

 @classmethod
 def tearDownClass(cls):
  cls.db.close()

 def test_inventory_repairs_and_core_conversion(self):
  db=self.db
  row=dict(manufacturer="GE",product="Venue",serial="TEST-001",cost_value=100.,
           notes="sample",location="A1",condition="Used",supplier="Synthetic",
           received_date="2026-10-22",customer="Test",rma="TEST-RMA-1",
           technician="Tester")
  with db.transaction():
   batch=create_batch(db,1)
   cc=allocate_code(db,"CC")
   uid=receive_unit(db,row,cc,batch,"VENUE")
   self.assertEqual(db.execute("SELECT cost FROM units WHERE id=%s",(uid,)).fetchone()["cost"],100.)
   transfer_unit(db,uid,"B2","move")
   self.assertEqual(db.execute("SELECT physical_location FROM units WHERE id=%s",(uid,)).fetchone()["physical_location"],"B2")
   rid=record_unit_repair(db,uid,"Inspection","test")
   self.assertGreater(rid,0)
   cr=allocate_code(db,"CR")
   core_id=receive_core(db,row,cr)
   db.execute("UPDATE core_items SET stage='Ready for Stock',qc_result='Passed' WHERE id=%s",(core_id,))
   db.execute("""INSERT INTO core_transactions(core_id,performed_at,description,labor_cost,parts_cost,other_cost)
                 VALUES (%s,'now','repair',10,5,0)""",(core_id,))
   converted_code,total,new_uid=convert_core_to_stock(db,core_id,lambda x:x.upper())
   self.assertEqual(total,115.)
   self.assertNotEqual(new_uid,uid)
   self.assertEqual(db.execute("SELECT converted_unit_id FROM core_items WHERE id=%s",(core_id,)).fetchone()["converted_unit_id"],new_uid)
   rp=allocate_code(db,"RP")
   repair_id=receive_customer_repair(db,row,rp)
   changed=update_repair_stage(db,repair_id,"QC",STAGES,{"customer":"Test"},"moved")
   self.assertTrue(changed)
   self.assertEqual(db.execute("SELECT count(*) AS n FROM customer_repair_history WHERE repair_id=%s",(repair_id,)).fetchone()["n"],2)
  self.assertEqual(db.execute("SELECT count(*) AS n FROM units").fetchone()["n"],2)

 def test_product_catalog_compatibility_and_audit(self):
  db=self.db
  with db.transaction():
   probe=db.execute("""INSERT INTO products(category,manufacturer,model,display_name)
                      VALUES ('PRB','GE','L4-20','GE L4-20') RETURNING id""").fetchone()["id"]
   system=db.execute("""INSERT INTO products(category,manufacturer,model,display_name)
                       VALUES ('SYS','GE','Venue','GE Venue') RETURNING id""").fetchone()["id"]
   db.execute("""INSERT INTO product_part_numbers(product_id,part_number)
                 VALUES (%s,%s)""",(probe,"TEST-PART"))
   db.execute("""INSERT INTO product_compatibility(probe_id,system_id)
                 VALUES (%s,%s)""",(probe,system))
   db.execute("""INSERT INTO product_audit(product_id,action,actor)
                 VALUES (%s,'Test insert','Test runner')""",(probe,))
   row=db.execute("""SELECT p.model,STRING_AGG(pn.part_number, ', ') AS parts
                     FROM products p LEFT JOIN product_part_numbers pn ON pn.product_id=p.id
                     WHERE p.id=%s GROUP BY p.id""",(probe,)).fetchone()
   self.assertEqual(row["parts"],"TEST-PART")
   self.assertEqual(db.execute("SELECT count(*) AS n FROM product_compatibility").fetchone()["n"],1)
   self.assertEqual(db.execute("SELECT count(*) AS n FROM product_audit").fetchone()["n"],1)

 def test_product_flask_routes_postgresql(self):
  # Use the same isolated disposable PostgreSQL DB as the integration tests.
  os.environ["CCM_PG_STAGING"]="1"
  os.environ["CCM_STAGING_DATABASE_URL"]=URL
  try:
   from app import app
   app.config["TESTING"]=True
   with app.test_client() as client:
    page=client.get("/products")
    self.assertEqual(page.status_code,200,page.data[:300])
    self.assertIn(b"Product Database",page.data)
    response=client.post("/products/request",data={
     "request_type":"New Product","category":"PRB",
     "manufacturer":"GE","model":"TEST-REQUEST-PROBE",
     "notes":"Synthetic test request"})
    self.assertEqual(response.status_code,302)
    self.assertEqual(self.db.execute("SELECT count(*) AS n FROM product_requests WHERE model='TEST-REQUEST-PROBE'").fetchone()["n"],1)
    with client.session_transaction() as sess:
     sess["product_admin"]=True
    admin=client.get("/products/admin")
    self.assertEqual(admin.status_code,200,admin.data[:300])
    probe=self.db.execute("SELECT id FROM products WHERE model='L4-20'").fetchone()
    if probe:
     # The Flask route uses a separate connection; commit fixture rows first.
     self.db.commit()
     detail=client.get("/products/"+str(probe["id"]))
     self.assertEqual(detail.status_code,200,detail.data[:300])
  finally:
   os.environ.pop("CCM_PG_STAGING",None)
   os.environ.pop("CCM_STAGING_DATABASE_URL",None)

 def test_dashboard_and_reporting_flask_routes(self):
  os.environ["CCM_PG_STAGING"]="1"
  os.environ["CCM_STAGING_DATABASE_URL"]=URL
  try:
   from app import app
   app.config["TESTING"]=True
   with app.test_client() as client:
    for path in ("/","/?view=All","/?q=Venue","/cores","/repairs",
                 "/repairs?stage=QC","/repairs?q=TEST"):
     response=client.get(path)
     self.assertEqual(response.status_code,200,(path,response.data[:300]))
    self.assertEqual(client.get("/unit/999999999").status_code,404)
    self.assertEqual(client.get("/repairs/999999999").status_code,404)
    uid=self.db.execute("SELECT id FROM units ORDER BY id LIMIT 1").fetchone()
    if uid:
     response=client.get("/unit/"+str(uid["id"]))
     self.assertEqual(response.status_code,200,response.data[:300])
    rid=self.db.execute("SELECT id FROM customer_repairs ORDER BY id LIMIT 1").fetchone()
    if rid:
     response=client.get("/repairs/"+str(rid["id"]))
     self.assertEqual(response.status_code,200,response.data[:300])
  finally:
   os.environ.pop("CCM_PG_STAGING",None)
   os.environ.pop("CCM_STAGING_DATABASE_URL",None)

 def test_pg_employee_security_roles_and_csrf(self):
  from flask import Flask
  from werkzeug.security import generate_password_hash
  from postgres_employee_security import register_pg_security
  self.db.execute(Path("migration/employee_schema.sql").read_text())
  self.db.execute("""INSERT INTO employee_users(email,password_hash,role)
      VALUES (%s,%s,'viewer') ON CONFLICT (LOWER(email)) DO UPDATE
      SET password_hash=EXCLUDED.password_hash,role='viewer',active=TRUE""",
      ("viewer@example.test",generate_password_hash("very-long-test-password")))
  self.db.execute("""INSERT INTO employee_users(email,password_hash,role)
      VALUES (%s,%s,'employee') ON CONFLICT (LOWER(email)) DO UPDATE
      SET password_hash=EXCLUDED.password_hash,role='employee',active=TRUE""",
      ("worker@example.test",generate_password_hash("very-long-test-password")))
  self.db.commit()
  site=Flask(__name__)
  site.secret_key="only-a-synthetic-ci-secret"
  @site.get("/")
  def index():
   return "dashboard"
  @site.post("/transfer/<int:uid>")
  def transfer(uid):
   return "changed"
  register_pg_security(site,URL)
  self.assertEqual(site.config["MAX_CONTENT_LENGTH"],10*1024*1024)
  with site.test_client() as client:
   self.assertEqual(client.get("/").status_code,302)
   login=client.get("/login")
   self.assertEqual(login.status_code,200)
   self.assertEqual(login.headers["X-Frame-Options"],"DENY")
   self.assertIn("geolocation=()",login.headers["Permissions-Policy"])
   with client.session_transaction() as sess:
    token=sess["_csrf_token"]
   self.assertEqual(client.post("/login",data={"email":"viewer@example.test",
       "password":"very-long-test-password"}).status_code,403)
   self.assertEqual(client.post("/login",data={"_csrf_token":token,
       "email":"viewer@example.test","password":"very-long-test-password"}).status_code,302)
   self.assertEqual(client.get("/").status_code,200)
   self.assertEqual(client.get("/products/admin").status_code,403)
   with client.session_transaction() as sess:
    token=sess["_csrf_token"]
   self.assertEqual(client.post("/transfer/1",data={"_csrf_token":token}).status_code,403)
   self.assertEqual(client.post("/logout",data={}).status_code,403)
   self.assertEqual(client.post("/logout",data={"_csrf_token":token}).status_code,302)
   self.assertEqual(client.get("/").status_code,302)
   with client.session_transaction() as sess:
    token=sess["_csrf_token"]
   self.assertEqual(client.post("/login",data={"_csrf_token":token,
       "email":"worker@example.test","password":"very-long-test-password"}).status_code,302)
   self.assertEqual(client.post("/transfer/1",data={}).status_code,403)
   with client.session_transaction() as sess:
    token=sess["_csrf_token"]
   self.assertEqual(client.post("/transfer/1",data={"_csrf_token":token}).status_code,200)
   self.assertEqual(client.get("/products/admin").status_code,403)
   self.assertEqual(client.post("/logout",data={"_csrf_token":token}).status_code,302)
   # Repeated invalid passwords are blocked by shared PostgreSQL state.
   self.assertEqual(client.get("/login").status_code,200)
   for _ in range(5):
    with client.session_transaction() as sess:
     token=sess["_csrf_token"]
    self.assertEqual(client.post("/login",data={"_csrf_token":token,
        "email":"blocked@example.test","password":"invalid"}).status_code,200)
   with client.session_transaction() as sess:
    token=sess["_csrf_token"]
   self.assertEqual(client.post("/login",data={"_csrf_token":token,
       "email":"blocked@example.test","password":"invalid"}).status_code,429)

 def test_security_allowlist_covers_all_write_routes(self):
  # Fail CI if a new mutating Flask endpoint is added without a role decision.
  from postgres_employee_security import WRITE_ROLES
  os.environ["CCM_PG_STAGING"]="1"
  os.environ["CCM_STAGING_DATABASE_URL"]=URL
  try:
   from app import app
   missing=[]
   for rule in app.url_map.iter_rules():
    if "POST" in rule.methods and rule.endpoint not in (
       "pg_employee_login","pg_employee_logout") and rule.endpoint not in WRITE_ROLES:
     missing.append((rule.rule,rule.endpoint))
   self.assertEqual(missing,[],"Unreviewed POST endpoints: "+str(missing))
  finally:
   os.environ.pop("CCM_PG_STAGING",None)
   os.environ.pop("CCM_STAGING_DATABASE_URL",None)


 def test_postgresql_staging_http_workflows(self):
  os.environ["CCM_PG_STAGING"]="1"
  os.environ["CCM_STAGING_DATABASE_URL"]=URL
  from app import app
  from postgres_staging_routes import receive, transfer, repair, update, core
  os.environ["CCM_STAGING_DATABASE_URL"]=URL
  app.config["TESTING"]=True
  try:
   with app.test_client() as client:
    form={"type[]":["CC","CR","RP"],"manufacturer[]":["GE"]*3,
      "product[]":["Venue","Probe","Venue"],"serial[]":["HTTP-1","HTTP-2","HTTP-3"],
      "location[]":["A1"]*3,"supplier[]":["Test"]*3,"cost[]":["100","40","0"],
      "customer[]":["","","Test Hospital"],"rma[]":["","","HTTP-RMA-1"]}
    response=client.post("/inventory/new",data=form)
    self.assertEqual(response.status_code,201,response.data[:300])
    cc,cr,rp=response.json["inventory_codes"]
    uid=self.db.execute("SELECT id FROM units WHERE inventory_code=%s",(cc,)).fetchone()["id"]
    cid=self.db.execute("SELECT id FROM core_items WHERE inventory_code=%s",(cr,)).fetchone()["id"]
    rid=self.db.execute("SELECT id FROM customer_repairs WHERE inventory_code=%s",(rp,)).fetchone()["id"]
    self.assertEqual(client.post("/transfer/"+str(uid),data={"location":"B2"}).status_code,200)
    self.assertEqual(client.post("/repair/"+str(uid),data={"type":"Inspection"}).status_code,201)
    self.assertEqual(client.post("/repairs/"+str(rid)+"/update",data={"stage":"QC"}).status_code,200)
    details=self.db.execute("SELECT customer,model,serial FROM customer_repairs WHERE id=%s",(rid,)).fetchone()
    self.assertEqual(details["customer"],"Test Hospital")
    self.assertEqual(details["model"],"Venue")
    self.assertEqual(details["serial"],"HTTP-3")
    self.assertEqual(client.post("/cores/"+str(cid),data={"action":"convert"}).status_code,400)
    self.assertEqual(client.post("/cores/"+str(cid),data={"action":"stage","stage":"QC"}).status_code,200)
    self.assertEqual(client.post("/cores/"+str(cid),data={"action":"qc","result":"Passed"}).status_code,400)
    self.assertEqual(client.post("/cores/"+str(cid),data={"action":"transaction",
      "description":"Repair","labor_method":"hourly","hours":"2","rate":"15",
      "parts":"5","other":"2"}).status_code,201)
    self.assertEqual(client.post("/cores/"+str(cid),data={"action":"qc",
      "result":"Passed","technician":"Test"}).status_code,200)
    result=client.post("/cores/"+str(cid),data={"action":"convert"})
    self.assertEqual(result.status_code,201,result.data[:300])
    self.assertEqual(result.json["cost"],77.)
    self.assertEqual(client.post("/cores/"+str(cid),data={"action":"convert"}).status_code,400)
  finally:
   os.environ.pop("CCM_PG_STAGING",None)
   os.environ.pop("CCM_STAGING_DATABASE_URL",None)

 def test_rollback(self):
  db=self.db
  before=db.execute("SELECT count(*) AS n FROM units").fetchone()["n"]
  try:
   with db.transaction():
    db.execute("INSERT INTO units(product_raw) VALUES ('ROLLBACK TEST')")
    raise ValueError("simulate failed receiving")
  except ValueError:
   pass
  self.assertEqual(db.execute("SELECT count(*) AS n FROM units").fetchone()["n"],before)

if __name__=="__main__":
 unittest.main()
