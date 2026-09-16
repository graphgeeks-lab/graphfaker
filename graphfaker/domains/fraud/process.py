"""The legitimate transaction process.

Everything here is vectorised with numpy so 90M transactions is a matter of
memory, not of Python loops. Realism comes from four things a uniform random
transaction model (gen-fraud-graph's, for one) leaves out:

* **Recurring flows.** Salary from employers on payday, rent on the first of
  the month, utilities and subscriptions on their own schedule. Most of a
  real account's volume is boring and periodic, and it is the background
  laundering patterns have to hide in.
* **Repeat partners.** An account transfers mostly to a small set of
  contacts drawn with same-region preference and activity weighting; only a
  quarter of transfers go to strangers. This gives the P2P layer hubs,
  clustering and community structure instead of an Erdős–Rényi soup.
* **Merchant popularity.** Card payments go to merchants in proportion to a
  heavy-tailed prominence, mostly within the customer's region.
* **Seasonality and income scaling.** Hour-of-day and day-of-week profiles per
  channel; amounts scale with the customer's income.

Balances are not tracked as a running ledger; amounts are income-aware but an
account is not prevented from overspending. That is a deliberate Phase 1
simplification and is noted in the design document.
"""

from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID
from graphfaker.domains.fraud.config import FraudConfig
from graphfaker.domains.fraud.entities import (
    ACCOUNT_ACTIVITY,
    MERCHANT_CATEGORIES,
    REGION,
    STATUS_ACTIVITY,
)

PAYS, TRANSFERS, WIRES = "PAYS", "TRANSFERS", "WIRES"
CHANNELS = (PAYS, TRANSFERS, WIRES)

#: Channel mix by account type: (PAYS, TRANSFERS, WIRES).
CHANNEL_MIX = {
    "checking": (0.70, 0.27, 0.03),
    "savings": (0.10, 0.85, 0.05),
    "business": (0.35, 0.50, 0.15),
    "credit": (0.97, 0.03, 0.00),
}

#: Day-of-week weights, Monday first.
WEEKDAY_PROFILE = {
    PAYS: [1.0, 1.0, 1.0, 1.05, 1.2, 1.15, 0.9],
    TRANSFERS: [1.1, 1.05, 1.0, 1.0, 1.15, 0.6, 0.5],
    WIRES: [1.2, 1.1, 1.1, 1.1, 1.2, 0.15, 0.1],
}
#: Hour-of-day weights: quiet nights, a lunchtime peak, an evening peak.
HOUR_PROFILE = np.array(
    [0.2, 0.1, 0.08, 0.06, 0.06, 0.1, 0.3, 0.6, 0.9, 1.0, 1.1, 1.2,
     1.4, 1.3, 1.1, 1.0, 1.1, 1.3, 1.5, 1.4, 1.1, 0.8, 0.5, 0.3]
)
BUSINESS_HOURS = np.array([0.0] * 8 + [1.0] * 10 + [0.0] * 6)

#: Legitimate transfer and wire amount profiles (log-normal), income-neutral.
#: Transfers are a mixture: everyday amounts plus a large-transfer component
#: (rent, tuition, a car) so the tail reaches the thousands the way real
#: P2P rails do. Without it any four-figure transfer is an outlier.
TRANSFER_AMOUNT = (4.6, 1.1)
LARGE_TRANSFER_AMOUNT = (7.8, 0.9)
LARGE_TRANSFER_RATE = 0.08
WIRE_AMOUNT = (7.0, 0.9)


def transfer_amounts(rng: np.random.Generator, n: int) -> np.ndarray:
    """Income-neutral legitimate transfer amounts from the mixture."""
    small = rng.lognormal(*TRANSFER_AMOUNT, size=n)
    large = rng.lognormal(*LARGE_TRANSFER_AMOUNT, size=n)
    return np.where(rng.random(n) < LARGE_TRANSFER_RATE, large, small)
#: Sensitivity of amounts to income: amount *= exp(k * (log_income - mean)).
INCOME_ELASTICITY = 0.5

TRANSFER_MEMOS = ["", "rent share", "dinner", "gift", "invoice", "loan repayment", "tickets", "thanks"]
TRANSFER_MEMO_WEIGHTS = [40, 8, 12, 10, 8, 6, 6, 10]

