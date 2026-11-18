"""Source connector contract. Add a new source = add one file implementing Source."""
from __future__ import annotations

import abc
import logging

import httpx

from ..models import RawLead, SearchQuery

log = logging.getLogger(__name__)


class SourceUnavailable(Exception):
    """Raised when a source can't run (missing key, unsupported country...)."""


class Source(abc.ABC):
    name: str = "base"
    countries: set[str] | None = None   # None = all countries
    needs_key: bool = False

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    def supports(self, q: SearchQuery) -> bool:
        return self.countries is None or q.country in self.countries

    @abc.abstractmethod
    def available(self) -> bool:
        """True if configured (API key present etc.)."""

    @abc.abstractmethod
    async def search(self, q: SearchQuery) -> list[RawLead]:
        """Return businesses for the query. Must not raise on 'no results'."""
