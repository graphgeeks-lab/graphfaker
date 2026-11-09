"""Priced scores: a detector is judged against raising no alerts at all."""

from graphfaker.domains.fraud.evaluate import Scores


def test_cost_prices_misses_against_false_alerts():
    s = Scores(tp=8, fp=30, fn=2)
    assert s.cost(20) == 2 * 20 + 30
    assert s.no_alert_cost(20) == 10 * 20
    assert s.value(20) == 200 - 70


def test_a_detector_that_flags_nothing_has_no_value():
    s = Scores(tp=0, fp=0, fn=10)
    assert s.value(20) == 0


def test_good_ranking_can_still_be_worth_nothing():
    # catches 1 of 20 fraud accounts at the cost of 30 false alerts
    s = Scores(tp=1, fp=30, fn=19)
    assert s.value(20) < 0


def test_priced_fields_only_when_a_weight_is_given():
    s = Scores(tp=1, fp=1, fn=1)
    assert "cost" not in s.as_dict()
    assert s.as_dict(miss_cost=5)["cost"] == 6


def test_the_weight_reaches_every_level_and_typology():
    """Priced numbers nobody can read out of a pipeline are ones nobody uses.

    ``Scores`` grew the parameter first and ``Evaluation`` did not, so the
    summary could print the cost while ``--json`` could not carry it.
    """
    from graphfaker.domains.fraud.evaluate import Evaluation

    result = Evaluation(
        account=Scores(tp=4, fp=10, fn=6),
        transaction=Scores(tp=1, fp=2, fn=3),
        pattern=Scores(tp=1, fp=0, fn=1),
        per_typology={"fan_in": Scores(tp=1, fp=0, fn=1)},
    )
    plain = result.as_dict()
    assert "cost" not in plain["account"] and "cost" not in plain["per_typology"]["fan_in"]

    priced = result.as_dict(miss_cost=20)
    assert priced["account"]["cost"] == 20 * 6 + 10
    assert priced["account"]["value"] == 20 * 4 - 10
    for level in ("account", "transaction", "pattern"):
        assert priced[level]["miss_cost"] == 20
    assert priced["per_typology"]["fan_in"]["cost"] == 20


def test_cost_ratio_is_the_number_that_compares_across_datasets():
    """A raw cost means nothing without knowing how much fraud was there to miss."""
    # Catches 8 of 10 for 30 false alerts: 70 against the 200 of doing nothing.
    assert Scores(tp=8, fp=30, fn=2).cost_ratio(20) == 70 / 200

    # Flagging nothing costs exactly what doing nothing costs.
    assert Scores(tp=0, fp=0, fn=10).cost_ratio(20) == 1.0

    # Worse than nothing: the false alerts outweigh what was caught.
    assert Scores(tp=1, fp=30, fn=19).cost_ratio(20) > 1.0

    # Perfect, and free.
    assert Scores(tp=10, fp=0, fn=0).cost_ratio(20) == 0.0


def test_cost_ratio_is_undefined_rather_than_zero_when_there_was_no_fraud():
    """0.0 would read as "free", which is the best possible score.

    What actually happened is that the detector raised alerts against nothing
    to catch, so the share of a zero baseline has no value to report. The
    other metrics degrade to 0.0 in their degenerate cases; this one must not,
    and None survives a round trip through JSON as null where inf and nan do
    not.
    """
    import json

    nothing_to_catch = Scores(tp=0, fp=5, fn=0)
    assert nothing_to_catch.no_alert_cost(20) == 0
    assert nothing_to_catch.cost_ratio(20) is None
    assert json.loads(json.dumps(nothing_to_catch.as_dict(20)))["cost_ratio"] is None


def test_the_summary_shows_the_share_and_a_dash_when_it_has_none():
    from graphfaker.domains.fraud.evaluate import Evaluation

    real = Evaluation(account=Scores(tp=8, fp=30, fn=2), transaction=Scores(tp=0, fp=5, fn=0),
                      pattern=Scores(tp=1, fp=0, fn=1))
    text = real.summary(miss_cost=20)
    assert "share" in text
    assert "0.35" in text, "70 of 200"
    assert "worth nothing" in text
    # The level with nothing to catch prints a dash in the share column, not
    # 0.00, which would read as free. Its value is -5: five false alerts
    # against a baseline of nothing.
    row = next(line for line in text.splitlines() if line.startswith("transaction")).split()
    assert row[-2:] == ["-", "-5"], row

    assert "share" not in real.summary(), "still opt-in"