#: Share of the P2P layer that goes to a known contact rather than a stranger.
CONTACT_RATE = 0.75
#: Share of card payments made in the customer's own region.
LOCAL_MERCHANT_RATE = 0.8
#: Share of the transaction budget spent on recurring flows. The budget per
#: account is fixed by the scale convention, so participation in scheduled
#: flows is thinned to keep the periodic share realistic at any budget.
RECURRING_SHARE = 0.3
#: Participation rates in scheduled flows at full participation.
RENT_RATE, UTILITY_RATE, SUBSCRIPTION_MEAN = 0.55, 0.7, 1.5


@dataclass
class Population:
    """Column arrays the process and the typologies both read."""

    account_ids: np.ndarray
    account_region: np.ndarray
    account_type: np.ndarray
    account_status: np.ndarray
    account_customer: np.ndarray  # index into customers
    account_opened: np.ndarray  # datetime64[D]
    customer_ids: np.ndarray
    customer_region: np.ndarray
    customer_segment: np.ndarray
    customer_log_income: np.ndarray
    customer_activity: np.ndarray
    merchant_ids: np.ndarray
    merchant_region: np.ndarray
    merchant_category: np.ndarray
    merchant_prominence: np.ndarray
    counterparty_ids: np.ndarray
    period_start: np.datetime64  # [s]
    period_days: int

    @property
    def n_accounts(self) -> int:
        return len(self.account_ids)

    @functools.cached_property
    def account_log_income(self) -> np.ndarray:
        return self.customer_log_income[self.account_customer]

    @functools.cached_property
    def _mean_log_income(self) -> float:
        return float(self.customer_log_income.mean())

    @property
    def account_weight(self) -> np.ndarray:
        """Relative transaction rate of every account."""
        type_factor = np.vectorize(ACCOUNT_ACTIVITY.get)(self.account_type)
        status_factor = np.vectorize(STATUS_ACTIVITY.get)(self.account_status)
        return self.customer_activity[self.account_customer] * type_factor * status_factor

    def ids(self, table: str, index: np.ndarray) -> pl.Series:
        """The ids at ``index`` of ``account``, ``merchant`` or ``counterparty``
        as a polars column.

        Generated ids are ``prefix_position``, so the column is composed in
        polars from the integer positions (a few bytes a row) instead of
        indexing a numpy array of Python strings (a Python object a row, 90M
        of them at scale 1.0). Ids that do not follow the convention take the
        slow path.
        """
        table_ids = getattr(self, f"{table}_ids")
        prefix = self._prefix(table)
        if prefix is None:
            return pl.Series(table_ids[index].astype(object), dtype=pl.String)
        return (prefix + pl.Series(np.asarray(index, dtype=np.int64)).cast(pl.String)).alias("id")

    @functools.cached_property
    def _prefixes(self) -> dict[str, str | None]:
        found: dict[str, str | None] = {}
        for table in ("account", "merchant", "counterparty"):
            table_ids = getattr(self, f"{table}_ids")
            found[table] = None
            if len(table_ids):
                head, _, tail = str(table_ids[0]).rpartition("_")
                expected = (head + "_") + pl.Series(np.arange(len(table_ids))).cast(pl.String)
                if tail == "0" and bool((pl.Series(table_ids.astype(object), dtype=pl.String) == expected).all()):
                    found[table] = head + "_"
        return found

    def _prefix(self, table: str) -> str | None:
        return self._prefixes[table]

    def income_factor(self, account_idx: np.ndarray) -> np.ndarray:
        """Spending relative to the average earner; cached inputs, because the
        typologies ask for one account at a time."""
        centred = self.account_log_income[account_idx] - self._mean_log_income
        return np.exp(INCOME_ELASTICITY * centred)

    def accounts_in_region(self, region: int, mask: np.ndarray | None = None) -> np.ndarray:
        selected = self.account_region == region
        if mask is not None:
            selected &= mask
        return np.flatnonzero(selected)


