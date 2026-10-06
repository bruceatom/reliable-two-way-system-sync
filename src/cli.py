"""Run with python -m src.cli. Only the Python standard library is required."""
import argparse
import json
from pathlib import Path
from .adapters.warehouse import Warehouse
from .adapters.accounting import Accounting
from .idempotency import Journal
from .sync import Sync
from .reconciliation import reconcile


def open_systems(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return (Warehouse(directory / "warehouse.db"), Accounting(directory / "accounting.db"),
            Journal(directory / "integration.db"))


def sample(key, quantity=4):
    return {"shipment_id": key, "order_id": "ORD-100", "sku": "WIDGET-01",
            "quantity": quantity, "fulfillment_status": "shipped"}


def show(label, value):
    print(label)
    print(json.dumps(value, indent=2, sort_keys=True))


def demo(warehouse, accounting, journal, sync):
    if warehouse.shipments() or accounting.invoices() or journal.summary():
        raise ValueError("demo requires an empty data directory; choose a new --data path")
    good, bad = sample("SHP-001"), sample("SHP-002", "four")
    warehouse.add_shipment(good)
    warehouse.add_shipment(bad)
    accounting.fail_after = 1
    show("1. Timeout after committed invoice (recoverable)",
         sync.submit("shipment-SHP-001", "warehouse_to_accounting", good))
    show("2. Retry persisted event; destination idempotency prevents a second invoice", sync.retry())
    show("3. Duplicate delivery", sync.submit("shipment-SHP-001", "warehouse_to_accounting", good))
    show("4. Invalid quantity quarantined", sync.submit("shipment-SHP-002", "warehouse_to_accounting", bad))
    accounting.mark_paid("SHP-001")
    show("5. Accounting-owned paid status flows back to warehouse", sync.poll())
    show("6. Reconciliation shows the rejected shipment and unresolved event",
         reconcile(warehouse, accounting, journal))
    print("Expected demo exception remains visible. Run audit or reconcile to inspect it.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data", help="directory for three persistent SQLite databases")
    parser.add_argument("command", choices=("demo", "sync", "retry", "reconcile", "audit", "seed", "pay"))
    parser.add_argument("shipment_id", nargs="?")
    args = parser.parse_args(argv)
    warehouse, accounting, journal = open_systems(args.data)
    sync = Sync(warehouse, accounting, journal)
    try:
        if args.command == "demo":
            demo(warehouse, accounting, journal, sync)
        elif args.command == "seed":
            warehouse.add_shipment(sample(args.shipment_id or "SHP-001"))
            show("Warehouse shipment created", args.shipment_id or "SHP-001")
        elif args.command == "pay":
            if not args.shipment_id:
                parser.error("pay requires shipment_id")
            accounting.mark_paid(args.shipment_id)
            show("Accounting payment recorded", args.shipment_id)
        elif args.command == "sync":
            show("Sync results", sync.poll())
            return 0 if reconcile(warehouse, accounting, journal)["healthy"] else 1
        elif args.command == "retry":
            show("Retry results", sync.retry())
            return 0 if reconcile(warehouse, accounting, journal)["healthy"] else 1
        elif args.command == "audit":
            show("Integration audit", journal.audit())
        elif args.command == "reconcile":
            report = reconcile(warehouse, accounting, journal)
            show("Reconciliation", report)
            return 0 if report["healthy"] else 1
        return 0
    except ValueError as exc:
        print("ERROR:", exc)
        return 2
    finally:
        warehouse.close()
        accounting.close()
        journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
