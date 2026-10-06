"""Reject ambiguous types and impossible values before any destination write."""
import re


class InvalidData(ValueError):
    pass


def identifier(value, field, max_length=64):
    if (not isinstance(value, str) or not 1 <= len(value) <= max_length
            or not re.fullmatch(r"[A-Za-z0-9_-]+", value)):
        raise InvalidData(f"{field}: expected 1-{max_length} letters, digits, underscores or hyphens")
    return value


def shipment(data):
    if not isinstance(data, dict):
        raise InvalidData("shipment: expected an object")
    result = {key: identifier(data.get(key), key) for key in ("shipment_id", "order_id", "sku")}
    quantity = data.get("quantity")
    if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
        raise InvalidData("quantity: expected an integer between 1 and 1000000")
    if data.get("fulfillment_status") != "shipped":
        raise InvalidData("fulfillment_status: invoice requires shipped goods")
    result.update(quantity=quantity, fulfillment_status="shipped")
    return result


def accounting_status(data):
    if not isinstance(data, dict):
        raise InvalidData("accounting status: expected an object")
    result = {key: identifier(data.get(key), key, max_length=128 if key == "invoice_id" else 64)
              for key in ("shipment_id", "invoice_id")}
    if data.get("invoice_status") not in ("issued", "void"):
        raise InvalidData("invoice_status: expected issued or void")
    if data.get("payment_status") not in ("unpaid", "paid"):
        raise InvalidData("payment_status: expected unpaid or paid")
    if type(data.get("version")) is not int or data["version"] < 1:
        raise InvalidData("version: expected a positive integer")
    result.update({key: data[key] for key in ("invoice_status", "payment_status", "version")})
    return result
