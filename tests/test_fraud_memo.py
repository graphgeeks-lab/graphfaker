"""Memos must not give the fraud away.

graphfaker 1.4 wrote a blank memo on every fraud transfer unless a typology chose one, and
structuring wrote "deposit", which no legitimate transfer used. Any model that read the memo
found fraud without learning laundering, and the hardness report, which measures numeric
features, did not see it.
"""

import polars as pl
import pytest

from graphfaker.domains import fraud

SCALE = 0.002


def _memo_shares(hardness: str) -> pl.DataFrame:
    run = fraud.generate(scale=SCALE, seed=3, hardness=hardness)
    tx = run.tables.edges["TRANSFERS"].select("tx_id", "memo")
    labels = run.truth["transactions"].select("tx_id", "is_fraud")
    joined = tx.join(labels, on="tx_id", how="left").with_columns(pl.col("is_fraud").fill_null(False))
    return (joined.group_by("is_fraud", "memo").len()
            .with_columns((pl.col("len") / pl.col("len").sum().over("is_fraud")).alias("share")))


@pytest.mark.parametrize("hardness", ["low", "medium", "high"])
def test_no_memo_is_used_only_by_fraud(hardness):
    shares = _memo_shares(hardness)
    fraud_memos = set(shares.filter(pl.col("is_fraud"))["memo"].to_list())
    legit_memos = set(shares.filter(~pl.col("is_fraud"))["memo"].to_list())
    assert fraud_memos <= legit_memos, f"fraud-only memos: {fraud_memos - legit_memos}"


def test_fraud_memos_look_ordinary_at_high_hardness():
    """Compared with ordinary person-to-person transfers. Payroll ("salary") is its own process,
    from employers' accounts; that it never appears on fraud marks some transfers as clearly
    legitimate, not any as fraud, so it is left out of the comparison."""
    shares = _memo_shares("high").filter(pl.col("memo") != "salary").with_columns(
        (pl.col("len") / pl.col("len").sum().over("is_fraud")).alias("share"))
    wide = shares.pivot(on="is_fraud", index="memo", values="share").fill_null(0.0)
    gap = (wide["true"] - wide["false"]).abs().max()
    assert gap < 0.15, wide
