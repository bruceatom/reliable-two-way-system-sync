"""Compare actual business facts and mirrored accounting state, never just call counts."""
from .transform import to_invoice


def reconcile(warehouse, accounting, journal):
    shipments = {row["shipment_id"]: row for row in warehouse.shipments()}
    invoices = {row["shipment_id"]: row for row in accounting.invoices()}
    exceptions = []
    matched = 0
    for key in sorted(shipments.keys() | invoices.keys()):
        reasons = []
        source, invoice = shipments.get(key), invoices.get(key)
        if source is None:
            reasons.append("orphan_invoice")
        elif invoice is None:
            reasons.append("missing_invoice")
        else:
            try:
                expected = to_invoice(source)
                for field, value in expected.items():
                    if invoice[field] != value:
                        reasons.append("facts_mismatch:" + field)
            except ValueError as exc:
                reasons.append("invalid_source:" + str(exc))
            mirror = warehouse.get(key)
            for field in ("invoice_id", "invoice_status", "payment_status"):
                if mirror[field] != invoice[field]:
                    reasons.append("status_mismatch:" + field)
            if mirror["accounting_version"] != invoice["version"]:
                reasons.append("status_version_mismatch")
        if reasons:
            exceptions.append({"shipment_id": key, "reasons": reasons})
        else:
            matched += 1
    states = journal.summary()
    unresolved = sum(count for state, count in states.items() if state != "done")
    return {"warehouse_shipments": len(shipments), "accounting_invoices": len(invoices),
            "matched": matched, "exception_count": len(exceptions), "exceptions": exceptions,
            "event_states": states, "unresolved_events": unresolved,
            "healthy": not exceptions and unresolved == 0}
