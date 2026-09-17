"""A deterministic fake e-commerce site with switchable faults, rendered as HTML."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from html import escape

CATEGORIES = ("phones", "laptops", "audio", "cameras", "watches")
PER_PAGE = 20


class Fault(StrEnum):
    NONE = "none"
    PRICE_CLASS_RENAMED = "price_class_renamed"
    EMPTY_CATEGORY = "empty_category"
    NEW_TEMPLATE = "new_template"
    CHALLENGE_PAGE = "challenge_page"
    HALF_THE_PAGES = "half_the_pages"
    PAGINATION_SHIFT = "pagination_shift"


@dataclass(frozen=True)
class Product:
    id: int
    title: str
    price: int | None
    rating: float | None
    discount: int | None


@dataclass
class Shop:
    """Catalog that drifts a little every day, like a real store."""

    seed: int
    products: dict[str, list[Product]] = field(default_factory=dict[str, list[Product]])
    next_id: int = 1

    def __post_init__(self) -> None:
        rng = random.Random(self.seed)
        for cat in CATEGORIES:
            self.products[cat] = [self._new(rng, cat) for _ in range(rng.randint(100, 140))]

    def _new(self, rng: random.Random, cat: str) -> Product:
        pid = self.next_id
        self.next_id += 1
        return Product(
            id=pid,
            title=f"{cat[:-1].title()} model {pid}",
            price=None if rng.random() < 0.01 else rng.randint(50, 5000) * 1000,
            rating=None if rng.random() < 0.03 else round(rng.uniform(3, 5), 1),
            discount=rng.randint(5, 50) if rng.random() < 0.3 else None,
        )

    def advance_day(self, day: int) -> None:
        """Removes up to 5% of each category's listings and adds up to 5% new ones."""
        rng = random.Random(self.seed * 1000 + day)
        for cat in CATEGORIES:
            items = self.products[cat]
            for _ in range(rng.randint(0, len(items) // 20)):
                items.pop(rng.randrange(len(items)))
            for _ in range(rng.randint(0, len(items) // 20)):
                items.insert(rng.randrange(len(items) + 1), self._new(rng, cat))

    def pages(self, fault: Fault) -> list[tuple[str, int]]:
        """(category, page number) pairs a crawler would visit."""
        out: list[tuple[str, int]] = []
        for cat in CATEGORIES:
            count = -(-len(self.products[cat]) // PER_PAGE)
            if fault is Fault.HALF_THE_PAGES:
                count = max(1, count // 2)
            out.extend((cat, n) for n in range(1, count + 1))
        return out

    def render(self, cat: str, page: int, fault: Fault, rng: random.Random) -> tuple[int, str]:
        """Returns (HTTP status, HTML) for one listing page."""
        if fault is Fault.CHALLENGE_PAGE and rng.random() < 0.3:
            return 200, CHALLENGE_HTML
        items = self.products[cat]
        start = (page - 1) * PER_PAGE
        if fault is Fault.PAGINATION_SHIFT:
            # New listings land at the top while the crawler walks the pages.
            start = max(0, start - (page - 1) * 6)
        chunk = items[start : start + PER_PAGE]
        if fault is Fault.EMPTY_CATEGORY and cat == CATEGORIES[0]:
            chunk = []
        new_template = fault is Fault.NEW_TEMPLATE and rng.random() < 0.4
        rename_price = fault is Fault.PRICE_CLASS_RENAMED
        cards = "".join(
            _card_v2(p) if new_template else _card_v1(p, rename_price and rng.random() < 0.6)
            for p in chunk
        )
        return (
            200,
            f"<html><body><h1>{escape(cat)}</h1><div class='grid'>{cards}</div></body></html>",
        )


def _money(value: int | None) -> str:
    return "N/A" if value is None else f"Rp {value:,}".replace(",", ".")


def _card_v1(p: Product, renamed_price: bool) -> str:
    price_class = "price-now" if renamed_price else "price"
    rating = "" if p.rating is None else f"<span class='rating'>{p.rating}</span>"
    discount = "" if p.discount is None else f"<span class='discount'>{p.discount}%</span>"
    return (
        f"<div class='card' data-id='{p.id}'><h2 class='title'>{escape(p.title)}</h2>"
        f"<span class='{price_class}'>{_money(p.price)}</span>{rating}{discount}</div>"
    )


def _card_v2(p: Product) -> str:
    return (
        f"<article class='product-card' data-sku='{p.id}'><p class='product-name'>{escape(p.title)}</p>"
        f"<div class='amount'>{_money(p.price)}</div></article>"
    )


CHALLENGE_HTML = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<div id='challenge'>Checking your browser before accessing the site.</div>"
    "<script>/* challenge */</script></body></html>"
)
