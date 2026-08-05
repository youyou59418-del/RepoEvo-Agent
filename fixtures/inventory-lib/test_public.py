import pytest

from inventory_lib.inventory import (
    Item,
    available,
    can_fulfill,
    needs_reorder,
    reserve,
    restock,
    snapshot,
    total_units,
)


def stock() -> dict[str, Item]:
    return {"A": Item("A", 10, 3), "B": Item("B", 2, 2)}


def test_reserve_and_restock() -> None:
    items = stock()
    assert reserve(items, "A", 4).quantity == 6
    assert restock(items, "A", 2).quantity == 8
    with pytest.raises(ValueError):
        reserve(items, "A", 0)


def test_available_missing_sku_is_zero() -> None:
    assert available(stock(), "MISSING") == 0


def test_reorder_threshold_includes_equal() -> None:
    assert needs_reorder(Item("B", 2, 2)) is True


def test_totals_and_snapshot_are_stable() -> None:
    items = stock()
    assert total_units(items) == 12
    assert [row["sku"] for row in snapshot(items)] == ["A", "B"]


def test_can_fulfill_requires_every_request() -> None:
    assert can_fulfill(stock(), {"A": 2, "B": 1}) is True
    assert can_fulfill(stock(), {"A": 2, "MISSING": 1}) is False
