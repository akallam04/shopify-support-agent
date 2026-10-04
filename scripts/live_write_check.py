"""Runs the same write actions on the live development store and on the simulated store, then compares.

Dry run by default: only prepares each action on both backends. --apply executes the writes,
re-reads the touched live orders, and saves a report to evals/results/live-writes/. The live
side uses SHOPIFY_WRITE_TOKEN and refuses to run against anything but a development store.

Run from the repo root: .venv/bin/python -m scripts.live_write_check [--apply]
"""

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from mcp_server.backends.shopify import ShopifyAdminBackend
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, SystemClock
from mcp_server.server import live_write_client
from mcp_server.shopify_client import ShopifyClient
from mcp_server.simdb import Order, load_db
from mcp_server.tools import execute, prepare

REPORT_DIR = Path("evals/results/live-writes")
MAYA, JORDAN, SOFIA = "maya.thompson@example.com", "jordan.lee@example.com", "sofia.ramirez@example.com"
NEW_ADDRESS = {"address1": "9 Pine St", "city": "Boulder", "province_code": "CO", "zip": "80302", "country_code": "US"}

CASES: list[tuple[str, str, dict[str, Any], str]] = [
    ("cancel eligible", "cancel_order", {"order_number": "#1020", "email": MAYA, "reason": "ordered_by_mistake"}, "ok"),
    ("address eligible", "update_shipping_address", {"order_number": "#1021", "email": JORDAN, **NEW_ADDRESS}, "ok"),
    (
        "return in window",
        "request_return",
        {"order_number": "#1017", "email": JORDAN, "reason": "size_too_small", "items": [{"title": "Stormline Rain Jacket", "variant": "L", "quantity": 1}]},
        "ok",
    ),
    ("return final sale", "request_return", {"order_number": "#1018", "email": SOFIA, "reason": "unwanted", "items": [{"title": "Meridian Ski Goggles"}]}, "final_sale"),
    ("return out of window", "request_return", {"order_number": "#1016", "email": MAYA, "reason": "unwanted", "items": [{"title": "Alpine Crossing Hiking Boots"}]}, "return_window_passed"),
    ("cancel after shipping", "cancel_order", {"order_number": "#1019", "email": MAYA, "reason": "changed_mind"}, "already_shipped"),
    ("cancel after the window", "cancel_order", {"order_number": "#1002", "email": MAYA, "reason": "changed_mind"}, "change_window_passed"),
    ("cancel with a wrong email", "cancel_order", {"order_number": "#1021", "email": MAYA, "reason": "changed_mind"}, "order_not_found"),
    ("replayed cancel", "cancel_order", {"order_number": "#1020", "email": MAYA, "reason": "ordered_by_mistake"}, "duplicate"),
]
TOUCHED = ("#1020", "#1021", "#1017")


def outcome(result: dict[str, Any]) -> str:
    if result.get("duplicate"):
        return "duplicate"
    return "ok" if result.get("ok") else result.get("code", "?")


def facts(order: Order) -> dict[str, Any]:
    address = order.shipping_address
    return {
        "cancelled": order.cancelled_at is not None,
        "financial_status": order.financial_status,
        "fulfillment_status": order.fulfillment_status,
        "shipping_address": address.model_dump() if address else None,
        "returns": sorted((r.status, rl.line_item_id, rl.quantity) for r in order.returns for rl in r.line_items),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only", help="run only the cases whose label contains this text")
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    s = get_settings()
    now = SystemClock().now()
    sim = SimStoreBackend(load_db(s.sim_seed_path), FrozenClock(now))
    read = ShopifyClient(s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version)
    live = ShopifyAdminBackend(read, live_write_client(s, read))

    rows = []
    cases = [c for c in CASES if not args.only or args.only in c[0]]
    for label, action, case_args, expected in cases:
        if args.apply:
            sim_out, live_out = outcome(execute(sim, action, case_args)), outcome(execute(live, action, case_args))
        else:
            sim_out = "ok" if prepare(sim, action, case_args).allowed else prepare(sim, action, case_args).code
            live_out = "ok" if prepare(live, action, case_args).allowed else prepare(live, action, case_args).code
        match = sim_out == live_out == expected or (not args.apply and expected == "duplicate" and sim_out == live_out)
        rows.append({"case": label, "action": action, "expected": expected, "sim": sim_out, "live": live_out, "match": match})
        print(f"{'ok ' if match else 'MISMATCH'} {label:<26} expected {expected:<22} sim {sim_out:<22} live {live_out}")

    report: dict[str, Any] = {"ran_at": now.isoformat(), "applied": args.apply, "cases": rows}
    if args.apply:
        state = {}
        touched = [n for n in TOUCHED if any(c[2]["order_number"] == n and c[3] == "ok" for c in cases)]
        for name in touched:
            live_order, sim_order = live.find_order(name).order, sim.find_order(name).order
            state[name] = {"live": facts(live_order), "sim": facts(sim_order)}
            for field in ("cancelled", "shipping_address", "returns", "financial_status", "fulfillment_status"):
                same = state[name]["live"][field] == state[name]["sim"][field]
                print(f"{'same' if same else 'DIFF':<4} {name} {field:<19} live {state[name]['live'][field]}  sim {state[name]['sim'][field]}")
        report["state"] = state
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {path}")
    read.close()


if __name__ == "__main__":
    main()
