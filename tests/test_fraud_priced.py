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
