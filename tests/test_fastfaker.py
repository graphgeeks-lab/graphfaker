"""Vectorised Faker draws and vectorised simple samplers.

The point of both is speed without changing what is drawn: the same tables,
the same weights, the same parameter references, from the shard's stream.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter

import numpy as np
import pytest
from faker import Faker

from graphfaker.engine.fastfaker import FastFaker, Unsupported
from graphfaker.engine.sampling import RowContext, sample_column
from graphfaker.engine.seeding import Streams
from graphfaker.schema.samplers import (
    BernoulliSampler,
    CategorySampler,
    ConstantSampler,
    FakerSampler,
    GaussianSampler,
    LognormalSampler,
    PoissonSampler,
    ReferenceSampler,
    UniformSampler,
)


@pytest.fixture(scope="module")
def fake():
    fake = Faker()
    fake.seed_instance(0)
    return fake


@pytest.fixture(scope="module")
def fast(fake):
    return FastFaker(fake)


@pytest.fixture
def rng():
    return np.random.default_rng(7)


def _person(fake):
    return next(p for p in fake.factories[0].providers if ".person" in type(p).__module__)


# ------------------------------------------------------------------ faker


def test_names_come_from_fakers_tables_with_fakers_weights(fake, fast, rng):
    person = _person(fake)
    firsts = set(person.first_names_male) | set(person.first_names_female) | set(person.first_names_nonbinary)
    lasts = set(person.last_names)
    names = fast.draw("name", {}, 5000, rng)
    assert all(any(w in firsts for w in n.split()) and any(w in lasts for w in n.split()) for n in names)

    counts = Counter(fast.draw("first_name", {}, 100000, rng))
    weights = person.first_names
    total = sum(weights.values())
    top = counts.most_common(1)[0][0]
    assert abs(counts[top] / 100000 - weights[top] / total) < 0.005, "the most common name appears at Faker's weight"


def test_formats_placeholders_and_composition(fast, rng):
    phones = fast.draw("phone_number", {}, 2000, rng)
    assert all(re.fullmatch(r"[+0-9().x-]+", p) for p in phones)
    assert not any(c in "".join(phones) for c in "#%$!@?"), "every placeholder is filled"
    assert any("x" in p for p in phones), "extensions occur"
    assert all(int(p.lstrip("(+0-1")[0]) >= 2 for p in phones if p.lstrip("(+0-1")), "$ draws 2-9"

    streets = fast.draw("street_address", {}, 2000, rng)
    assert all(s.split()[0].isdigit() for s in streets), "a building number first"
    assert any("Apt." in s or "Suite" in s for s in streets)

    emails = fast.draw("email", {}, 2000, rng)
    assert all(re.fullmatch(r"[a-z0-9._-]+@example\.(org|com|net)", e) for e in emails), emails[:3]

    cities = fast.draw("city", {}, 500, rng)
    assert all(c[0].isupper() for c in cities)
    companies = fast.draw("company", {}, 500, rng)
    assert any(c.endswith(("Inc", "LLC", "Group", "PLC", "Ltd")) or "," in c or "-" in c for c in companies)


def test_direct_providers(fast, rng):
    today = dt.datetime.now(tz=dt.timezone.utc).date()
    dobs = fast.draw("date_of_birth", {"minimum_age": 18, "maximum_age": 85}, 5000, rng)
    ages = [(today - d).days / 365.25 for d in dobs]
    assert 18 <= min(ages) and max(ages) < 86.1
    assert len(set(fast.draw("uuid4", {}, 1000, rng))) == 1000
    assert all(re.fullmatch(r"[0-9a-f-]{36}", u) for u in fast.draw("uuid4", {}, 10, rng))
    assert all(len(c) == 2 and c.isupper() for c in fast.draw("country_code", {}, 100, rng))
    words = fast.draw("words", {"nb": 3}, 10, rng)
    assert all(isinstance(w, list) and len(w) == 3 for w in words)
    assert all(-90 <= v <= 90 for v in fast.draw("latitude", {}, 100, rng))
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in fast.draw("date", {}, 10, rng))


def test_unsupported_providers_are_reported_not_faked(fast, rng):
    assert not fast.supports("iban", {})
    assert not fast.supports("catch_phrase", {})
    assert not fast.supports("name", {"unknown_kwarg": 1})
    with pytest.raises(Unsupported):
        fast.draw("iban", {}, 3, rng)


def test_draws_are_reproducible_from_the_stream(fast):
    a = fast.draw("street_address", {}, 200, np.random.default_rng(3))
    b = fast.draw("street_address", {}, 200, np.random.default_rng(3))
    c = fast.draw("street_address", {}, 200, np.random.default_rng(4))
    assert a == b and a != c


# ---------------------------------------------------------------- engine


def _ctx(seed: int = 1, groups: int = 3, count: int = 3000) -> RowContext:
    from graphfaker.engine.sampling import GroupParams

    streams = Streams.root(seed)
    ctx = RowContext(streams=streams)
    ctx.group_ids["g"] = streams.rng.integers(0, groups, size=count)
    ctx.latent["g"] = GroupParams(factor="g", values=[{"mean": 10.0 * (k + 1), "label": f"g{k}", "p": 0.1 * (k + 1)} for k in range(groups)])
    return ctx


def test_simple_samplers_are_vectorised_and_honour_references():
    ctx = _ctx()
    n = 3000
    means = sample_column(GaussianSampler(mean="@g.mean", sd=0.01), n, ctx)
    by_group = {k: np.mean([m for m, g in zip(means, ctx.group_ids["g"]) if g == k]) for k in range(3)}
    assert all(abs(by_group[k] - 10.0 * (k + 1)) < 0.01 for k in range(3)), by_group

    labels = sample_column(ReferenceSampler(ref="@g.label"), n, ctx)
    assert labels[:5] == [f"g{g}" for g in ctx.group_ids["g"][:5]]

    flags = sample_column(BernoulliSampler(p="@g.p"), n, ctx)
    rate = {k: np.mean([f for f, g in zip(flags, ctx.group_ids["g"]) if g == k]) for k in range(3)}
    assert rate[0] < rate[1] < rate[2]

    ints = sample_column(UniformSampler(low=1, high=6, integer=True), n, ctx)
    assert set(ints) == {1, 2, 3, 4, 5, 6}
    floats = sample_column(UniformSampler(low=0.0, high=1.0, decimals=2), n, ctx)
    assert all(isinstance(v, float) and 0 <= v <= 1 and round(v, 2) == v for v in floats)
    cats = sample_column(CategorySampler(values=["a", "b", "c"], weights=[1, 1, 8]), n, ctx)
    assert Counter(cats)["c"] > 0.7 * n
    assert sample_column(ConstantSampler(value="USD"), 4, ctx) == ["USD"] * 4
    assert all(isinstance(v, int) for v in sample_column(PoissonSampler(lam=3.0), 10, ctx))
    logs = sample_column(LognormalSampler(mu=0.0, sigma=0.5, decimals=3), n, ctx)
    assert abs(float(np.median(logs)) - 1.0) < 0.1
    clipped = sample_column(GaussianSampler(mean=0.0, sd=100.0, low=-1.0, high=1.0, integer=True), n, ctx)
    assert set(clipped) <= {-1, 0, 1}


def test_faker_columns_take_the_vectorised_path_and_keep_the_options():
    ctx = _ctx()
    names = sample_column(FakerSampler(provider="name"), 50, ctx)
    assert len(names) == 50 and all(" " in n for n in names)
    words = sample_column(FakerSampler(provider="words", kwargs={"nb": 2}, join=", "), 5, ctx)
    assert all(", " in w for w in words)
    upper = sample_column(FakerSampler(provider="word", transform="upper"), 5, ctx)
    assert all(w.isupper() for w in upper)
    lat = sample_column(FakerSampler(provider="latitude", as_type="float"), 5, ctx)
    assert all(isinstance(v, float) for v in lat)
    ibans = sample_column(FakerSampler(provider="iban"), 3, ctx)  # row by row, through Faker
    assert all(len(i) > 10 for i in ibans)


def test_a_column_is_the_same_whatever_the_process():
    """The vectorised draws come from the shard's numpy stream, so they are a
    function of the seed alone."""
    a = sample_column(FakerSampler(provider="email"), 100, _ctx(seed=9))
    b = sample_column(FakerSampler(provider="email"), 100, _ctx(seed=9))
    assert a == b
