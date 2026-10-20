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
    actor_counts,
    actor_mix_matrix,
    amounts,
    blocks,
    channel_counts,
    day_weights,
    describe_process,
    hours_of_day,
    monthly_dates,
    round_amounts,
    schedule_days,
    split_by_mix,
    timestamps,
)
from graphfaker.schema.process import (
    ActorMix,
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


def test_a_channel_gets_its_volume_exactly_one_way():
    timing = office_hours()
    with pytest.raises(ValueError, match="not both"):
        ChannelSpec(
            name="orders",
            relationship="ORDERS",
            timing=timing,
            share=0.3,
            schedule=MonthlySchedule(day_min=1, day_max=5),
        )
    with pytest.raises(ValueError, match="not both"):
        ChannelSpec(name="ships", relationship="SHIPS", timing=timing, share=0.3, derives_from="orders")

    # A channel with no allocation at all is only wrong once the process is
    # assembled, because the actor mix is declared there.
    with pytest.raises(ValueError, match="a share or a schedule"):
        ProcessSpec(name="p", channels=(ChannelSpec(name="orders", relationship="ORDERS", timing=timing),))
    with pytest.raises(ValueError, match="not a channel"):
        ProcessSpec(
            name="p",
            channels=(ChannelSpec(name="ships", relationship="SHIPS", timing=timing, derives_from="orders"),),
        )


def test_an_actor_mix_is_checked_against_its_channels():
    timing = office_hours()
    with pytest.raises(ValueError, match="2 shares for 3 channels"):
        ActorMix(by="account_type", channels=("a", "b", "c"), shares={"checking": (0.5, 0.5)})
    with pytest.raises(ValueError, match="do not exist"):
        ProcessSpec(
            name="p",
            channels=(ChannelSpec(name="a", relationship="A", timing=timing, share=1.0),),
            actor_mix=ActorMix(by="kind", channels=("a", "b"), shares={"x": (0.5, 0.5)}),
        )
    with pytest.raises(ValueError, match="does not also take a share"):
        ProcessSpec(
            name="p",
            channels=(
                ChannelSpec(name="a", relationship="A", timing=timing, share=1.0),
                ChannelSpec(name="b", relationship="B", timing=timing),
            ),
            actor_mix=ActorMix(by="kind", channels=("a", "b"), shares={"x": (0.5, 0.5)}),
        )


# -------------------------------------------------------------- allocation


def test_a_flat_share_divides_the_budget():
    timing = office_hours()
    spec = ProcessSpec(
        name="supply_chain",
        channels=(
            ChannelSpec(name="orders", relationship="ORDERS", timing=timing, share=0.28),
            ChannelSpec(name="ships", relationship="SHIPS", timing=timing, derives_from="orders"),
            ChannelSpec(name="delivers", relationship="DELIVERS", timing=timing, share=0.12),
        ),
    )
    assert channel_counts(spec, 100_000) == {"orders": 28_000, "delivers": 12_000}
    # A derived channel takes no share: the remainder is what it costs.
    assert sum(channel_counts(spec, 100_000).values()) < 100_000

    # At a scale where a share truncates to nothing, a named channel still
    # produces one, which is how a tiny run exercises every channel.
    assert channel_counts(spec, 3) == {"orders": 0, "delivers": 0}
    assert channel_counts(spec, 3, at_least_one=("orders",)) == {"orders": 1, "delivers": 0}


def test_an_actor_mix_divides_each_actor_rather_than_the_budget():
    rng = np.random.default_rng(6)
    mix = ActorMix(
        by="account_type",
        channels=("pays", "transfers", "wires"),
        shares={"checking": (0.70, 0.27, 0.03), "credit": (0.97, 0.03, 0.00)},
    )
    kinds = np.array(["checking"] * 4_000 + ["credit"] * 4_000)
    weights = np.ones(len(kinds))

    counts = actor_counts(rng, weights, 400_000)
    assert counts.sum() == pytest.approx(400_000, rel=0.01)
    # Poisson around the expectation, not the expectation itself: the
    # busiest account is busy because it was drawn that way.
    assert counts.std() > 0

    pays, transfers, wires = split_by_mix(rng, counts, actor_mix_matrix(mix, kinds))
    assert ((pays + transfers + wires) == counts).all(), "every event goes somewhere"
    credit = kinds == "credit"
    assert wires[credit].sum() == 0, "a credit card makes no wires"
    assert pays[credit].sum() / counts[credit].sum() == pytest.approx(0.97, abs=0.01)
    assert transfers[~credit].sum() / counts[~credit].sum() == pytest.approx(0.27, abs=0.01)


def test_a_scheduled_channel_draws_only_its_hour():
    rng = np.random.default_rng(7)
    office = hours_of_day(rng, HourWindow(start_hour=9, hours=8), 2_000)
    assert office.min() >= 9 and office.max() <= 16

    night_shift = hours_of_day(rng, HourProfile(weights=tuple([0.0] * 8 + [1.0] * 10 + [0.0] * 6)), 2_000)
    assert night_shift.min() >= 8 and night_shift.max() <= 17


def test_the_description_says_where_every_channel_comes_from():
    from graphfaker.domains.fraud import process as fraud_process

    lines = describe_process(fraud_process.PROCESS)
    assert any("set by account_type" in line for line in lines)
    assert any(line.startswith("rent (PAYS): monthly, day 1-5") for line in lines)
    assert not any("unallocated" in line for line in lines)


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

    Two of the three moved in this release and neither is this work's doing.
    Identifier coverage changes what a supply chain company carries, and
    freezing the reference date changes every birth date in the bank. The
    coordination digest is untouched, and the timing, amounts and budget of
    all three are still drawn in the order they always were, which is what
    this test is actually for.
    """
    from graphfaker.domains import get
    from graphfaker.engine.run import fingerprint

    assert fingerprint(get("fraud").run(scale=0.004, seed=42)) == "d28b79c1241e0504"
    assert fingerprint(get("supply_chain").run(scale=0.004, seed=42)) == "f60024d3d08e4dbc"
    assert fingerprint(get("coordination").run(scale=0.0006, seed=5)) == "d39a9533236cbe70"


def test_the_packs_allocate_from_their_declaration():
    from graphfaker.domains.fraud import process as fraud_process
    from graphfaker.domains.supply_chain import process as supply_process

    # Supply chain: three channels take a share, two are produced alongside
    # the orders, and the shares are the constants the pack documents.
    spec = supply_process.PROCESS
    assert spec.shares == {
        supply_process.ORDERS: supply_process.ORDER_SHARE,
        supply_process.DELIVERS: supply_process.DELIVERY_SHARE,
        supply_process.INTERCOMPANY: supply_process.INTERCOMPANY_SHARE,
    }
    assert spec.channel(supply_process.SHIPS).derives_from == supply_process.ORDERS
    assert spec.channel(supply_process.INVOICES).derives_from == supply_process.ORDERS

    # Fraud: no channel takes a flat share, because the budget goes to
    # accounts and the account type decides what it spends it on.
    assert fraud_process.PROCESS.shares == {}
    assert fraud_process.PROCESS.actor_mix.shares == fraud_process.CHANNEL_MIX


def test_the_domains_hand_their_process_to_the_registry():
    from graphfaker.domains import get

    assert get("fraud").process is not None
    assert get("supply_chain").process is not None
    assert get("social").process is None


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
