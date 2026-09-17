import pytest

from scraperwatch.stats.values import EmptyRule, type_family


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "N/A", " n/a ", "-", [], {}, (), set()],
)
def test_empty_values(value: object) -> None:
    assert EmptyRule().is_empty(value)


@pytest.mark.parametrize("value", ["Rp 10.000", 0, 0.0, False, ["x"], {"a": 1}, "none"])
def test_non_empty_values(value: object) -> None:
    assert not EmptyRule().is_empty(value)


def test_custom_placeholders_replace_defaults() -> None:
    rule = EmptyRule(["Tidak tersedia"])
    assert rule.is_empty("tidak tersedia")
    assert not rule.is_empty("N/A")


@pytest.mark.parametrize(
    ("value", "family"),
    [
        (1, "number"),
        (1.5, "number"),
        (True, "bool"),
        ("x", "string"),
        ({"a": 1}, "object"),
        ([1], "array"),
        ((1,), "array"),
        (None, "null"),
    ],
)
def test_type_family(value: object, family: str) -> None:
    assert type_family(value) == family
