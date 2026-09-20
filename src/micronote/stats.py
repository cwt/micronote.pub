"""Counts read model with a short TTL memo.

The queries live in `repository.py`; this module only memoizes them.
`cache.clear()` drops the memo whenever writers invalidate the page cache,
so the two can never disagree.
"""

from cachetools import TTLCache

from micronote import repository

_COUNTS_CACHE: TTLCache[str, dict[str, int]] = TTLCache(maxsize=1, ttl=30)


def counts() -> dict[str, int]:
    cached = _COUNTS_CACHE.get("counts")
    if cached is not None:
        return cached
    result = repository.counts()
    _COUNTS_CACHE["counts"] = result
    return result


def clear_counts() -> None:
    _COUNTS_CACHE.clear()
