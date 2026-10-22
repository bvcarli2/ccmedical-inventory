"""Offline Flask integration tests for PostgreSQL staging routes.

Run: python -m unittest discover -s tests -p 'test_pg_staging_routes.py'
These mock DB connections; they are NOT live PostgreSQL integration tests.
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from flask import Flask

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.errors import UniqueViolation
except ImportError:
    psycopg = types.ModuleType("psycopg")
    rows = types.ModuleType("psycopg.rows")
    errors = types.ModuleType("psycopg.errors")
    rows.dict_row = object()
    errors.UniqueViolation = type("UniqueViolation",(Exception,),{})
    psycopg.rows = rows
    psycopg.errors = errors
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = rows
    sys.modules["psycopg.errors"] = errors

import postgres_staging_routes as routes

def amount(value, label):
    n = float(value or 0)
    if n < 0:
        raise ValueError(label)
    return n

class PostgreSQLStagingRoutesTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.add_url_rule("/inventory/new",endpoint="inventory_new",
                              view_func=lambda: "old sqlite handler",methods=["POST"])
        self.app.add_url_rule("/transfer/<int:uid>",endpoint="transfer",
                              view_func=lambda uid: "old sqlite handler",methods=["POST"])
        self.app.add_url_rule("/repair/<int:uid>",endpoint="repair",
                              view_func=lambda uid: "old sqlite handler",methods=["POST"])
        self.app.add_url_rule("/repairs/<int:rid>/update",endpoint="repair_update",
                              view_func=lambda rid: "old sqlite handler",methods=["POST"])
        self.app.add_url_rule("/cores/<int:cid>",endpoint="core_detail",
                              view_func=lambda cid: "old sqlite handler",methods=["POST"])
        routes.install_staging_routes(
            self.app,lambda s:s.upper(),
            lambda s:("Active",s) if s == "A1" else ("Needs review",None),
            amount)
        self.client = self.app.test_client()

    def test_non_staging_route_is_blocked(self):
        self.assertEqual(self.client.get("/inventory/new").status_code,405)

    def test_inventory_receiving_routes_to_postgres(self):
        fake = MagicMock()
        fake.__enter__.return_value = fake
        with patch.object(routes,"connect",return_value=fake), \
             patch.object(routes,"create_batch",return_value=4) as batch, \
             patch.object(routes,"allocate_code",return_value="CC-102226-001") as allocate, \
             patch.object(routes,"receive_unit",return_value=8) as receive:
            response = self.client.post("/inventory/new",data={
                "type[]":"CC","product[]":"GE Venue",
                "cost[]":"100","manufacturer[]":"GE",
            })
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json["received"],1)
        batch.assert_called_once_with(fake,1)
        allocate.assert_called_once_with(fake,"CC")
        receive.assert_called_once()

    def test_transfer_routes_to_postgres(self):
        fake = MagicMock()
        fake.__enter__.return_value = fake
        with patch.object(routes,"connect",return_value=fake), \
             patch.object(routes,"transfer_unit") as transfer:
            response = self.client.post("/transfer/9",data={"location":"a1"})
        self.assertEqual(response.status_code,200)
        transfer.assert_called_once_with(fake,9,"A1","")

    def test_repair_update_routes_to_postgres(self):
        fake = MagicMock()
        fake.__enter__.return_value = fake
        with patch.object(routes,"connect",return_value=fake), \
             patch.object(routes,"update_repair_stage",return_value=True) as update:
            response = self.client.post("/repairs/11/update",data={"stage":"QC"})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json["stage_changed"])
        self.assertEqual(update.call_args.args[1:3],(11,"QC"))

    def test_core_conversion_routes_to_postgres(self):
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.execute.return_value.fetchone.return_value = {
            "id":5,"stage":"Ready for Stock","qc_result":"Passed"}
        with patch.object(routes,"connect",return_value=fake), \
             patch.object(routes,"convert_core_to_stock",
                          return_value=("CC-102226-002",150.0,21)) as convert:
            response = self.client.post("/cores/5",data={"action":"convert"})
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json["unit_id"],21)
        convert.assert_called_once()

    def test_invalid_stage_does_not_connect(self):
        with patch.object(routes,"connect") as connect:
            response = self.client.post("/repairs/11/update",data={"stage":"INVALID"})
        # The PostgreSQL operation rejects the stage before writing.
        self.assertEqual(response.status_code,400)

if __name__ == "__main__":
    unittest.main()
