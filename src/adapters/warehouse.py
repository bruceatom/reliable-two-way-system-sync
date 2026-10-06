import json
import sqlite3
from ..transform import canonical


class Warehouse:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS shipments (
                shipment_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                invoice_id TEXT, invoice_status TEXT, payment_status TEXT,
                accounting_version INTEGER NOT NULL DEFAULT 0
            );
        """)

    def close(self):
        self.db.close()

    def add_shipment(self, data):
        # Source may contain bad data; validation belongs at the integration boundary.
        with self.db:
            self.db.execute("INSERT INTO shipments(shipment_id,payload) VALUES (?,?)",
                            (data["shipment_id"], canonical(data)))

    def shipments(self):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM shipments ORDER BY shipment_id")]

    def get(self, shipment_id):
        self.db.row_factory = sqlite3.Row
        row = self.db.execute("SELECT * FROM shipments WHERE shipment_id=?", (shipment_id,)).fetchone()
        return dict(row) if row else None

    def apply_status(self, data):
        with self.db:
            current = self.get(data["shipment_id"])
            if current is None:
                raise ValueError("unknown warehouse shipment")
            if current["invoice_id"] and current["invoice_id"] != data["invoice_id"]:
                raise ValueError("invoice identifier conflict")
            if data["version"] < current["accounting_version"]:
                return "stale"
            if data["version"] == current["accounting_version"]:
                same = all(current[key] == data[key] for key in
                           ("invoice_id", "invoice_status", "payment_status"))
                if not same:
                    raise ValueError("same accounting version has conflicting values")
                return "duplicate"
            self.db.execute("""UPDATE shipments SET invoice_id=?, invoice_status=?,
                payment_status=?, accounting_version=? WHERE shipment_id=?""",
                (data["invoice_id"], data["invoice_status"], data["payment_status"],
                 data["version"], data["shipment_id"]))
            return "updated"
