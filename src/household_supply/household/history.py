from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .events import HouseholdEvent, event_effective_at


@dataclass(frozen=True, slots=True)
class HouseholdHistory:
    """Immutable append-only fact set used to derive household state."""

    events: tuple[HouseholdEvent, ...] = ()

    def __post_init__(self) -> None:
        events = tuple(self.events)
        event_ids = [event.event_id for event in events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("household history contains duplicate event ids")
        object.__setattr__(self, "events", events)

    def through(self, as_of: datetime) -> HouseholdHistory:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("household history as_of must be timezone-aware")
        return HouseholdHistory(
            tuple(
                event
                for event in self.events
                if event.recorded_at <= as_of and event_effective_at(event) <= as_of
            )
        )
