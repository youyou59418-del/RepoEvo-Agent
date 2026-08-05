from decimal import Decimal

import pytest

from order_service.orders import (
    Order,
    paginate,
    parse_order,
    sort_orders,
    to_public_dict,
    total_for_customer,
    transition,
    validate_order,
)


def make_order(order_id: str, customer: str, total: str, status: str = "pending") -> Order:
    return Order(order_id, customer, Decimal(total), status)


def test_parse_and_validate_order() -> None:
    order = parse_order(
        {"order_id": "o1", "customer_id": "c1", "total": "12.5", "status": "pending"}
    )
    assert order.total == Decimal("12.50")
    validate_order(order)
    with pytest.raises(ValueError):
        validate_order(make_order("o2", "c1", "-1"))


def test_customer_total() -> None:
    orders = [make_order("o1", "c1", "2.50"), make_order("o2", "c2", "4.00")]
    assert total_for_customer(orders, "c1") == Decimal("2.50")


def test_pagination_is_one_based() -> None:
    orders = [make_order(f"o{i}", "c1", str(i)) for i in range(1, 6)]
    assert [o.order_id for o in paginate(orders, 2, 2)] == ["o3", "o4"]


def test_status_transition_rules() -> None:
    order = make_order("o1", "c1", "2", "pending")
    assert transition(order, "paid").status == "paid"
    with pytest.raises(ValueError):
        transition(order, "shipped")


def test_sort_and_public_serialization() -> None:
    orders = [make_order("o2", "c1", "3"), make_order("o1", "c1", "3")]
    assert [o.order_id for o in sort_orders(orders)] == ["o1", "o2"]
    assert to_public_dict(orders[0])["total"] == "3"
