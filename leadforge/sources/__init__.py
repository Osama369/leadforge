from __future__ import annotations

from .base import Source
from .directories import directory_sources
from .google_places import GooglePlacesSource
from .serper import SerperPlacesSource


def all_sources(client) -> list[Source]:
    """Every registered discovery source. Order = priority (API data first)."""
    return [GooglePlacesSource(client), SerperPlacesSource(client), *directory_sources(client)]


__all__ = ["Source", "all_sources"]
