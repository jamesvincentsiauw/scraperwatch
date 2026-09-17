import dataclasses
import threading

import pytest

from scraperwatch.stats.aggregate import MAX_SAMPLES, RunStats, as_mapping
from scraperwatch.stats.values import EmptyRule


def new_stats(key: tuple[str, ...] = ()) -> RunStats:
    return RunStats(EmptyRule(), key)


def test_counts_pages_items_and_fields() -> None:
    stats = new_stats()
    stats.add_page(
        "https://shop.test/p/1",
        [{"title": "A", "price": 10}, {"title": "B", "price": None}],
        status=200,
    )
    stats.add_page("https://shop.test/p/2", [{"title": "", "price": 5.5}], status=200)

    s = stats.page_types["default"]
    assert (s.pages, s.items, s.empty_pages, s.error_pages) == (2, 3, 0, 0)
    assert s.fields["title"].present == 3
    assert s.fields["title"].non_empty == 2
    assert s.fields["price"].non_empty == 2
    assert s.fields["price"].types["number"] == 2
    assert s.fields["price"].empty_samples.urls == ["https://shop.test/p/1"]
    assert stats.domains["shop.test"].statuses[200] == 2


def test_missing_key_counts_as_empty() -> None:
    stats = new_stats()
    stats.add_page("https://a.test/1", [{"title": "A", "price": 1}])
    stats.add_page("https://a.test/2", [{"title": "B"}])

    price = stats.page_types["default"].fields["price"]
    assert price.present == 1
    assert price.non_empty == 1
    assert stats.page_types["default"].items == 2
    assert price.empty_samples.urls == ["https://a.test/2"]


def test_empty_and_error_pages_are_separate() -> None:
    stats = new_stats()
    stats.add_page("https://a.test/empty", [])
    stats.add_page("https://a.test/broken", [], error="timeout")

    s = stats.page_types["default"]
    assert (s.empty_pages, s.error_pages) == (1, 1)
    assert s.empty_page_samples.urls == ["https://a.test/empty"]
    assert s.error_samples.urls == ["https://a.test/broken"]


def test_page_types_are_tracked_separately() -> None:
    stats = new_stats()
    stats.add_page("https://a.test/list", [{"url": "x"}], page_type="listing")
    stats.add_page("https://a.test/item", [{"title": "x"}], page_type="product")
    assert set(stats.page_types) == {"listing", "product"}
    assert stats.total_items == 2
    assert stats.total_pages == 2


def test_duplicates_by_key() -> None:
    stats = new_stats(("platform", "id"))
    stats.add_page("https://a.test/1", [{"platform": "s", "id": 1}, {"platform": "s", "id": 2}])
    stats.add_page("https://a.test/2", [{"platform": "s", "id": 2}, {"platform": "s", "id": 3}])
    stats.add_page("https://a.test/3", [{"platform": None, "id": None}])

    s = stats.page_types["default"]
    assert s.keyed_items == 4
    assert s.duplicate_items() == 1
    assert s.unique_items() == 4  # three distinct keys plus one keyless item
    assert s.duplicate_samples.urls == ["https://a.test/2"]


def test_samples_are_capped_and_unique() -> None:
    stats = new_stats()
    for i in range(MAX_SAMPLES + 3):
        stats.add_page(f"https://a.test/{i}", [])
        stats.add_page(f"https://a.test/{i}", [])
    assert len(stats.page_types["default"].empty_page_samples.urls) == MAX_SAMPLES


def test_accepts_dataclasses_and_model_dump() -> None:
    @dataclasses.dataclass
    class Product:
        title: str

    class Model:
        def model_dump(self) -> dict[str, object]:
            return {"title": "m"}

    assert as_mapping(Product("p")) == {"title": "p"}
    assert as_mapping(Model()) == {"title": "m"}
    with pytest.raises(TypeError):
        as_mapping("not an item")


def test_concurrent_pages_are_all_counted() -> None:
    stats = new_stats(("id",))

    def worker(offset: int) -> None:
        for i in range(500):
            stats.add_page(f"https://a.test/{offset}-{i}", [{"id": offset * 1000 + i, "v": 1}])

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    s = stats.page_types["default"]
    assert s.pages == 4000
    assert s.fields["v"].non_empty == 4000
    assert s.duplicate_items() == 0
