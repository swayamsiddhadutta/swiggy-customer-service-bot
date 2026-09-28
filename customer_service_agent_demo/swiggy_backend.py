from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable


DB_PATH = Path(__file__).with_name("swiggy_backend.sqlite")
MAX_REASON_STEPS = 6

READ_ONLY_TOOLS = {
    "get_order_status",
    "get_order_details",
    "check_refund_eligibility",
    "get_order_history",
    "check_payment_transactions",
}

MUTATING_TOOLS = {
    "issue_refund",
    "cancel_order",
    "create_escalation_ticket",
}


def pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def stable_id(prefix: str, value: str, digits: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    numeric = int(digest[:12], 16) % (10**digits)
    return f"{prefix}-{numeric:0{digits}d}"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def audit(conn: sqlite3.Connection, action: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_log(action, payload_json) VALUES (?, ?)",
        (action, json.dumps(payload, sort_keys=True)),
    )


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    cur = conn.cursor()

    cur.executescript(
        """
        DROP TABLE IF EXISTS audit_log;
        DROP TABLE IF EXISTS escalation_tickets;
        DROP TABLE IF EXISTS refunds;
        DROP TABLE IF EXISTS support_contacts;
        DROP TABLE IF EXISTS payments;
        DROP TABLE IF EXISTS order_status_events;
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS menu_items;
        DROP TABLE IF EXISTS restaurants;
        DROP TABLE IF EXISTS delivery_partners;
        DROP TABLE IF EXISTS customer_addresses;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            loyalty_tier TEXT NOT NULL,
            risk_level TEXT NOT NULL DEFAULT 'normal'
        );

        CREATE TABLE customer_addresses (
            address_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            label TEXT NOT NULL,
            line1 TEXT NOT NULL,
            city TEXT NOT NULL,
            area TEXT NOT NULL,
            pincode TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE restaurants (
            restaurant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT NOT NULL,
            area TEXT NOT NULL,
            cuisine TEXT NOT NULL,
            rating REAL NOT NULL,
            is_active INTEGER NOT NULL
        );

        CREATE TABLE menu_items (
            menu_item_id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL,
            is_available INTEGER NOT NULL,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(restaurant_id)
        );

        CREATE TABLE delivery_partners (
            partner_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            vehicle_type TEXT NOT NULL,
            current_area TEXT NOT NULL,
            rating REAL NOT NULL
        );

        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            address_id TEXT NOT NULL,
            restaurant_id TEXT NOT NULL,
            delivery_partner_id TEXT,
            status TEXT NOT NULL,
            eta_minutes INTEGER,
            delivered_minutes_ago INTEGER,
            cancellable INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            delivery_fee INTEGER NOT NULL,
            platform_fee INTEGER NOT NULL,
            taxes INTEGER NOT NULL,
            total_amount INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            delivered_at TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
            FOREIGN KEY (address_id) REFERENCES customer_addresses(address_id),
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(restaurant_id),
            FOREIGN KEY (delivery_partner_id) REFERENCES delivery_partners(partner_id)
        );

        CREATE TABLE order_items (
            order_id TEXT NOT NULL,
            menu_item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            line_total INTEGER NOT NULL,
            PRIMARY KEY (order_id, item_name),
            FOREIGN KEY (order_id) REFERENCES orders(order_id),
            FOREIGN KEY (menu_item_id) REFERENCES menu_items(menu_item_id)
        );

        CREATE TABLE order_status_events (
            event_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            status TEXT NOT NULL,
            event_time TEXT NOT NULL,
            note TEXT NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE payments (
            transaction_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            method TEXT NOT NULL,
            provider TEXT NOT NULL,
            captured_at TEXT,
            FOREIGN KEY (order_id) REFERENCES orders(order_id),
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE support_contacts (
            contact_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            order_id TEXT,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE refunds (
            refund_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            item TEXT NOT NULL,
            amount INTEGER NOT NULL,
            reason TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE escalation_tickets (
            ticket_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            order_id TEXT,
            team TEXT NOT NULL,
            priority TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    cur.executemany(
        """
        INSERT INTO customers(customer_id, name, phone, loyalty_tier, risk_level)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("CUST-1", "Asha Rao", "+91-90000-00001", "gold", "normal"),
            ("CUST-2", "Kabir Shah", "+91-90000-00002", "silver", "normal"),
            ("CUST-3", "Meera Iyer", "+91-90000-00003", "gold", "normal"),
            ("CUST-4", "Dev Menon", "+91-90000-00004", "bronze", "normal"),
            ("CUST-5", "Ira Sen", "+91-90000-00005", "silver", "normal"),
        ],
    )

    cur.executemany(
        """
        INSERT INTO customer_addresses(
            address_id, customer_id, label, line1, city, area, pincode, latitude, longitude
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("ADDR-1", "CUST-1", "home", "221 Indiranagar 12th Main", "Bengaluru", "Indiranagar", "560038", 12.9784, 77.6408),
            ("ADDR-2", "CUST-2", "office", "Prestige Tech Park", "Bengaluru", "Marathahalli", "560037", 12.9569, 77.7011),
            ("ADDR-3", "CUST-3", "home", "HSR Layout Sector 2", "Bengaluru", "HSR Layout", "560102", 12.9116, 77.6389),
            ("ADDR-4", "CUST-4", "home", "Koramangala 5th Block", "Bengaluru", "Koramangala", "560095", 12.9352, 77.6245),
            ("ADDR-5", "CUST-5", "home", "JP Nagar 6th Phase", "Bengaluru", "JP Nagar", "560078", 12.8877, 77.5800),
        ],
    )

    cur.executemany(
        """
        INSERT INTO restaurants(restaurant_id, name, city, area, cuisine, rating, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("REST-1", "Rolls Republic", "Bengaluru", "Indiranagar", "North Indian", 4.3, 1),
            ("REST-2", "Burger Bay", "Bengaluru", "Marathahalli", "American", 4.1, 1),
            ("REST-3", "Biryani House", "Bengaluru", "HSR Layout", "Hyderabadi", 4.4, 1),
            ("REST-4", "Pizza Port", "Bengaluru", "Koramangala", "Italian", 4.0, 1),
            ("REST-5", "Darshini Cafe", "Bengaluru", "JP Nagar", "South Indian", 4.5, 1),
        ],
    )

    cur.executemany(
        """
        INSERT INTO menu_items(menu_item_id, restaurant_id, item_name, category, price, is_available)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("MENU-101", "REST-1", "paneer roll", "main", 180, 1),
            ("MENU-202-A", "REST-2", "burger", "main", 220, 1),
            ("MENU-202-B", "REST-2", "coke", "beverage", 120, 1),
            ("MENU-301", "REST-3", "biryani", "main", 450, 1),
            ("MENU-404-A", "REST-4", "pizza", "main", 520, 1),
            ("MENU-404-B", "REST-4", "garlic bread", "side", 160, 1),
            ("MENU-505-A", "REST-5", "dosa", "main", 150, 1),
            ("MENU-505-B", "REST-5", "filter coffee", "beverage", 90, 1),
        ],
    )

    cur.executemany(
        """
        INSERT INTO delivery_partners(partner_id, name, phone, vehicle_type, current_area, rating)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("DP-1", "Ravi Kumar", "+91-91111-11111", "bike", "Indiranagar", 4.8),
            ("DP-2", "Naveen P", "+91-92222-22222", "bike", "Marathahalli", 4.6),
            ("DP-3", "Sana K", "+91-93333-33333", "scooter", "HSR Layout", 4.7),
            ("DP-4", "Arun V", "+91-94444-44444", "bike", "Koramangala", 4.5),
        ],
    )

    cur.executemany(
        """
        INSERT INTO orders(
            order_id, customer_id, address_id, restaurant_id, delivery_partner_id,
            status, eta_minutes, delivered_minutes_ago, cancellable,
            subtotal, delivery_fee, platform_fee, taxes, total_amount,
            created_at, delivered_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("ORD-101", "CUST-1", "ADDR-1", "REST-1", "DP-1", "out_for_delivery", 10, None, 0, 180, 35, 6, 9, 230, "2026-09-27T14:40:00+05:30", None),
            ("ORD-202", "CUST-2", "ADDR-2", "REST-2", "DP-2", "delivered", None, 20, 0, 340, 39, 6, 17, 402, "2026-09-27T13:50:00+05:30", "2026-09-27T14:25:00+05:30"),
            ("ORD-301", "CUST-3", "ADDR-3", "REST-3", "DP-3", "delivered", None, 45, 0, 450, 45, 6, 23, 524, "2026-09-27T13:15:00+05:30", "2026-09-27T14:05:00+05:30"),
            ("ORD-302", "CUST-3", "ADDR-3", "REST-3", None, "created", None, None, 1, 450, 45, 6, 23, 524, "2026-09-27T13:16:00+05:30", None),
            ("ORD-404", "CUST-4", "ADDR-4", "REST-4", "DP-4", "delivered", None, 2880, 0, 680, 49, 6, 34, 769, "2026-09-25T14:20:00+05:30", "2026-09-25T15:05:00+05:30"),
            ("ORD-505", "CUST-5", "ADDR-5", "REST-5", None, "preparing", 35, None, 1, 240, 30, 6, 12, 288, "2026-09-27T14:55:00+05:30", None),
        ],
    )

    cur.executemany(
        """
        INSERT INTO order_items(order_id, menu_item_id, item_name, quantity, unit_price, line_total)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("ORD-101", "MENU-101", "paneer roll", 1, 180, 180),
            ("ORD-202", "MENU-202-A", "burger", 1, 220, 220),
            ("ORD-202", "MENU-202-B", "coke", 1, 120, 120),
            ("ORD-301", "MENU-301", "biryani", 1, 450, 450),
            ("ORD-302", "MENU-301", "biryani", 1, 450, 450),
            ("ORD-404", "MENU-404-A", "pizza", 1, 520, 520),
            ("ORD-404", "MENU-404-B", "garlic bread", 1, 160, 160),
            ("ORD-505", "MENU-505-A", "dosa", 1, 150, 150),
            ("ORD-505", "MENU-505-B", "filter coffee", 1, 90, 90),
        ],
    )

    cur.executemany(
        """
        INSERT INTO order_status_events(event_id, order_id, status, event_time, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("EVT-101-1", "ORD-101", "confirmed", "2026-09-27T14:41:00+05:30", "Restaurant accepted the order"),
            ("EVT-101-2", "ORD-101", "out_for_delivery", "2026-09-27T15:02:00+05:30", "Partner picked up the order"),
            ("EVT-202-1", "ORD-202", "delivered", "2026-09-27T14:25:00+05:30", "Delivered to office reception"),
            ("EVT-301-1", "ORD-301", "delivered", "2026-09-27T14:05:00+05:30", "Delivered to customer"),
            ("EVT-302-1", "ORD-302", "created", "2026-09-27T13:16:00+05:30", "Potential duplicate order created"),
            ("EVT-404-1", "ORD-404", "delivered", "2026-09-25T15:05:00+05:30", "Delivered to customer"),
            ("EVT-505-1", "ORD-505", "preparing", "2026-09-27T15:00:00+05:30", "Restaurant is preparing the order"),
        ],
    )

    cur.executemany(
        """
        INSERT INTO payments(transaction_id, order_id, customer_id, amount, status, method, provider, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("TXN-101", "ORD-101", "CUST-1", 230, "charged", "upi", "razorpay", "2026-09-27T14:40:05+05:30"),
            ("TXN-202", "ORD-202", "CUST-2", 402, "charged", "card", "juspay", "2026-09-27T13:50:10+05:30"),
            ("TXN-301", "ORD-301", "CUST-3", 524, "charged", "upi", "razorpay", "2026-09-27T13:15:10+05:30"),
            ("TXN-302", "ORD-302", "CUST-3", 524, "charged", "upi", "razorpay", "2026-09-27T13:16:08+05:30"),
            ("TXN-404", "ORD-404", "CUST-4", 769, "charged", "wallet", "paytm", "2026-09-25T14:20:10+05:30"),
            ("TXN-505", "ORD-505", "CUST-5", 288, "charged", "upi", "razorpay", "2026-09-27T14:55:08+05:30"),
        ],
    )

    cur.executemany(
        """
        INSERT INTO support_contacts(contact_id, customer_id, order_id, category, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("SUP-301-A", "CUST-3", "ORD-301", "payment_dispute", "open", "2026-09-27T14:15:00+05:30"),
            ("SUP-301-B", "CUST-3", "ORD-301", "payment_dispute", "open", "2026-09-27T14:35:00+05:30"),
        ],
    )

    conn.commit()
    conn.close()


def detect_item(user_text: str) -> str | None:
    for item in ["garlic bread", "filter coffee", "paneer roll", "biryani", "burger", "coke", "pizza", "dosa", "coffee"]:
        if item in user_text.lower():
            return "filter coffee" if item == "coffee" else item
    return None


def get_order_status(order_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Fetch current order status, ETA, restaurant, and delivery partner."""
    with connect(db_path) as conn:
        order = row_to_dict(
            conn.execute(
                """
                SELECT
                    o.order_id,
                    o.customer_id,
                    o.status,
                    o.eta_minutes,
                    o.cancellable,
                    o.total_amount,
                    r.name AS restaurant_name,
                    r.area AS restaurant_area,
                    dp.name AS delivery_partner_name,
                    dp.phone AS delivery_partner_phone,
                    dp.current_area AS delivery_partner_area
                FROM orders o
                JOIN restaurants r ON r.restaurant_id = o.restaurant_id
                LEFT JOIN delivery_partners dp ON dp.partner_id = o.delivery_partner_id
                WHERE o.order_id = ?
                """,
                (order_id,),
            ).fetchone()
        )
        events = [
            dict(row)
            for row in conn.execute(
                """
                SELECT status, event_time, note
                FROM order_status_events
                WHERE order_id = ?
                ORDER BY event_time
                """,
                (order_id,),
            ).fetchall()
        ]
    if not order:
        return {"ok": False, "error": "order_not_found", "order_id": order_id}
    return {"ok": True, **order, "status_events": events}


def get_order_details(order_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Fetch order, items, totals, restaurant, address, and delivery age."""
    with connect(db_path) as conn:
        order = row_to_dict(
            conn.execute(
                """
                SELECT
                    o.order_id,
                    o.customer_id,
                    o.status,
                    o.delivered_minutes_ago,
                    o.cancellable,
                    o.subtotal,
                    o.delivery_fee,
                    o.platform_fee,
                    o.taxes,
                    o.total_amount,
                    o.created_at,
                    o.delivered_at,
                    r.name AS restaurant_name,
                    r.cuisine,
                    a.area AS delivery_area,
                    a.pincode
                FROM orders o
                JOIN restaurants r ON r.restaurant_id = o.restaurant_id
                JOIN customer_addresses a ON a.address_id = o.address_id
                WHERE o.order_id = ?
                """,
                (order_id,),
            ).fetchone()
        )
        items = [
            {
                "item": row["item_name"],
                "quantity": row["quantity"],
                "price": row["unit_price"],
                "line_total": row["line_total"],
            }
            for row in conn.execute(
                """
                SELECT item_name, quantity, unit_price, line_total
                FROM order_items
                WHERE order_id = ?
                ORDER BY item_name
                """,
                (order_id,),
            ).fetchall()
        ]
    if not order:
        return {"ok": False, "error": "order_not_found", "order_id": order_id}
    return {"ok": True, **order, "items": items}


def check_refund_eligibility(
    order_id: str,
    item: str,
    reason: str,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Policy check before refunding an item."""
    details = get_order_details(order_id, db_path)
    if not details.get("ok"):
        return details

    normalized_item = item.lower()
    matched = next(
        (row for row in details["items"] if row["item"].lower() == normalized_item),
        None,
    )
    if matched is None:
        return {
            "ok": True,
            "eligible": False,
            "reason": "item_not_found_in_order",
            "refund_amount": 0,
        }

    if details["status"] != "delivered":
        return {
            "ok": True,
            "eligible": False,
            "reason": "order_not_delivered_yet",
            "refund_amount": 0,
        }

    delivered_minutes_ago = details["delivered_minutes_ago"] or 0
    if delivered_minutes_ago > 24 * 60:
        return {
            "ok": True,
            "eligible": False,
            "reason": "refund_window_expired",
            "refund_amount": 0,
            "delivered_minutes_ago": delivered_minutes_ago,
        }

    if reason not in {"missing_item", "wrong_item", "damaged_item"}:
        return {
            "ok": True,
            "eligible": False,
            "reason": "unsupported_refund_reason",
            "refund_amount": 0,
        }

    return {
        "ok": True,
        "eligible": True,
        "reason": reason,
        "refund_amount": matched["line_total"],
        "item": matched["item"],
    }


def issue_refund(
    order_id: str,
    item: str,
    idempotency_key: str,
    reason: str = "missing_item",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Issue an item-level refund after policy approval."""
    eligibility = check_refund_eligibility(order_id, item, reason, db_path)
    if not eligibility.get("eligible"):
        return {
            "ok": False,
            "error": "policy_denied",
            "eligibility": eligibility,
        }

    refund_id = stable_id("REF", idempotency_key, 5)
    with connect(db_path) as conn:
        existing = row_to_dict(
            conn.execute(
                "SELECT refund_id, status, amount, reason FROM refunds WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        )
        if existing:
            return {"ok": True, "duplicate_request": True, "eta": "3-5 business days", **existing}

        conn.execute(
            """
            INSERT INTO refunds(refund_id, order_id, item, amount, reason, idempotency_key, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                refund_id,
                order_id,
                eligibility["item"],
                eligibility["refund_amount"],
                reason,
                idempotency_key,
                "success",
            ),
        )
        audit(
            conn,
            "issue_refund",
            {
                "refund_id": refund_id,
                "order_id": order_id,
                "item": eligibility["item"],
                "amount": eligibility["refund_amount"],
                "reason": reason,
                "idempotency_key": idempotency_key,
            },
        )
        conn.commit()

    return {
        "ok": True,
        "refund_id": refund_id,
        "status": "success",
        "amount": eligibility["refund_amount"],
        "reason": reason,
        "eta": "3-5 business days",
    }


def get_order_history(customer_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Fetch recent orders and prior support contacts."""
    with connect(db_path) as conn:
        orders = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    o.order_id,
                    o.status,
                    o.total_amount,
                    o.created_at,
                    r.name AS restaurant_name
                FROM orders o
                JOIN restaurants r ON r.restaurant_id = o.restaurant_id
                WHERE o.customer_id = ?
                ORDER BY o.created_at DESC
                """,
                (customer_id,),
            ).fetchall()
        ]
        support_contacts = [
            dict(row)
            for row in conn.execute(
                """
                SELECT contact_id, order_id, category, status, created_at
                FROM support_contacts
                WHERE customer_id = ?
                ORDER BY created_at
                """,
                (customer_id,),
            ).fetchall()
        ]
    return {
        "ok": True,
        "customer_id": customer_id,
        "orders": orders,
        "prior_support_contacts": len(support_contacts),
        "support_contacts": support_contacts,
    }


def check_payment_transactions(
    customer_id: str,
    order_ids: list[str],
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Fetch payment transactions for supplied order IDs."""
    if not order_ids:
        return {"ok": True, "transactions": [], "duplicate_charge_likely": False}

    placeholders = ",".join("?" for _ in order_ids)
    params: list[Any] = [customer_id, *order_ids]
    with connect(db_path) as conn:
        transactions = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT transaction_id, order_id, amount, status, method, provider, captured_at
                FROM payments
                WHERE customer_id = ?
                AND order_id IN ({placeholders})
                ORDER BY captured_at
                """,
                params,
            ).fetchall()
        ]

    charged = [row for row in transactions if row["status"] == "charged"]
    duplicate_charge_likely = len(charged) >= 2 and len({row["amount"] for row in charged}) == 1
    return {
        "ok": True,
        "transactions": transactions,
        "duplicate_charge_likely": duplicate_charge_likely,
    }


