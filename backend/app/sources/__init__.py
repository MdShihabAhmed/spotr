"""Problem sources. Importing this package registers every built-in source."""

from app.sources import codeforces  # noqa: F401  (registers CodeforcesSource)
from app.sources.base import (
    ProblemIdentity,
    ProblemSource,
    SourceError,
    SourceMetadata,
    available_sources,
    get_source,
)

__all__ = [
    "ProblemIdentity",
    "ProblemSource",
    "SourceError",
    "SourceMetadata",
    "available_sources",
    "get_source",
]
