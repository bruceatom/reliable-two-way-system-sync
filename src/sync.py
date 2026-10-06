import json
from .idempotency import EventConflict
from .adapters.accounting import TemporaryFailure
from .transform import to_invoice, to_warehouse_status
from .validation import identifier


class Sync:
    def __init__(self, warehouse, accounting, journal, max_attempts=3):
        self.warehouse, self.accounting, self.journal = warehouse, accounting, journal
        self.max_attempts = max_attempts

    def submit(self, event_id, direction, payload):
        identifier(event_id, "event_id", max_length=160)
        if direction not in ("warehouse_to_accounting", "accounting_to_warehouse"):
            raise ValueError("unknown direction")
        event = self.journal.ingest(event_id, direction, payload)
        if event["state"] in ("done", "quarantined", "dead_letter"):
            return event["state"]
        return self.process(event)

    def process(self, event):
        event_id = event["event_id"]
        payload = json.loads(event["payload"])
        try:
            if event["direction"] == "warehouse_to_accounting":
                self.accounting.create_invoice(to_invoice(payload))
            else:
                result = self.warehouse.apply_status(to_warehouse_status(payload))
                self.journal.record(event_id, "status_application", result)
        except TemporaryFailure as exc:
            state = "dead_letter" if event["attempts"] + 1 >= self.max_attempts else "retry"
            self.journal.transition(event_id, state, str(exc), attempted=True)
            return state
        except ValueError as exc:
            self.journal.transition(event_id, "quarantined", str(exc), attempted=True)
            return "quarantined"
        # If the process dies here, replay is safe at the destination boundary.
        self.journal.transition(event_id, "done", attempted=True)
        return "done"

    def retry(self):
        return {event["event_id"]: self.process(event) for event in self.journal.recoverable()}

    def poll(self):
        results = {}
        for data in self.warehouse.shipments():
            key = "shipment-" + data["shipment_id"]
            try:
                results[key] = self.submit(key, "warehouse_to_accounting", data)
            except EventConflict:
                results[key] = "conflict"
        for data in self.accounting.invoices():
            key = f"status-{data['shipment_id']}-{data['version']}"
            try:
                results[key] = self.submit(key, "accounting_to_warehouse", data)
            except EventConflict:
                results[key] = "conflict"
        return results
