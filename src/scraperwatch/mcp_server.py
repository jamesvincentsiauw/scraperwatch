"""MCP server that lets an agent read recorded runs. Needs the `mcp` extra."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scraperwatch import __version__
from scraperwatch.config import Config, load_config
from scraperwatch.queries import Queries
from scraperwatch.store import open_store, store_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

INSTRUCTIONS = (
    "Read-only access to scraperwatch runs. Start with list_runs to find a run id, "
    "get_run for its findings, and compare_run to see which fields fell against the baseline."
)


def build_server(queries: Queries) -> FastMCP:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("scraperwatch", instructions=INSTRUCTIONS)

    @server.tool()
    def list_runs(target: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Recent runs, newest first, optionally for one target. Status is pass, warn or quarantine."""
        return queries.list_runs(target, limit)

    @server.tool()
    def get_run(run_id: str) -> dict[str, Any]:
        """A run's status, counts and findings, with sample URLs for each finding."""
        return queries.get_run(run_id)

    @server.tool()
    def compare_run(run_id: str) -> dict[str, Any]:
        """Per field: how often it was filled in this run, and its usual range in the baseline."""
        return queries.compare_run(run_id)

    @server.tool()
    def get_baseline(target: str) -> dict[str, Any]:
        """What the next run of a target will be compared with: usual fill rate and range per field."""
        return queries.get_baseline(target)

    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraperwatch-mcp", description="Serve scraperwatch runs to an MCP client over stdio."
    )
    parser.add_argument("--version", action="version", version=f"scraperwatch {__version__}")
    parser.add_argument("--config", type=Path, help="path to scraperwatch.toml")
    parser.add_argument("--store", help="store to use instead of the configured one")
    return parser


def open_queries(config: Config, store_url: str | None = None) -> Queries:
    url = store_url or config.store
    # Opening a missing store would create an empty one and hide a wrong path.
    if not store_path(url).exists():
        raise FileNotFoundError(f"No scraperwatch store at {store_path(url)}. Pass --store.")
    return Queries(open_store(url), config)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        queries = open_queries(load_config(args.config), args.store)
        server = build_server(queries)
    except ImportError:
        print(
            "scraperwatch-mcp needs the mcp extra: pip install 'scraperwatch[mcp]'", file=sys.stderr
        )
        return 1
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
