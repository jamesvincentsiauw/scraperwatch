from pathlib import Path

import pytest

from scraperwatch.config import DEFAULT_STORE, Config, load_config

EXAMPLE = """
store = "sqlite:///data/sw.db"
baseline_runs = 7
empty_values = ["N/A", "Tidak ada"]

[targets.shopee-prices]
required = ["title", "price"]
key = ["platform", "product_id"]
min_items = 50

[targets.shopee-prices.fields.discount]
severity = "ignore"

[targets.shopee-prices.fields.stock]
severity = "required"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "scraperwatch.toml"
    path.write_text(text)
    return path


def test_defaults_without_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config(environ={})
    assert config == Config()
    assert config.store == DEFAULT_STORE
    settings = config.target_settings("anything")
    assert settings.required == ()
    assert settings.baseline_runs == 10


def test_file_settings_merge_with_defaults(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, EXAMPLE), environ={})
    assert config.store == "sqlite:///data/sw.db"

    s = config.target_settings("shopee-prices")
    assert s.required == ("title", "price", "stock")
    assert s.key == ("platform", "product_id")
    assert s.ignored == frozenset({"discount"})
    assert (s.baseline_runs, s.min_items) == (7, 50)
    assert s.empty_values == ("N/A", "Tidak ada")

    other = config.target_settings("other")
    assert (other.baseline_runs, other.min_items, other.required) == (7, 30, ())


def test_code_arguments_win(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, EXAMPLE), environ={})
    s = config.target_settings("shopee-prices", required=["title"], key=["url"], ignored=[])
    assert (s.required, s.key, s.ignored) == (("title",), ("url",), frozenset())


def test_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, EXAMPLE)
    monkeypatch.chdir(tmp_path)
    config = load_config(
        environ={
            "SCRAPERWATCH_CONFIG": str(path),
            "SCRAPERWATCH_STORE": "/tmp/other.db",
            "SCRAPERWATCH_EVIDENCE_DIR": "/tmp/evidence",
        }
    )
    assert config.store == "/tmp/other.db"
    assert str(config.evidence_dir) == "/tmp/evidence"
    assert "shopee-prices" in config.targets


def test_config_file_in_working_directory_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, 'store = "found.db"\n')
    monkeypatch.chdir(tmp_path)
    assert load_config(environ={}).store == "found.db"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("stor = 'x'\n", "unknown settings"),
        ("[targets.a]\nrequierd = ['x']\n", "unknown settings"),
        ("[targets.a.fields.b]\nseverity = 'loud'\n", "severity must be one of"),
        ("targets = 3\n", "must be a table"),
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(write(tmp_path, text), environ={})


def test_required_and_ignored_conflict(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, EXAMPLE), environ={})
    with pytest.raises(ValueError, match="both required and ignored"):
        config.target_settings("shopee-prices", required=["discount"])