def population(tables: dict[str, pl.DataFrame], config: FraudConfig) -> Population:
    customers, accounts = tables["Customer"], tables["Account"]
    merchants, counterparties = tables["Merchant"], tables["Counterparty"]
    cust_index = {cid: i for i, cid in enumerate(customers[ID].to_list())}
    return Population(
        account_ids=accounts[ID].to_numpy(),
        account_region=accounts[REGION].to_numpy(),
        account_type=accounts["account_type"].to_numpy(),
        account_status=accounts["status"].to_numpy(),
        account_customer=np.array([cust_index[c] for c in accounts["customer"].to_list()]),
        account_opened=accounts["opened_at"].to_numpy().astype("datetime64[D]"),
        customer_ids=customers[ID].to_numpy(),
        customer_region=customers[REGION].to_numpy(),
        customer_segment=customers["segment"].to_numpy(),
        customer_log_income=customers["log_income"].to_numpy().astype(float),
        customer_activity=customers["activity"].to_numpy().astype(float),
        merchant_ids=merchants[ID].to_numpy(),
        merchant_region=merchants[REGION].to_numpy(),
        merchant_category=merchants["category"].to_numpy(),
        merchant_prominence=merchants["prominence"].to_numpy().astype(float),
        counterparty_ids=counterparties[ID].to_numpy(),
        period_start=np.datetime64(config.period_start.isoformat(), "s"),
        period_days=config.period_days,
    )


# ----------------------------------------------------------------- timing


def day_weights(pop: Population, channel: str) -> np.ndarray:
    start = pop.period_start.astype("datetime64[D]").astype(dt.date)
    profile = WEEKDAY_PROFILE[channel]
    weights = np.array([profile[(start + dt.timedelta(days=d)).weekday()] for d in range(pop.period_days)])
    return weights / weights.sum()


def sample_timestamps(
    rng: np.random.Generator,
    pop: Population,
    n: int,
    channel: str,
    hours: np.ndarray = HOUR_PROFILE,
) -> np.ndarray:
    """``n`` timestamps over the period following the channel's seasonality."""
    days = rng.choice(pop.period_days, size=n, p=day_weights(pop, channel))
    hour = rng.choice(24, size=n, p=hours / hours.sum())
    seconds = days * 86_400 + hour * 3_600 + rng.integers(0, 3_600, size=n)
    return pop.period_start + seconds.astype("timedelta64[s]")


def monthly_dates(pop: Population, day_of_month: np.ndarray) -> list[np.ndarray]:
    """For every period month, the timestamp of ``day_of_month`` per row (or
    NaT when that day falls outside the period)."""
    start = pop.period_start.astype("datetime64[D]").astype(dt.date)
    end = start + dt.timedelta(days=pop.period_days)
    out = []
    year, month = start.year, start.month
    while dt.date(year, month, 1) < end:
        first = np.datetime64(dt.date(year, month, 1).isoformat(), "s")
        stamps = first + ((day_of_month - 1) * 86_400).astype("timedelta64[s]")
        lo, hi = pop.period_start, pop.period_start + np.timedelta64(pop.period_days * 86_400, "s")
        stamps = np.where((stamps >= lo) & (stamps < hi), stamps, np.datetime64("NaT", "s"))
        out.append(stamps)
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return out


def _frame(source, target, amount, timestamp, memo: str | np.ndarray | None = None, recurring: bool = False) -> pl.DataFrame:
    """One channel's transactions. ``source`` and ``target`` are id columns
    (see :meth:`Population.ids`); ``memo`` is one string for every row or an
    array of them. Constant columns are built by polars, not as arrays of
    Python objects, which is what keeps 90M rows in a few gigabytes."""
    n = len(source)
    if memo is None:
        memo_column = pl.repeat("", n, dtype=pl.String, eager=True)
    elif isinstance(memo, str):
        memo_column = pl.repeat(memo, n, dtype=pl.String, eager=True)
    else:
        memo_column = pl.Series(np.asarray(memo, dtype=object), dtype=pl.String)
    return pl.DataFrame(
        {
            "source": pl.Series(source, dtype=pl.String) if not isinstance(source, pl.Series) else source,
            "target": pl.Series(target, dtype=pl.String) if not isinstance(target, pl.Series) else target,
            "amount": np.round(np.asarray(amount, dtype=float), 2),
            "timestamp": np.asarray(timestamp).astype("datetime64[us]"),
            "memo": memo_column,
            "recurring": pl.repeat(recurring, n, dtype=pl.Boolean, eager=True),
        }
    )


# --------------------------------------------------------------- merchants


