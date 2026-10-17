"""The process vocabulary, and the packs that were lifted onto it.

The test that matters most is the last one: the packs' datasets did not move.
A refactor is not a release, and the reproducibility contract says a dataset
is a pure function of the schema, the seed and the shard size within one.
Everything above it checks that the declarations mean what they say.
"""

from __future__ import annotations

import numpy as np
import pytest

from graphfaker.engine.process import (
    amounts,
    blocks,
    day_weights,
    monthly_dates,
    round_amounts,
    schedule_days,
    timestamps,
)
from graphfaker.schema.process import (
    ChannelSpec,
    HourProfile,
    HourWindow,
    Lognormal,
    Mixture,
    MonthlySchedule,
    ProcessSpec,
    QuarterEndBoost,
    Rounding,
    TimingModel,
)

START = np.datetime64("2026-01-01T00:00:00", "s")
DAYS = 120
WEEKDAY = (1.15, 1.2, 1.15, 1.1, 0.95, 0.25, 0.15)


def office_hours() -> TimingModel:
    return TimingModel(weekday=WEEKDAY, hour=HourWindow(start_hour=8, hours=10))


# ------------------------------------------------------------------ timing


def test_the_weekday_profile_lands_on_the_right_days():
    import datetime as dt

    weights = day_weights(office_hours(), START, DAYS)
    assert weights.shape == (DAYS,)
    assert weights.sum() == pytest.approx(1.0)

    # Each day of the period carries the weight of the weekday it actually
    # falls on. Checked against the calendar rather than against another
    # index into the same array, because an off-by-one here shifts every
    # event in every dataset by a day and nothing else would notice.
    first = START.astype("datetime64[D]").astype(dt.date)
    scale = weights.max() / max(WEEKDAY)
    for offset in range(14):
        date = first + dt.timedelta(days=offset)
        assert weights[offset] == pytest.approx(WEEKDAY[date.weekday()] * scale), date


def test_the_quarter_end_boost_only_lifts_quarter_ends():
    plain = day_weights(office_hours(), START, DAYS)
    boosted = day_weights(
        office_hours().model_copy(update={"quarter_end": QuarterEndBoost(factor=2.4, days=6)}),
        START,
        DAYS,
    )
    # March is the first quarter end inside a period starting in January.
    march_end = (np.datetime64("2026-03-28") - START.astype("datetime64[D]")).astype(int)
    january = 10
    assert boosted[march_end] / boosted[january] > plain[march_end] / plain[january]
    assert boosted.sum() == pytest.approx(1.0)


def test_an_hour_window_puts_everything_inside_it():
    rng = np.random.default_rng(0)
    timing = office_hours()
    stamps = timestamps(rng, timing, START, day_weights(timing, START, DAYS), 5_000)
    hours = stamps.astype("datetime64[h]").astype(int) % 24
    assert hours.min() >= 8 and hours.max() <= 17, (hours.min(), hours.max())


def test_an_hour_profile_follows_its_weights():
    rng = np.random.default_rng(0)
    quiet_night = [0.05] * 6 + [1.0] * 12 + [0.5] * 6
    timing = TimingModel(weekday=WEEKDAY, hour=HourProfile(weights=tuple(quiet_night)))
    stamps = timestamps(rng, timing, START, day_weights(timing, START, DAYS), 20_000)
    hours = stamps.astype("datetime64[h]").astype(int) % 24
    nights = (hours < 6).mean()
    assert 0.01 < nights < 0.05, nights


def test_no_rows_is_an_empty_result_rather_than_an_error():
    timing = office_hours()
    assert len(timestamps(np.random.default_rng(0), timing, START, day_weights(timing, START, DAYS), 0)) == 0
    assert len(amounts(np.random.default_rng(0), Lognormal(mean=4.0, sigma=1.0), 0)) == 0
    assert list(blocks(0, 10)) == []


# ---------------------------------------------------------------- schedules


def test_a_monthly_schedule_recurs_on_the_same_day_every_month():
    rng = np.random.default_rng(1)
    days = schedule_days(rng, MonthlySchedule(day_min=1, day_max=5), 50)
    assert days.min() >= 1 and days.max() <= 5

    months = monthly_dates(START, DAYS, days)
    # 120 days from 1 January is 31 + 28 + 31 + 30, so the period ends on
    # 30 April and touches four months.
    assert len(months) == 4
    for month in months:
        inside = month[~np.isnat(month)]
        assert ((inside - START).astype("timedelta64[D]").astype(int) < DAYS).all()
    # The day of the month is the same in each, which is what recurring means.
    first, second = months[0], months[1]
    both = ~np.isnat(first) & ~np.isnat(second)
    assert (
        first[both].astype("datetime64[D]").astype(object) == second[both].astype("datetime64[D]").astype(object)
    ).sum() == 0, "different months"
    day_of = lambda s: np.array([d.day for d in s.astype("datetime64[D]").astype(object)])  # noqa: E731
    assert (day_of(first[both]) == day_of(second[both])).all()


