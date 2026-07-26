"""Problem-source abstraction.

A `ProblemSource` knows how to (a) parse a problem's identity from a user-supplied URL
and (b) fetch only the *permitted* extras (metadata + editorial) via that source's
official API. It never scrapes restricted problem HTML — the user provides the
statement/constraints/samples.

Codeforces is the only source implemented now; the registry + ABC exist so CSES /
AtCoder / others are added as new classes with no pipeline changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProblemIdentity:
    """A source-agnostic problem identity, produced by `ProblemSource.parse_identity`."""

    source: str
    external_id: str  # source-local id, e.g. "1846C"
    url: str | None = None  # canonical display URL
    source_metadata: dict = field(default_factory=dict)  # e.g. {"contest_id": 1846, "index": "C"}

    @property
    def slug(self) -> str:
        """The public/route id: "{source}-{external_id}", e.g. "cf-1846C"."""
        return f"{SOURCE_SLUG_PREFIX.get(self.source, self.source)}-{self.external_id}"


@dataclass(frozen=True)
class SourceMetadata:
    title: str
    rating: int | None
    tags: list[str]


# Short, stable slug prefixes per source (keeps route ids compact: "cf-1846C").
SOURCE_SLUG_PREFIX = {"codeforces": "cf"}


class SourceError(Exception):
    """A source's permitted API was unreachable or returned an error."""


class ProblemSource(ABC):
    name: str

    @abstractmethod
    def parse_identity(self, url: str) -> ProblemIdentity:
        """Parse a problem URL into its identity. Raises `ValueError` on an
        unrecognizable URL (the caller turns that into a 400, never a 500)."""

    @abstractmethod
    async def fetch_metadata(self, source_metadata: dict) -> SourceMetadata | None:
        """Permitted metadata (title/rating/tags), or None if unavailable (non-fatal)."""

    @abstractmethod
    async def fetch_editorial(self, editorial_url: str) -> str | None:
        """Editorial text via the source's permitted API, or None (best-effort)."""


_REGISTRY: dict[str, ProblemSource] = {}


def register_source(source: ProblemSource) -> None:
    _REGISTRY[source.name] = source


def get_source(name: str) -> ProblemSource:
    """Return the source strategy for `name`. Raises `ValueError` if unknown."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown problem source: {name!r}") from None


def available_sources() -> list[str]:
    return sorted(_REGISTRY)
