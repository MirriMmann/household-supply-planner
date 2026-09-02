from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from household_supply.domain import MarketAcquisitionBatch


@runtime_checkable
class MarketProvider(Protocol):
    """External acquisition mechanism.

    Implementations may call an API, read a file, scrape a site, or use any
    other mechanism. The core only accepts the attributable batch returned by
    this boundary.
    """

    @property
    def provider_id(self) -> str: ...

    def acquire(self) -> MarketAcquisitionBatch: ...


def acquire_market(provider: MarketProvider) -> MarketAcquisitionBatch:
    provider_id = provider.provider_id.strip()
    if not provider_id:
        raise ValueError("market provider_id must not be empty")
    batch = provider.acquire()
    if batch.provider_id != provider_id:
        raise ValueError("market provider returned a batch under a different provider_id")
    return batch


@dataclass(frozen=True, slots=True)
class StaticMarketProvider:
    """Deterministic provider used by fixtures and adapter contract tests."""

    batch: MarketAcquisitionBatch

    @property
    def provider_id(self) -> str:
        return self.batch.provider_id

    def acquire(self) -> MarketAcquisitionBatch:
        return self.batch
