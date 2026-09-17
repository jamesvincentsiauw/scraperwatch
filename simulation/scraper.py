"""The scraper under test: written for template v1 and never updated, like most scrapers."""

from __future__ import annotations

from html.parser import HTMLParser


class ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, object]] = []
        self._current: dict[str, object] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "div" and "card" in classes:
            self._current = {
                "id": int(a.get("data-id") or 0),
                "title": None,
                "price": None,
                "rating": None,
            }
            self.items.append(self._current)
        elif self._current is not None:
            for name in ("title", "price", "rating", "discount"):
                if name in classes:
                    self._field = name

    def handle_data(self, data: str) -> None:
        if self._current is None or self._field is None:
            return
        text = data.strip()
        if self._field == "price":
            digits = text.removeprefix("Rp ").replace(".", "")
            self._current["price"] = int(digits) if digits.isdigit() else text
        elif self._field == "rating":
            self._current["rating"] = float(text)
        elif self._field == "discount":
            self._current["discount"] = int(text.rstrip("%"))
        else:
            self._current[self._field] = text
        self._field = None


def parse_listing(html: str) -> list[dict[str, object]]:
    parser = ListingParser()
    parser.feed(html)
    return parser.items
