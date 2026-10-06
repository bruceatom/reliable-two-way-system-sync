import tempfile
import unittest
from pathlib import Path
from src.cli import open_systems, sample, main
from src.sync import Sync
from src.idempotency import EventConflict
from src.reconciliation import reconcile
from src.validation import InvalidData, shipment
from src.transform import canonical


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.w, self.a, self.j = open_systems(self.path)
        self.sync = Sync(self.w, self.a, self.j)
        self.good = sample("SHP-001")
        self.w.add_shipment(self.good)

    def tearDown(self):
        self.w.close()
        self.a.close()
        self.j.close()
        self.temp.cleanup()

    def send(self, data=None, event_id="event-1"):
        return self.sync.submit(event_id, "warehouse_to_accounting", data or self.good)

    def test_two_way_sync_and_payment_are_reconciled(self):
        self.sync.poll()
        self.a.mark_paid("SHP-001")
        self.sync.poll()
        self.assertEqual("paid", self.w.get("SHP-001")["payment_status"])
        self.assertTrue(reconcile(self.w, self.a, self.j)["healthy"])

    def test_duplicate_event_and_different_event_same_shipment(self):
        self.assertEqual("done", self.send())
        self.assertEqual("done", self.send())
        self.assertEqual("done", self.send(event_id="redelivery-new-id"))
        self.assertEqual(1, len(self.a.invoices()))
        self.assertEqual(1, self.j.get("event-1")["attempts"])

    def test_reused_event_identifier_is_visible_conflict(self):
        self.send()
        changed = dict(self.good, quantity=99)
        with self.assertRaises(EventConflict):
            self.send(changed)
        self.assertEqual("event_conflict", self.j.audit()[-1]["action"])
        self.assertEqual(4, self.a.get("SHP-001")["quantity"])
        self.assertEqual(1, self.j.summary()["conflict"])

    def test_poll_isolates_conflict_and_keeps_processing_other_shipments(self):
        self.sync.poll()
        with self.w.db:
            self.w.db.execute("UPDATE shipments SET payload=? WHERE shipment_id=?",
                              (canonical(dict(self.good, quantity=99)), "SHP-001"))
        self.w.add_shipment(sample("SHP-002"))
        results = self.sync.poll()
        self.assertEqual("conflict", results["shipment-SHP-001"])
        self.assertEqual("done", results["shipment-SHP-002"])
        self.assertEqual(2, len(self.a.invoices()))
        self.assertFalse(reconcile(self.w, self.a, self.j)["healthy"])

    def test_maximum_length_business_identifiers_can_sync(self):
        self.w.add_shipment(sample("S" * 64))
        self.sync.poll()
        self.assertEqual(2, reconcile(self.w, self.a, self.j)["matched"])

    def test_malformed_status_payload_is_quarantined(self):
        self.assertEqual("quarantined", self.sync.submit("malformed", "accounting_to_warehouse", []))

    def test_changed_facts_cannot_overwrite_an_existing_invoice(self):
        self.send()
        self.assertEqual("quarantined", self.send(dict(self.good, quantity=99), "changed"))
        self.assertEqual(4, self.a.get("SHP-001")["quantity"])

    def test_invalid_data_is_quarantined_without_destination_write(self):
        self.assertEqual("quarantined", self.send(dict(self.good, sku="")))
        self.assertEqual([], self.a.invoices())
        self.assertIn("sku", self.j.get("event-1")["error"])

    def test_quantity_validation_rejects_ambiguous_and_impossible_types(self):
        for quantity in (True, 0, -1, 1.5, "4", None, 1_000_001):
            with self.subTest(quantity=quantity), self.assertRaises(InvalidData):
                shipment(dict(self.good, quantity=quantity))

    def test_precommit_failure_recovers(self):
        self.a.fail_before = 1
        self.assertEqual("retry", self.send())
        self.assertEqual([], self.a.invoices())
        self.assertEqual({"event-1": "done"}, self.sync.retry())

    def test_timeout_after_commit_survives_process_restart(self):
        self.a.fail_after = 1
        self.assertEqual("retry", self.send())
        self.assertEqual(1, len(self.a.invoices()))
        self.w.close()
        self.a.close()
        self.j.close()
        self.w, self.a, self.j = open_systems(self.path)
        self.sync = Sync(self.w, self.a, self.j)
        self.assertEqual({"event-1": "done"}, self.sync.retry())
        self.assertEqual(1, len(self.a.invoices()))
        self.assertEqual(2, self.j.get("event-1")["attempts"])

    def test_failure_budget_dead_letters_instead_of_retrying_forever(self):
        self.a.fail_before = 10
        self.send()
        self.sync.retry()
        self.assertEqual({"event-1": "dead_letter"}, self.sync.retry())
        self.assertEqual({}, self.sync.retry())
        self.assertEqual(3, self.j.get("event-1")["attempts"])

    def test_return_updates_cannot_overwrite_warehouse_facts(self):
        self.send()
        malicious = dict(self.a.get("SHP-001"), quantity=999, sku="FOREIGN")
        self.assertEqual("done", self.sync.submit("status-1", "accounting_to_warehouse", malicious))
        self.assertEqual(self.good, self.w.shipments()[0])

    def test_out_of_order_status_cannot_regress_payment(self):
        self.send()
        old = self.a.get("SHP-001")
        self.a.mark_paid("SHP-001")
        self.sync.submit("paid", "accounting_to_warehouse", self.a.get("SHP-001"))
        self.sync.submit("old", "accounting_to_warehouse", old)
        self.assertEqual("paid", self.w.get("SHP-001")["payment_status"])
        self.assertEqual(2, self.w.get("SHP-001")["accounting_version"])

    def test_equal_version_conflicting_status_is_quarantined(self):
        self.sync.poll()
        data = dict(self.a.get("SHP-001"), payment_status="paid")
        self.assertEqual("quarantined", self.sync.submit("conflict", "accounting_to_warehouse", data))

    def test_unknown_shipment_status_is_quarantined(self):
        data = {"shipment_id": "OTHER", "invoice_id": "INV-OTHER", "invoice_status": "issued",
                "payment_status": "unpaid", "version": 1}
        self.assertEqual("quarantined", self.sync.submit("orphan", "accounting_to_warehouse", data))

    def test_reconciliation_detects_actual_fact_drift_and_status_lag(self):
        self.send()
        report = reconcile(self.w, self.a, self.j)
        self.assertFalse(report["healthy"])
        self.assertIn("status_mismatch:invoice_id", report["exceptions"][0]["reasons"])
        self.sync.poll()
        with self.a.db:
            self.a.db.execute("UPDATE invoices SET quantity=999")
        report = reconcile(self.w, self.a, self.j)
        self.assertIn("facts_mismatch:quantity", report["exceptions"][0]["reasons"])
        self.assertEqual(0, report["matched"])

    def test_reconciliation_counts_missing_and_orphan_records(self):
        self.a.create_invoice(dict(shipment_id="ORPHAN", order_id="O", sku="S", quantity=1))
        report = reconcile(self.w, self.a, self.j)
        self.assertEqual(2, report["exception_count"])
        self.assertEqual(0, report["matched"])

    def test_unresolved_event_prevents_false_health_even_when_records_match(self):
        self.sync.poll()
        self.send(dict(self.good, quantity=-5), "bad")
        report = reconcile(self.w, self.a, self.j)
        self.assertEqual(1, report["matched"])
        self.assertEqual(0, report["exception_count"])
        self.assertEqual(1, report["unresolved_events"])
        self.assertFalse(report["healthy"])

    def test_cli_reconcile_returns_nonzero_for_exceptions(self):
        self.assertEqual(1, main(["--data", str(self.path), "reconcile"]))


if __name__ == "__main__":
    unittest.main()