def test_a_weighted_schedule_uses_the_days_it_was_given():
    rng = np.random.default_rng(2)
    payday = MonthlySchedule(day_min=1, day_max=28, days=(25, 26, 27, 28, 1), weights=(0.4, 0.15, 0.15, 0.2, 0.1))
    days = schedule_days(rng, payday, 4_000)
    assert set(np.unique(days).tolist()) <= {25, 26, 27, 28, 1}
    assert (days == 25).mean() == pytest.approx(0.4, abs=0.03)


def test_a_schedule_is_checked_when_it_is_written():
    with pytest.raises(ValueError, match="day_max is before day_min"):
        MonthlySchedule(day_min=20, day_max=3)
    with pytest.raises(ValueError, match="one weight per day"):
        MonthlySchedule(day_min=1, day_max=28, days=(1, 2), weights=(1.0,))


# ------------------------------------------------------------------ amounts


def test_a_mixture_has_the_tail_one_lognormal_cannot():
    rng = np.random.default_rng(3)
    base = Lognormal(mean=4.6, sigma=1.1)
    mixture = Mixture(base=base, rare=Lognormal(mean=7.8, sigma=0.9), rare_rate=0.08)

    plain = amounts(rng, base, 100_000)
    mixed = amounts(rng, mixture, 100_000)
    assert np.median(mixed) == pytest.approx(np.median(plain), rel=0.15), "the middle is the same"
    assert np.quantile(mixed, 0.99) > 3 * np.quantile(plain, 0.99), "the tail is not"


def test_rounding_rounds_the_measured_share_and_leaves_the_rest():
    rng = np.random.default_rng(4)
    values = amounts(rng, Lognormal(mean=8.0, sigma=0.6), 20_000)
    rounded = round_amounts(rng, values, Rounding(step=500.0, rate=0.22))
    was_rounded = rounded % 500.0 == 0
    assert was_rounded.mean() == pytest.approx(0.22, abs=0.02)
    assert (rounded[was_rounded] >= 500.0).all(), "rounding down to nothing is not rounding"
    assert (rounded[~was_rounded] == values[~was_rounded]).all(), "the rest are untouched"
    # A rate of zero is the identity, so a pack can turn it off.
    assert (round_amounts(rng, values, Rounding(step=500.0, rate=0.0)) == values).all()


# ------------------------------------------------------------------- blocks


def test_blocks_cover_everything_exactly_once():
    ranges = list(blocks(10_000, 3_000))
    assert ranges == [(0, 3000), (3000, 6000), (6000, 9000), (9000, 10000)]
    assert sum(stop - start for start, stop in ranges) == 10_000


# -------------------------------------------------------------------- spec


def test_a_channel_is_scheduled_or_takes_a_share():
    timing = office_hours()
    with pytest.raises(ValueError, match="a share or a schedule"):
        ChannelSpec(name="orders", relationship="ORDERS", timing=timing)
    with pytest.raises(ValueError, match="not both"):
        ChannelSpec(
            name="orders",
            relationship="ORDERS",
            timing=timing,
            share=0.3,
            schedule=MonthlySchedule(day_min=1, day_max=5),
        )


def test_a_process_reports_the_shares_that_divide_the_budget():
    timing = office_hours()
    spec = ProcessSpec(
        name="supply_chain",
        channels=(
            ChannelSpec(name="orders", relationship="ORDERS", timing=timing, share=0.28),
            ChannelSpec(name="ships", relationship="SHIPS", timing=timing, share=0.3),
            ChannelSpec(
                name="rent", relationship="PAYS", timing=timing, schedule=MonthlySchedule(day_min=1, day_max=5)
            ),
        ),
    )
    assert spec.shares == {"orders": 0.28, "ships": 0.3}, "a scheduled channel takes no share"
    assert spec.channel("ships").relationship == "SHIPS"
    with pytest.raises(KeyError):
        spec.channel("nope")


# --------------------------------------------------- the packs, unmoved


def test_the_packs_declare_their_timing_and_still_produce_the_same_bytes():
    """The acceptance criterion for the lift.

    These digests were taken before a line of it was written. If the lifted
    engine draws in a different order from the code it replaced, every one
    of them changes, which is the only check that matters here.
    """
    from graphfaker.domains import get
    from graphfaker.engine.run import fingerprint

    assert fingerprint(get("fraud").run(scale=0.004, seed=42)) == "352db296d8963ab7"
    assert fingerprint(get("supply_chain").run(scale=0.004, seed=42)) == "d93687863b22aa68"
    assert fingerprint(get("coordination").run(scale=0.0006, seed=5)) == "d39a9533236cbe70"


def test_the_packs_timing_is_a_declaration_now():
    from graphfaker.domains.fraud import process as fraud_process
    from graphfaker.domains.supply_chain import process as supply_process

    assert isinstance(supply_process.TIMING, TimingModel)
    assert isinstance(supply_process.TIMING.hour, HourWindow)
    assert supply_process.TIMING.quarter_end is not None

    assert set(fraud_process.TIMING) == set(fraud_process.CHANNELS)
    assert isinstance(fraud_process.TIMING["WIRES"].hour, HourProfile)
    # A wire on a Saturday is nearly unheard of; a card payment is not.
    assert fraud_process.TIMING["WIRES"].weekday[5] < fraud_process.TIMING["PAYS"].weekday[5] / 3
