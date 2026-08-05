from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

ALLOWED_STATUSES = {"pending", "paid", "shipped", "cancelled"}
TRANSITIONS = {
    "pending": {"paid", "cancelled"},
    "paid": {"shipped", "cancelled"},
    "shipped": set(),
    "cancelled": set(),
}


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    total: Decimal
    status: str


def validate_order(order: Order) -> None:
    if not order.order_id or not order.customer_id:
        raise ValueError("order and customer IDs are required")
    if order.total < 0:
        raise ValueError("total cannot be negative")
    if order.status not in ALLOWED_STATUSES:
        raise ValueError("unknown status")


def parse_order(payload: Mapping[str, object]) -> Order:
    try:
        total = Decimal(str(payload["total"])).quantize(Decimal("0.01"))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError("invalid total") from exc
    order = Order(
        order_id=str(payload["order_id"]),
        customer_id=str(payload["customer_id"]),
        total=total,
        status=str(payload["status"]),
    )
    validate_order(order)
    return order


def total_for_customer(orders: Iterable[Order], customer_id: str) -> Decimal:
    return sum(
        (order.total for order in orders if order.customer_id == customer_id),
        Decimal("0.00"),
    )


def filter_by_status(orders: Iterable[Order], status: str) -> list[Order]:
    return [order for order in orders if order.status == status]


def paginate(items: list[Order], page: int, page_size: int) -> list[Order]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("invalid pagination")
    start = (page - 1) * page_size
    return items[start : start + page_size]


def transition(order: Order, new_status: str) -> Order:
    if new_status not in TRANSITIONS[order.status]:
        raise ValueError("invalid status transition")
    return replace(order, status=new_status)


def sort_orders(orders: Iterable[Order]) -> list[Order]:
    return sorted(orders, key=lambda order: (-order.total, order.order_id))


def to_public_dict(order: Order) -> dict[str, str]:
    return {
        "order_id": order.order_id,
        "customer_id": order.customer_id,
        "total": format(order.total, "f"),
        "status": order.status,
    }
