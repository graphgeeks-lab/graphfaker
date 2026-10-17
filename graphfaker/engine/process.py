"""Running a declared process: days, timestamps, amounts, blocks.

The counterpart to :mod:`graphfaker.schema.process`. Everything here takes a
declaration and a generator and returns numpy, in the order the packs
already drew things, because the packs' datasets must not move: the
reproducibility contract says a dataset is a pure function of the schema,
the seed and the shard size within a release, and a refactor is not a
release.

It also decides how many events each channel gets. The two shapes the packs
used are both here: ``channel_counts`` for a flat share of the budget, and
``actor_counts`` plus ``split_by_mix`` for a budget spread over actors and
then divided by what kind of actor each one is.

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
    ActorMix,
    AmountModel,
    ChannelSpec,
    HourModel,
    HourProfile,
    Lognormal,
    MonthlySchedule,
    ProcessSpec,
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


def hours_of_day(rng: np.random.Generator, hour: HourModel, size: int) -> np.ndarray:
    """Which hour of the day ``size`` events land in.

    What a scheduled channel needs: the day comes from the schedule, so only
    the hour is drawn. The ad hoc path does not use this, because a window
    there draws a uniform second across the whole window rather than an hour
    and then a second inside it.
    """
    if isinstance(hour, HourProfile):
        weights = hour.array()
        return rng.choice(24, size=size, p=weights / weights.sum())
    return hour.start_hour + rng.integers(0, hour.hours, size=size)


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


def channel_counts(spec: ProcessSpec, budget: int, *, at_least_one: tuple[str, ...] = ()) -> dict[str, int]:
    """How many events each channel with a flat share gets of the budget.

    Truncating rather than rounding, because that is what the packs did and
    the remainder belongs to the channels that derive their volume from
    another. ``at_least_one`` names channels that must produce something
    even at a scale where their share truncates to nothing, which is how a
    tiny run still exercises every channel.
    """
    out = {}
    for name, share in spec.shares.items():
        count = int(budget * share)
        out[name] = max(1, count) if name in at_least_one else count
    return out


def actor_counts(rng: np.random.Generator, weights: np.ndarray, budget: int) -> np.ndarray:
    """How many events each actor produces, in proportion to its weight.

    Poisson around the expectation rather than a fixed share each, so the
    busiest account is busy because it was drawn that way rather than
    because it was told to be, and the totals come out near the budget
    without any actor being pinned to its mean.
    """
    return rng.poisson((weights / weights.sum()) * budget)


def actor_mix_matrix(mix: ActorMix, values: np.ndarray) -> np.ndarray:
    """One row of channel shares per actor, looked up by ``mix.by``."""
    return np.array([mix.shares[value] for value in values])


def split_by_mix(rng: np.random.Generator, counts: np.ndarray, mix: np.ndarray) -> list[np.ndarray]:
    """Divide each actor's events between the mix's channels.

    Binomial down the channels in order, each against what the earlier ones
    left, so the last channel takes the remainder and the totals add up
    exactly. Drawing in channel order is not an implementation detail: it
    is the order the fraud pack drew in, and the datasets must not move.
    """
    remaining = counts
    out = []
    for column in range(mix.shape[1] - 1):
        share = mix[:, column] if column == 0 else np.clip(_conditional(mix, column), 0, 1)
        taken = rng.binomial(remaining, share)
        out.append(taken)
        remaining = remaining - taken
    out.append(remaining)
    return out


def _conditional(mix: np.ndarray, column: int) -> np.ndarray:
    """A channel's share of what the channels before it left behind."""
    left = 1 - mix[:, :column].sum(axis=1)
    return np.divide(mix[:, column], left, out=np.ones(len(mix)), where=left > 0)


def describe(channel: ChannelSpec, spec: ProcessSpec | None = None) -> str:
    """One line saying where a channel's volume, timing and amounts come from."""
    parts = [_allocation(channel, spec), _timing(channel.timing)]
    if channel.amount is not None:
        parts.append(_amount(channel.amount))
    if channel.rounding is not None:
        parts.append(f"{channel.rounding.rate:.0%} rounded to {channel.rounding.step:g}")
    label = channel.name if channel.name == channel.relationship else f"{channel.name} ({channel.relationship})"
    return f"{label}: " + ", ".join(parts)


def describe_process(spec: ProcessSpec) -> list[str]:
    """The whole declaration as lines, for a report or a schema dump."""
    lines = [describe(channel, spec) for channel in spec.channels]
    if spec.actor_mix is not None:
        lines.append(
            f"the {spec.actor_mix.by} mix divides "
            + ", ".join(spec.actor_mix.channels)
            + ": "
            + "; ".join(
                f"{value} " + "/".join(f"{share:.0%}" for share in row)
                for value, row in spec.actor_mix.shares.items()
            )
        )
    return lines


def _allocation(channel: ChannelSpec, spec: ProcessSpec | None) -> str:
    if channel.share is not None:
        return f"{channel.share:.0%} of the event budget"
    if channel.derives_from is not None:
        return f"one per {channel.derives_from}"
    if channel.schedule is not None:
        when = (
            "monthly, day " + "/".join(str(d) for d in channel.schedule.days)
            if channel.schedule.days
            else f"monthly, day {channel.schedule.day_min}-{channel.schedule.day_max}"
        )
        return when if channel.participation >= 1.0 else f"{when}, {channel.participation:.0%} take part"
    if spec is not None and spec.actor_mix is not None and channel.name in spec.actor_mix.channels:
        return f"a share of each actor's events, set by {spec.actor_mix.by}"
    return "unallocated"


def _timing(timing: TimingModel) -> str:
    if isinstance(timing.hour, HourProfile):
        when = "hour-of-day profile"
    else:
        end = timing.hour.start_hour + timing.hour.hours
        when = f"{timing.hour.start_hour:02d}:00-{end:02d}:00"
    if timing.quarter_end is not None:
        when += f", x{timing.quarter_end.factor:g} over the last {timing.quarter_end.days} days of a quarter"
    return when


def _amount(model: AmountModel) -> str:
    if isinstance(model, Lognormal):
        return f"lognormal({model.mean:g}, {model.sigma:g})"
    return (
        f"lognormal({model.base.mean:g}, {model.base.sigma:g}) with "
        f"{model.rare_rate:.0%} lognormal({model.rare.mean:g}, {model.rare.sigma:g})"
    )
