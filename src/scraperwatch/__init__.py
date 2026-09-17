"""Catch scrapers that report success but return empty or incomplete data."""

import logging

__version__ = "0.1.0.dev0"

# Applications decide whether scraperwatch's log lines are shown.
logging.getLogger("scraperwatch").addHandler(logging.NullHandler())

# Imported after __version__, which scraperwatch.run reads.
from scraperwatch.config import Config, load_config  # noqa: E402
from scraperwatch.model import Finding, Quarantined, Severity, Status, Verdict  # noqa: E402
from scraperwatch.run import Run, run  # noqa: E402

__all__ = [
    "Config",
    "Finding",
    "Quarantined",
    "Run",
    "Severity",
    "Status",
    "Verdict",
    "__version__",
    "load_config",
    "run",
]
