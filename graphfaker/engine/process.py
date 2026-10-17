"""Running a declared process: days, timestamps, amounts, blocks.

The counterpart to :mod:`graphfaker.schema.process`. Everything here takes a
declaration and a generator and returns numpy, in the order the packs
already drew things, because the packs' datasets must not move: the
reproducibility contract says a dataset is a pure function of the schema,
the seed and the shard size within a release, and a refactor is not a
release.

That constraint shaped the interface more than anything else. ``timestamps``
makes three draws for an hour profile and two for a window, because that is
what the fraud and supply chain packs respectively did, and a single tidier
implementation would have changed both. Where the shapes genuinely differed
the difference is in the model rather than in a branch here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import numpy as np

from graphfaker.schema.process import (
    AmountModel,
    ChannelSpec,
    HourProfile,
    Lognormal,
    MonthlySchedule,
    Rounding,
    TimingModel,
)


def day_weights(timing: TimingModel, start: np.datetime64, days: int) -> np.ndarray:
    """A weight per day of the period, normalised.

    The weekday profile, times any calendar boost the model declares.
    """
    weekday = np.asarray(timing.weekday, dtype=float)
    dates = start.astype("datetime64[D]") + np.arange(days)
    # 1970-01-01 was a Thursday, so +3 puts Monday at 0.
    index = (dates.astype("datetime64[D]").astype(int) + 3) % 7
    weights = weekday[index]

    if timing.quarter_end is not None:
        months = dates.astype("datetime64[M]").astype(int) % 12
        day_of_month = (dates - dates.astype("datetime64[M]")).astype(int)
        month_length = (
            (dates.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]")
            - dates.astype("datetime64[M]").astype("datetime64[D]")
        ).astype(int)
        quarter_end_month = np.isin(months, [2, 5, 8, 11])
        near_end = day_of_month >= (month_length - timing.quarter_end.days)
        weights = weights * np.where(quarter_end_month & near_end, timing.quarter_end.factor, 1.0)

    return weights / weights.sum()


def timestamps(
    rng: np.random.Generator,
    timing: TimingModel,
    start: np.datetime64,
    weights: np.ndarray,
    size: int,
) -> np.ndarray:
    """``size`` timestamps in the period, following the day and hour models.

    ``weights`` is passed rather than recomputed because a channel draws
    many times against the same profile and the profile costs more than the
    draw.
    """
    if size <= 0:
        return np.empty(0, dtype="datetime64[s]")
    days = rng.choice(len(weights), size=size, p=weights)
    if isinstance(timing.hour, HourProfile):
        hours = timing.hour.array()
        hour = rng.choice(24, size=size, p=hours / hours.sum())
        seconds = days * 86_400 + hour * 3_600 + rng.integers(0, 3_600, size=size)
        return start + seconds.astype("timedelta64[s]")
    window = timing.hour
    seconds = (window.start_hour * 3600 + rng.integers(0, window.hours * 3600, size=size)).astype(np.int64)
    return start + (days.astype(np.int64) * 86_400 + seconds).astype("timedelta64[s]")


def monthly_dates(start: np.datetime64, days: int, day_of_month: np.ndarray) -> list[np.ndarray]:
    """For every month the period touches, the timestamp of ``day_of_month``
    per row, or NaT where that day falls outside the period."""
    first_day = start.astype("datetime64[D]").astype(dt.date)
    end = first_day + dt.timedelta(days=days)
    out = []
    year, month = first_day.year, first_day.month
    while dt.date(year, month, 1) < end:
        first = np.datetime64(dt.date(year, month, 1).isoformat(), "s")
        stamps = first + ((day_of_month - 1) * 86_400).astype("timedelta64[s]")
        lo, hi = start, start + np.timedelta64(days * 86_400, "s")
        stamps = np.where((stamps >= lo) & (stamps < hi), stamps, np.datetime64("NaT", "s"))
        out.append(stamps)
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return out


def schedule_days(rng: np.random.Generator, schedule: MonthlySchedule, size: int) -> np.ndarray:
    """The day of the month each row recurs on."""
    if schedule.days:
        weights = np.asarray(schedule.weights, dtype=float)
        return rng.choice(np.asarray(schedule.days), size=size, p=weights / weights.sum())
    return rng.integers(schedule.day_min, schedule.day_max + 1, size=size)


def amounts(rng: np.random.Generator, model: AmountModel, size: int) -> np.ndarray:
    """Amounts from the declared distribution.

    A mixture draws both arms at full size and chooses between them, which
    costs two draws it does not strictly need and is what the fraud pack
    did. Drawing only the rare arm where it is needed would be cheaper and
    would change every amount in every existing dataset.
    """
    if size <= 0:
        return np.empty(0, dtype=float)
    if isinstance(model, Lognormal):
        return rng.lognormal(model.mean, model.sigma, size=size)
    base = rng.lognormal(model.base.mean, model.base.sigma, size=size)
    rare = rng.lognormal(model.rare.mean, model.rare.sigma, size=size)
    return np.where(rng.random(size) < model.rare_rate, rare, base)


def round_amounts(rng: np.random.Generator, values: np.ndarray, rounding: Rounding) -> np.ndarray:
    """Round a share of the amounts to the step.

    Procurement invoices are round more often than chance, so a set where
    none are hands a detector a free feature.
    """
    if not len(values) or rounding.rate <= 0:
        return values
    chosen = rng.random(len(values)) < rounding.rate
    rounded = np.maximum(rounding.step, np.round(values / rounding.step) * rounding.step)
    return np.where(chosen, rounded, values)


def blocks(total: int, per_block: int) -> Iterator[tuple[int, int]]:
    """``(start, stop)`` ranges covering ``total`` in blocks.

    The process is assembled a block at a time so the arrays in flight stay
    bounded whatever the scale; at ``scale=1.0`` this is the difference
    between 15 GB and not finishing.
    """
    start = 0
    while start < total:
        stop = min(start + per_block, total)
        yield start, stop
        start = stop


def channel_budget(spec_shares: dict[str, float], budget: int) -> dict[str, int]:
    """How many events each ad hoc channel gets of the budget."""
    return {name: int(budget * share) for name, share in spec_shares.items()}


def describe(channel: ChannelSpec) -> str:
    """One line for a report or a schema dump."""
    when = (
        f"monthly, day {channel.schedule.day_min}-{channel.schedule.day_max}"
        if channel.schedule and not channel.schedule.days
        else "monthly, on set days"
        if channel.schedule
        else f"{channel.share:.0%} of the budget"
    )
    return f"{channel.name} ({channel.relationship}): {when}"