class MerchantIndex:
    """Prominence-weighted merchant lookup by region and category."""

    def __init__(self, pop: Population):
        self.pop = pop
        self._by_key: dict[tuple[int | None, str | None], tuple[np.ndarray, np.ndarray]] = {}

    def _entry(self, region: int | None, category: str | None) -> tuple[np.ndarray, np.ndarray]:
        key = (region, category)
        if key not in self._by_key:
            mask = np.ones(len(self.pop.merchant_ids), dtype=bool)
            if region is not None:
                mask &= self.pop.merchant_region == region
            if category is not None:
                mask &= self.pop.merchant_category == category
            idx = np.flatnonzero(mask)
            prominence = self.pop.merchant_prominence[idx]
            weights = prominence / prominence.sum() if len(idx) else prominence
            self._by_key[key] = (idx, weights)
        return self._by_key[key]

    def choose(self, rng: np.random.Generator, n: int, region: int | None, category: str | None) -> np.ndarray:
        idx, weights = self._entry(region, category)
        if len(idx) == 0 and region is not None:
            idx, weights = self._entry(None, category)
        if len(idx) == 0:
            idx, weights = self._entry(None, None)
        return rng.choice(idx, size=n, p=weights)

    def choose_for_accounts(
        self, rng: np.random.Generator, account_idx: np.ndarray, category: str | None
    ) -> np.ndarray:
        """One merchant per account, local with :data:`LOCAL_MERCHANT_RATE`."""
        out = np.empty(len(account_idx), dtype=np.int64)
        local = rng.random(len(account_idx)) < LOCAL_MERCHANT_RATE
        regions = self.pop.account_region[account_idx]
        for region in np.unique(regions[local]):
            rows = np.flatnonzero(local & (regions == region))
            out[rows] = self.choose(rng, len(rows), int(region), category)
        rows = np.flatnonzero(~local)
        if len(rows):
            out[rows] = self.choose(rng, len(rows), None, category)
        return out


def merchant_amounts(rng: np.random.Generator, pop: Population, merchant_idx: np.ndarray, account_idx: np.ndarray) -> np.ndarray:
    categories = pop.merchant_category[merchant_idx]
    mu = np.vectorize(lambda c: MERCHANT_CATEGORIES[c][0])(categories)
    sigma = np.vectorize(lambda c: MERCHANT_CATEGORIES[c][1])(categories)
    return rng.lognormal(mu, sigma) * pop.income_factor(account_idx)


# --------------------------------------------------------------- contacts


@dataclass
class Contacts:
    """Each account's transfer partners, flat with offsets."""

    offsets: np.ndarray
    counts: np.ndarray
    partners: np.ndarray

    def sample(self, rng: np.random.Generator, source_idx: np.ndarray) -> np.ndarray:
        pick = rng.integers(0, self.counts[source_idx])
        return self.partners[self.offsets[source_idx] + pick]


def build_contacts(rng: np.random.Generator, pop: Population, weights: np.ndarray) -> Contacts:
    """2–6 partners per account: 70% from the same region, weighted by
    activity so hubs emerge, never the account itself."""
    n = pop.n_accounts
    counts = rng.integers(2, 7, size=n)
    offsets = np.concatenate([[0], np.cumsum(counts)[:-1]])
    partners = np.empty(counts.sum(), dtype=np.int64)
    global_p = weights / weights.sum()
    regions = np.unique(pop.account_region)
    region_members = {int(r): np.flatnonzero(pop.account_region == r) for r in regions}
    region_p = {r: weights[m] / weights[m].sum() for r, m in region_members.items()}

    source_of_slot = np.repeat(np.arange(n), counts)
    local = rng.random(len(source_of_slot)) < 0.7
    slot_region = pop.account_region[source_of_slot]
    for region in regions:
        rows = np.flatnonzero(local & (slot_region == region))
        members = region_members[int(region)]
        if len(members) < 2:
            local[rows] = False
            continue
        partners[rows] = rng.choice(members, size=len(rows), p=region_p[int(region)])
    rows = np.flatnonzero(~local)
    partners[rows] = rng.choice(n, size=len(rows), p=global_p)

    # Resample the rare self-partner.
    for _ in range(5):
        bad = np.flatnonzero(partners == source_of_slot)
        if not len(bad):
            break
        partners[bad] = rng.choice(n, size=len(bad), p=global_p)
    return Contacts(offsets=offsets, counts=counts, partners=partners)


# --------------------------------------------------------------- recurring


