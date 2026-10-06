"""Allowlist transforms make field ownership enforceable."""
import hashlib
import json
from . import validation


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def fingerprint(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def to_invoice(data):
    valid = validation.shipment(data)
    return {key: valid[key] for key in ("shipment_id", "order_id", "sku", "quantity")}


def to_warehouse_status(data):
    return validation.accounting_status(data)
