"""Configuration from scraperwatch.toml and SCRAPERWATCH_* environment variables."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from scraperwatch.settings import TargetSettings

CONFIG_FILE = "scraperwatch.toml"
DEFAULT_STORE = ".scraperwatch/scraperwatch.db"
DEFAULT_EVIDENCE_DIR = ".scraperwatch/evidence"

_TOP_LEVEL = {
    "store",
    "evidence_dir",
    "evidence",
    "baseline_runs",
    "min_items",
    "empty_values",
    "targets",
}
_TARGET_KEYS = {"required", "key", "baseline_runs", "min_items", "empty_values", "fields"}
_SEVERITIES = {"ignore", "required", "default"}


@dataclass(frozen=True)
class Config:
    store: str = DEFAULT_STORE
    evidence_dir: Path = Path(DEFAULT_EVIDENCE_DIR)
    evidence: bool = True
    defaults: Mapping[str, Any] = field(default_factory=dict[str, Any])
    targets: Mapping[str, Mapping[str, Any]] = field(default_factory=dict[str, Mapping[str, Any]])

    def target_settings(
        self,
        target: str,
        *,
        required: Sequence[str] | None = None,
        key: Sequence[str] | None = None,
        ignored: Sequence[str] | None = None,
    ) -> TargetSettings:
        """Merges global defaults, the target's table and arguments passed in code, in that order."""
        table = {**self.defaults, **self.targets.get(target, {})}
        required_set = list(table.get("required", []))
        ignored_set: list[str] = []
        for name, options in table.get("fields", {}).items():
            severity = options.get("severity", "default")
            if severity == "ignore":
                ignored_set.append(name)
            elif severity == "required" and name not in required_set:
                required_set.append(name)

        kwargs: dict[str, Any] = {
            "required": tuple(required if required is not None else required_set),
            "key": tuple(key if key is not None else table.get("key", [])),
            "ignored": frozenset(ignored if ignored is not None else ignored_set),
        }
        for name in ("baseline_runs", "min_items"):
            if name in table:
                kwargs[name] = int(table[name])
        if "empty_values" in table:
            kwargs["empty_values"] = tuple(table["empty_values"])
        return TargetSettings(**kwargs)


def load_config(path: Path | None = None, environ: Mapping[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    if path is None and env.get("SCRAPERWATCH_CONFIG"):
        path = Path(env["SCRAPERWATCH_CONFIG"])
    if path is None and Path(CONFIG_FILE).is_file():
        path = Path(CONFIG_FILE)

    data: dict[str, Any] = {}
    if path is not None:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    _validate(data, path)

    defaults = {k: data[k] for k in ("baseline_runs", "min_items", "empty_values") if k in data}
    return Config(
        store=env.get("SCRAPERWATCH_STORE") or data.get("store", DEFAULT_STORE),
        evidence_dir=Path(
            env.get("SCRAPERWATCH_EVIDENCE_DIR") or data.get("evidence_dir", DEFAULT_EVIDENCE_DIR)
        ),
        evidence=bool(data.get("evidence", True)),
        defaults=defaults,
        targets=data.get("targets", {}),
    )


def _validate(data: Mapping[str, Any], path: Path | None) -> None:
    where = str(path) if path else "configuration"
    unknown = set(data) - _TOP_LEVEL
    if unknown:
        raise ValueError(f"{where}: unknown settings {sorted(unknown)}")
    targets: object = data.get("targets", {})
    if not isinstance(targets, Mapping):
        raise ValueError(f"{where}: [targets] must be a table")
    for name, table in cast(Mapping[str, object], targets).items():
        if not isinstance(table, Mapping):
            raise ValueError(f"{where}: [targets.{name}] must be a table")
        table = cast(Mapping[str, Any], table)
        unknown = set(table) - _TARGET_KEYS
        if unknown:
            raise ValueError(f"{where}: [targets.{name}] has unknown settings {sorted(unknown)}")
        fields = cast(Mapping[str, Mapping[str, Any]], table.get("fields", {}))
        for fname, options in fields.items():
            if options.get("severity", "default") not in _SEVERITIES:
                raise ValueError(
                    f"{where}: [targets.{name}.fields.{fname}] severity must be one of {sorted(_SEVERITIES)}"
                )