def recurring_participation(pop: Population, budget: int) -> float:
    """Fraction of eligible accounts that take part in scheduled flows so that
    recurring events stay near :data:`RECURRING_SHARE` of the budget."""
    months = max(1, round(pop.period_days / 30))
    checking = int(((pop.account_status == "active") & (pop.account_type == "checking")).sum())
    per_month = checking * (RENT_RATE + UTILITY_RATE * 1.5 + SUBSCRIPTION_MEAN + 1.0)
    expected = per_month * months
    return float(min(1.0, RECURRING_SHARE * budget / expected)) if expected else 1.0


def recurring_flows(
    rng: np.random.Generator, pop: Population, merchants: MerchantIndex, participation: float = 1.0
) -> dict[str, list[pl.DataFrame]]:
    frames: dict[str, list[pl.DataFrame]] = {PAYS: [], TRANSFERS: []}
    active = pop.account_status == "active"
    checking = active & (pop.account_type == "checking")
    business = active & (pop.account_type == "business")
    checking_idx = np.flatnonzero(checking)

    # --- salary: employer (business account) -> the customer's first checking
    #     account, monthly on payday.
    first_checking: dict[int, int] = {}
    for idx in checking_idx:
        first_checking.setdefault(int(pop.account_customer[idx]), int(idx))
    employed = [
        (cust, acc)
        for cust, acc in first_checking.items()
        if pop.customer_segment[cust] != "business" and rng.random() < participation
    ]
    if employed and business.any():
        cust_idx = np.array([c for c, _ in employed])
        acc_idx = np.array([a for _, a in employed])
        employer = np.empty(len(acc_idx), dtype=np.int64)
        biz_by_region = {int(r): np.flatnonzero(business & (pop.account_region == r)) for r in np.unique(pop.account_region)}
        biz_all = np.flatnonzero(business)
        for region in np.unique(pop.account_region[acc_idx]):
            rows = np.flatnonzero(pop.account_region[acc_idx] == region)
            pool = biz_by_region.get(int(region))
            pool = pool if pool is not None and len(pool) else biz_all
            employer[rows] = rng.choice(pool, size=len(rows))
        payday = rng.choice([25, 26, 27, 28, 1], size=len(acc_idx), p=[0.4, 0.15, 0.15, 0.2, 0.1])
        monthly = np.exp(pop.customer_log_income[cust_idx]) / 12.0
        for stamps in monthly_dates(pop, payday):
            ok = ~np.isnat(stamps)
            if not ok.any():
                continue
            hour = rng.choice(24, size=ok.sum(), p=BUSINESS_HOURS / BUSINESS_HOURS.sum())
            ts = stamps[ok] + (hour * 3_600).astype("timedelta64[s]")
            amount = monthly[ok] * rng.normal(1.0, 0.01, size=ok.sum())
            frames[TRANSFERS].append(
                _frame(
                    pop.ids("account", employer[ok]),
                    pop.ids("account", acc_idx[ok]),
                    amount,
                    ts,
                    memo="salary",
                    recurring=True,
                )
            )

    # --- rent, utilities, subscriptions: fixed merchant, fixed day, stable amount.
    def scheduled(category: str, accounts: np.ndarray, per_account: np.ndarray, day_lo: int, day_hi: int, jitter: float):
        acc = np.repeat(accounts, per_account)
        if not len(acc):
            return
        merchant = merchants.choose_for_accounts(rng, acc, category)
        day = rng.integers(day_lo, day_hi + 1, size=len(acc))
        base = merchant_amounts(rng, pop, merchant, acc)
        for stamps in monthly_dates(pop, day):
            ok = ~np.isnat(stamps)
            if not ok.any():
                continue
            hour = rng.choice(24, size=ok.sum(), p=HOUR_PROFILE / HOUR_PROFILE.sum())
            ts = stamps[ok] + (hour * 3_600).astype("timedelta64[s]")
            amount = base[ok] * rng.normal(1.0, jitter, size=ok.sum())
            frames[PAYS].append(
                _frame(pop.ids("account", acc[ok]), pop.ids("merchant", merchant[ok]), amount, ts, recurring=True)
            )

    renters = checking_idx[rng.random(len(checking_idx)) < RENT_RATE * participation]
    scheduled("rent", renters, np.ones(len(renters), dtype=int), 1, 5, 0.0)
    utility_users = checking_idx[rng.random(len(checking_idx)) < UTILITY_RATE * participation]
    scheduled("utilities", utility_users, rng.integers(1, 3, size=len(utility_users)), 8, 20, 0.08)
    subs_idx = np.flatnonzero(active & np.isin(pop.account_type, ["checking", "credit"]))
    scheduled("subscription", subs_idx, rng.poisson(SUBSCRIPTION_MEAN * participation, size=len(subs_idx)), 1, 28, 0.0)
    return frames


