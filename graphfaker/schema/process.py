"""When events happen, how often, and how big they are.

A domain pack used to answer those three questions in its own
``process.py``, and the three packs answered them the same way with
different constants: a weekday profile turned into a day weight per day of
the period, a draw of days and hours into timestamps, a lognormal or a
mixture of two into amounts, a monthly schedule into recurring dates. This
module is those answers as declarations, and :mod:`graphfaker.engine.process`
runs them.

The split is deliberate and is the one the designer work depends on. What a
process *is* belongs in the schema, where it can be written in YAML, checked
by pydantic and compiled from a description. Who sends money to whom stays
in the pack, because partner selection is the part that is genuinely
different each time: a merchant chosen by region and category, a contact
from a repeat list, a supplier with a contract. A timing model is not.

Nothing here draws anything. These are descriptions, and the same
description must produce the same draws for the same seed, which is why the
engine's functions take them rather than the other way round.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

#: Weekday weights run Monday to Sunday, which is what ``date.weekday()``
#: returns and what every profile in the packs was already indexed by.
WEEKDAYS = 7


class HourProfile(BaseModel):
    """Hour of day from 24 weights, then a random second inside the hour.

    The shape the fraud pack uses: a card payment is likelier at 18:00 than
    at 04:00, and a 03:00 transaction is a thing a detector could notice.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = "profile"
    weights: tuple[float, ...] = Field(min_length=24, max_length=24)

    def array(self) -> np.ndarray:
        return np.asarray(self.weights, dtype=float)


class HourWindow(BaseModel):
    """A uniform second inside a window of the day.

    The shape the supply chain pack uses: an order is placed in office
    hours, so a 03:00 order is an anomaly rather than a rare normal event.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = "window"
    start_hour: int = Field(ge=0, le=23)
    hours: int = Field(gt=0, le=24)


HourModel = HourProfile | HourWindow


class QuarterEndBoost(BaseModel):
    """The last days of a calendar quarter, weighted up.

    A cluster of purchases at quarter end is normal, which is exactly why it
    is a good place to hide a split-order scheme.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    factor: float = Field(gt=0.0)
    days: int = Field(gt=0, le=28)


class TimingModel(BaseModel):
    """Which day of the period an event lands on, and which second of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Monday to Sunday, relative. Normalised over the period by the engine.
    weekday: tuple[float, ...] = Field(min_length=WEEKDAYS, max_length=WEEKDAYS)
    hour: HourModel
    quarter_end: QuarterEndBoost | None = None


class MonthlySchedule(BaseModel):
    """A day of the month, every month of the period.

    Rent on the first, salary on the 25th, a subscription on whichever day
    it was taken out. The day is drawn per row and then reused for every
    month, which is what makes the flow recurring rather than merely
    frequent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: An inclusive range the day is drawn uniformly from.
    day_min: int = Field(ge=1, le=28)
    day_max: int = Field(ge=1, le=28)
    #: Or an explicit set of days with weights, for a payday.
    days: tuple[int, ...] = ()
    weights: tuple[float, ...] = ()

    def model_post_init(self, _: object) -> None:
        if self.days and len(self.days) != len(self.weights):
            raise ValueError("a weighted schedule needs one weight per day")
        if not self.days and self.day_max < self.day_min:
            raise ValueError("day_max is before day_min")


class Lognormal(BaseModel):
    """The workhorse: most amounts small, a long tail of large ones."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = "lognormal"
    mean: float
    sigma: float = Field(gt=0.0)


class Mixture(BaseModel):
    """Two lognormals, one of them rare.

    A legitimate transfer is usually a few tens and occasionally a car. One
    distribution cannot be both, and a detector trained on the single
    distribution learns a threshold that the real tail walks straight
    through.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = "mixture"
    base: Lognormal
    rare: Lognormal
    rare_rate: float = Field(ge=0.0, le=1.0)


AmountModel = Lognormal | Mixture


class Rounding(BaseModel):
    """Round numbers at a measured rate.

    Procurement invoices are round far more often than chance, so a
    generated set where none are makes "is it round" a free detector.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    step: float = Field(gt=0.0)
    rate: float = Field(ge=0.0, le=1.0)


class ChannelSpec(BaseModel):
    """One stream of events: a relationship, a share of the budget, timing
    and amounts.

    ``share`` is of the whole event budget. A recurring channel sets
    ``schedule`` instead and takes whatever its participation produces,
    which is why the two cannot both be set.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    relationship: str
    timing: TimingModel
    amount: AmountModel | None = None
    rounding: Rounding | None = None
    share: float | None = Field(default=None, ge=0.0, le=1.0)
    schedule: MonthlySchedule | None = None
    #: Share of candidates that take part in a recurring flow at all.
    participation: float = Field(default=1.0, ge=0.0, le=1.0)

    def model_post_init(self, _: object) -> None:
        if self.share is not None and self.schedule is not None:
            raise ValueError(f"{self.name}: a channel is scheduled or it takes a share, not both")
        if self.share is None and self.schedule is None:
            raise ValueError(f"{self.name}: give the channel a share or a schedule")


class ProcessSpec(BaseModel):
    """Every channel of a domain's ordinary behaviour.

    The shares of the ad hoc channels are what divides the event budget.
    They are not required to sum to one: a pack may leave room for the
    recurring flows it declares separately.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    channels: tuple[ChannelSpec, ...] = Field(min_length=1)
    #: Events per block when the process is assembled block-wise, which is
    #: what keeps peak memory flat at any scale.
    block_events: int = Field(default=2_000_000, gt=0)

    def channel(self, name: str) -> ChannelSpec:
        for channel in self.channels:
            if channel.name == name:
                return channel
        raise KeyError(name)

    @property
    def shares(self) -> dict[str, float]:
        return {c.name: c.share for c in self.channels if c.share is not None}
