"""Simulated store database: the storage model both backends map into, plus canonical hashing."""

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Address(Model):
    first_name: str | None = None
    last_name: str | None = None
    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    province_code: str | None = None
    zip: str | None = None
    country_code: str | None = None


class Variant(Model):
    variant_id: str
    title: str
    sku: str | None = None
    price: str
    inventory_quantity: int | None = None
    available_for_sale: bool


class Product(Model):
    product_id: str
    handle: str
    title: str
    status: str
    product_type: str = ""
    vendor: str = ""
    description: str = ""
    tags: list[str] = []
    is_gift_card: bool = False
    variants: dict[str, Variant]


class Customer(Model):
    customer_id: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    default_address: Address | None = None


class LineItem(Model):
    line_item_id: str
    product_id: str | None = None
    variant_id: str | None = None
    title: str
    variant_title: str | None = None
    sku: str | None = None
    quantity: int
    unit_price: str


class Tracking(Model):
    number: str | None = None
    company: str | None = None
    url: str | None = None


class FulfilledLine(Model):
    fulfillment_line_item_id: str
    line_item_id: str
    quantity: int


class Fulfillment(Model):
    fulfillment_id: str
    status: str
    display_status: str | None = None
    created_at: str
    delivered_at: str | None = None
    tracking: list[Tracking] = []
    line_items: list[FulfilledLine] = []


class ReturnLine(Model):
    line_item_id: str
    quantity: int


class ReturnRequest(Model):
    status: str
    reason: str
    line_items: list[ReturnLine]


class Order(Model):
    order_id: str
    name: str
    email: str | None = None
    customer_id: str | None = None
    created_at: str
    processed_at: str
    cancelled_at: str | None = None
    cancel_reason: str | None = None
    financial_status: str
    fulfillment_status: str
    currency: str
    total: str
    tags: list[str] = []
    shipping_address: Address | None = None
    line_items: list[LineItem]
    fulfillments: list[Fulfillment] = []
    returns: list[ReturnRequest] = []


class SnapshotMeta(Model):
    store_domain: str
    api_version: str
    frozen_now: str
    notes: list[str] = []


class SimDB(Model):
    meta: SnapshotMeta
    products: dict[str, Product]
    customers: dict[str, Customer]
    orders: dict[str, Order]

    def copy_fresh(self) -> "SimDB":
        return copy.deepcopy(self)


HASHED_SECTIONS = ("products", "customers", "orders")


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        items = [_canonical(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, separators=(",", ":")))
    return value


def canonical_json(db: SimDB) -> str:
    state = db.model_dump(mode="json", include=set(HASHED_SECTIONS))
    return json.dumps(_canonical(state), sort_keys=True, separators=(",", ":"))


def db_hash(db: SimDB) -> str:
    return hashlib.sha256(canonical_json(db).encode()).hexdigest()


def load_db(path: str | Path) -> SimDB:
    return SimDB.model_validate_json(Path(path).read_text())


def save_db(db: SimDB, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(db.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