# ------------------------------------------------------------------ ad hoc


def adhoc_flows(
    rng: np.random.Generator,
    pop: Population,
    merchants: MerchantIndex,
    contacts: Contacts,
    budget: int,
) -> dict[str, list[pl.DataFrame]]:
    frames: dict[str, list[pl.DataFrame]] = {PAYS: [], TRANSFERS: [], WIRES: []}
    if budget <= 0:
        return frames
    weights = pop.account_weight
    expected = weights / weights.sum() * budget
    counts = rng.poisson(expected)

    mix = np.array([CHANNEL_MIX[t] for t in pop.account_type])
    pays = rng.binomial(counts, mix[:, 0])
    rest = counts - pays
    p_tr = np.divide(mix[:, 1], 1 - mix[:, 0], out=np.ones(len(mix)), where=(1 - mix[:, 0]) > 0)
    transfers = rng.binomial(rest, np.clip(p_tr, 0, 1))
    wires = rest - transfers

    # PAYS
    src = np.repeat(np.arange(pop.n_accounts), pays)
    if len(src):
        merchant = merchants.choose_for_accounts(rng, src, None)
        frames[PAYS].append(
            _frame(
                pop.ids("account", src),
                pop.ids("merchant", merchant),
                merchant_amounts(rng, pop, merchant, src),
                sample_timestamps(rng, pop, len(src), PAYS),
            )
        )

    # TRANSFERS
    src = np.repeat(np.arange(pop.n_accounts), transfers)
    if len(src):
        known = rng.random(len(src)) < CONTACT_RATE
        dst = np.empty(len(src), dtype=np.int64)
        dst[known] = contacts.sample(rng, src[known])
        strangers = np.flatnonzero(~known)
        dst[strangers] = rng.choice(pop.n_accounts, size=len(strangers), p=weights / weights.sum())
        for _ in range(5):
            bad = np.flatnonzero(dst == src)
            if not len(bad):
                break
            dst[bad] = rng.integers(0, pop.n_accounts, size=len(bad))
        amount = transfer_amounts(rng, len(src)) * pop.income_factor(src)
        memo = rng.choice(TRANSFER_MEMOS, size=len(src), p=np.array(TRANSFER_MEMO_WEIGHTS) / sum(TRANSFER_MEMO_WEIGHTS))
        frames[TRANSFERS].append(
            _frame(
                pop.ids("account", src),
                pop.ids("account", dst),
                amount,
                sample_timestamps(rng, pop, len(src), TRANSFERS),
                memo=memo,
            )
        )

    # WIRES
    src = np.repeat(np.arange(pop.n_accounts), wires)
    if len(src) and len(pop.counterparty_ids):
        dst = rng.integers(0, len(pop.counterparty_ids), size=len(src))
        amount = rng.lognormal(*WIRE_AMOUNT, size=len(src)) * pop.income_factor(src)
        frames[WIRES].append(
            _frame(
                pop.ids("account", src),
                pop.ids("counterparty", dst),
                amount,
                sample_timestamps(rng, pop, len(src), WIRES, hours=BUSINESS_HOURS + 0.02),
                memo="international transfer",
            )
        )
    return frames


def legitimate_transactions(
    rng: np.random.Generator, pop: Population, budget: int
) -> tuple[dict[str, pl.DataFrame], Contacts, MerchantIndex]:
    """All legitimate transactions, per channel, within ``budget`` events."""
    merchants = MerchantIndex(pop)
    contacts = build_contacts(rng, pop, pop.account_weight + 1e-9)
    recurring = recurring_flows(rng, pop, merchants, recurring_participation(pop, budget))
    n_recurring = sum(f.height for frames in recurring.values() for f in frames)
    adhoc = adhoc_flows(rng, pop, merchants, contacts, budget - n_recurring)
    out: dict[str, pl.DataFrame] = {}
    for channel in CHANNELS:
        parts = recurring.get(channel, []) + adhoc.get(channel, [])
        out[channel] = pl.concat(parts, rechunk=True) if parts else _frame([], [], [], np.array([], dtype="datetime64[s]"))
    return out, contacts, merchants
