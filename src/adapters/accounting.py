import sqlite3
from ..transform import fingerprint


class TemporaryFailure(ConnectionError):
    pass


class Conflict(ValueError):
    pass


class Accounting:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.fail_before = 0
        self.fail_after = 0
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS invoices (
                shipment_id TEXT PRIMARY KEY, invoice_id TEXT UNIQUE NOT NULL,
                order_id TEXT NOT NULL, sku TEXT NOT NULL, quantity INTEGER NOT NULL,
                fingerprint TEXT NOT NULL, invoice_status TEXT NOT NULL DEFAULT 'issued',
                payment_status TEXT NOT NULL DEFAULT 'unpaid', version INTEGER NOT NULL DEFAULT 1
            );
        """)

    def close(self):
        self.db.close()

    def create_invoice(self, data):
        if self.fail_before:
            self.fail_before -= 1
            raise TemporaryFailure("accounting unavailable before write")
        digest = fingerprint(data)
        with self.db:
            row = self.db.execute("SELECT * FROM invoices WHERE shipment_id=?",
                                  (data["shipment_id"],)).fetchone()
            if row:
                if row["fingerprint"] != digest:
                    raise Conflict("shipment already invoiced with different facts")
                return dict(row)
            self.db.execute("""INSERT INTO invoices
                (shipment_id,invoice_id,order_id,sku,quantity,fingerprint) VALUES (?,?,?,?,?,?)""",
                (data["shipment_id"], "INV-" + data["shipment_id"], data["order_id"],
                 data["sku"], data["quantity"], digest))
        if self.fail_after:
            self.fail_after -= 1
            raise TemporaryFailure("timeout after invoice committed; outcome unknown")
        return self.get(data["shipment_id"])

    def get(self, shipment_id):
        row = self.db.execute("SELECT * FROM invoices WHERE shipment_id=?", (shipment_id,)).fetchone()
        return dict(row) if row else None

    def invoices(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM invoices ORDER BY shipment_id")]

    def mark_paid(self, shipment_id):
        with self.db:
            cursor = self.db.execute("""UPDATE invoices SET payment_status='paid', version=version+1
                WHERE shipment_id=? AND payment_status!='paid'""", (shipment_id,))
            if self.get(shipment_id) is None:
                raise ValueError("unknown accounting shipment")
            return cursor.rowcount