def cancel_order(order_id: str, idempotency_key: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Cancel an order if it is still in a cancellable state."""
    status = get_order_status(order_id, db_path)
    if not status.get("ok"):
        return status
    if not status["cancellable"]:
        return {
            "ok": False,
            "error": "policy_denied",
            "reason": "order_not_cancellable",
            "current_status": status["status"],
        }

    with connect(db_path) as conn:
        conn.execute(
            "UPDATE orders SET status = ?, cancellable = 0 WHERE order_id = ?",
            ("cancelled", order_id),
        )
        conn.execute(
            """
            INSERT INTO order_status_events(event_id, order_id, status, event_time, note)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
            """,
            (
                stable_id("EVT", idempotency_key, 6),
                order_id,
                "cancelled",
                "Cancelled by customer-service agent",
            ),
        )
        audit(conn, "cancel_order", {"order_id": order_id, "idempotency_key": idempotency_key})
        conn.commit()

    return {
        "ok": True,
        "order_id": order_id,
        "status": "cancelled",
        "idempotency_key": idempotency_key,
    }


def create_escalation_ticket(
    customer_id: str,
    order_id: str | None,
    reasoning: str,
    trace: list[str],
    team: str = "payments",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Create a human-review ticket with trace context."""
    ticket_id = stable_id("TICKET", customer_id + reasoning, 4)
    priority = "high" if team in {"payments", "support-ops"} else "normal"
    with connect(db_path) as conn:
        existing = row_to_dict(
            conn.execute(
                "SELECT ticket_id, status, team FROM escalation_tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        )
        if existing:
            return {"ok": True, "duplicate_request": True, **existing}

        conn.execute(
            """
            INSERT INTO escalation_tickets(
                ticket_id, customer_id, order_id, team, priority, reasoning,
                trace_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                ticket_id,
                customer_id,
                order_id,
                team,
                priority,
                reasoning,
                json.dumps(trace, sort_keys=True),
                "created",
            ),
        )
        audit(
            conn,
            "create_escalation_ticket",
            {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "order_id": order_id,
                "team": team,
                "reasoning": reasoning,
            },
        )
        conn.commit()

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "status": "created",
        "team": team,
        "priority": priority,
        "sla": "24 hours",
    }


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_order_status": get_order_status,
    "get_order_details": get_order_details,
    "check_refund_eligibility": check_refund_eligibility,
    "issue_refund": issue_refund,
    "get_order_history": get_order_history,
    "check_payment_transactions": check_payment_transactions,
    "cancel_order": cancel_order,
    "create_escalation_ticket": create_escalation_ticket,
}


SCENARIOS = [
    {
        "name": "1. Where is my order?",
        "customer_id": "CUST-1",
        "order_id": "ORD-101",
        "message": "Where is my order?",
    },
    {
        "name": "2. Missing drink refund",
        "customer_id": "CUST-2",
        "order_id": "ORD-202",
        "message": "My order arrived without the coke I ordered. I want a refund.",
    },
    {
        "name": "3. Charged twice with repeat support contacts",
        "customer_id": "CUST-3",
        "order_id": "ORD-301",
        "message": (
            "I was charged twice, the app shows two orders but I only got one, "
            "and I already messaged support twice."
        ),
    },
    {
        "name": "4. Refund request outside automatic window",
        "customer_id": "CUST-4",
        "order_id": "ORD-404",
        "message": "The garlic bread was missing from my old order. I want a refund.",
    },
    {
        "name": "5. Cancel an order that is still cancellable",
        "customer_id": "CUST-5",
        "order_id": "ORD-505",
        "message": "Please cancel my order.",
    },
]
