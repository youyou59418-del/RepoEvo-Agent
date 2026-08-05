from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Item:
    sku: str
    quantity: int
    reorder_point: int


def available(stock: dict[str, Item], sku: str) -> int:
    item = stock.get(sku)
    return 0 if item is None else item.quantity


def reserve(stock: dict[str, Item], sku: str, amount: int) -> Item:
    if amount <= 0:
        raise ValueError("amount must be positive")
    item = stock.get(sku)
    if item is None or item.quantity < amount:
        raise ValueError("insufficient stock")
    updated = replace(item, quantity=item.quantity - amount)
    stock[sku] = updated
    return updated


def restock(stock: dict[str, Item], sku: str, amount: int) -> Item:
    if amount <= 0:
        raise ValueError("amount must be positive")
    item = stock.get(sku, Item(sku, 0, 0))
    updated = replace(item, quantity=item.quantity + amount)
    stock[sku] = updated
    return updated


def needs_reorder(item: Item) -> bool:
    return item.quantity <= item.reorder_point


def total_units(stock: dict[str, Item]) -> int:
    return sum(item.quantity for item in stock.values())


def snapshot(stock: dict[str, Item]) -> list[dict[str, int | str]]:
    return [
        {"sku": sku, "quantity": item.quantity, "reorder_point": item.reorder_point}
        for sku, item in sorted(stock.items())
    ]


def can_fulfill(stock: dict[str, Item], requests: dict[str, int]) -> bool:
    return all(amount > 0 and available(stock, sku) >= amount for sku, amount in requests.items())
