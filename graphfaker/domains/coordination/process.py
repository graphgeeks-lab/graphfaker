"""The organic activity process.

Everything here is vectorised with numpy, so the event stream is a matter of
memory rather than of Python loops.

Realism comes from four things a uniform random activity model leaves out, and
each of them is a signal a coordination detector would otherwise get for free:

* **Heavy-tailed volume.** Account activity is log-normal, so most accounts
  post a handful of times over the period and a few thousand post constantly.
  If volume were uniform, any campaign account would be a volume outlier and
  the detection problem would be trivial.
* **Topic affinity.** An account posts mostly about topics in its own interest
  community, weighted by topic prominence. A campaign pushing one topic is
  only anomalous against a population that *also* concentrates, and the
  organic concentration is what makes topic focus a weak signal rather than a
  decisive one.
* **Reshares follow the follow graph.** An account reshares and replies to
  accounts it follows, in proportion to their audience. This gives the
  interaction graph its own hubs and clustering rather than an
  Erdős–Rényi soup, and it means an amplification ring has to beat real
  amplification.
* **Time of day and day of week.** Human posting has a diurnal rhythm.
  Sub-minute synchrony is detectable partly because *nothing* organic is that
  synchronous, so the organic rhythm has to be present for that contrast to be
  real.

Channels are the three event kinds: ``POSTED`` (account to topic), ``RESHARED``
and ``REPLIED`` (account to account).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID
from graphfaker.domains.coordination.config import CoordinationConfig
from graphfaker.domains.coordination.entities import COMMUNITY

POSTED = "POSTED"
RESHARED = "RESHARED"
REPLIED = "REPLIED"
CHANNELS = (POSTED, RESHARED, REPLIED)

#: Share of events of each kind. Reading and resharing is cheaper than
#: authoring, so reshares outnumber original posts on every real platform.
CHANNEL_MIX = {POSTED: 0.34, RESHARED: 0.48, REPLIED: 0.18}

#: Relative posting rate by hour, local. Two humps: a commute-and-lunch rise
#: and an evening peak, with a trough overnight.
HOUR_PROFILE = np.array(
    [
        0.25, 0.15, 0.10, 0.08, 0.08, 0.12, 0.30, 0.65,
        0.95, 1.05, 1.05, 1.00, 1.10, 1.05, 1.00, 1.00,
        1.05, 1.20, 1.40, 1.55, 1.60, 1.40, 0.95, 0.55,
    ],
    dtype=np.float64,
)
#: Monday to Sunday. Weekends are busier for everything except work topics,
#: which this model does not distinguish.
DAY_PROFILE = np.array([1.0, 1.0, 1.0, 1.0, 1.05, 1.15, 1.10], dtype=np.float64)

#: Distinct ways an organic post about a topic can be phrased. A campaign at
#: ``text_blend=0`` collapses to one of them, which is the duplicate-text
#: signal; the organic spread is what that collapse is measured against.
#:
#: Large on purpose. At 400 nearly every organic template was also used by
#: another account, so ``template_reuse`` sat near 1.0 for everyone and a
#: duplicate-text rule flagged 90% of *organic* accounts — the canonical
#: copypasta signal reduced to noise by a generator artifact. The number of
#: ways to phrase a real post is effectively unbounded, so collisions between
#: unrelated accounts should be rare.
TEMPLATES_PER_TOPIC = 20_000

#: Rows built per block. Keeps peak memory proportional to the block rather
#: than to the whole stream.
BLOCK_ROWS = 2_000_000

#: Share of accounts that read without posting. Every measured platform is
#: mostly lurkers, and leaving them out had two consequences: only 1.5% of
#: accounts were silent (against 20-40% reported for real networks), and "low
#: activity" stopped being informative, which flatters any detector using
#: volume as a feature.
LURKER_SHARE = 0.30


class Population:
    """Numpy views of the node tables, plus the derived weights the process
    needs. Built once; every channel reads from it."""

    def __init__(self, tables: dict[str, pl.DataFrame], config: CoordinationConfig):
        accounts, topics = tables["Account"], tables["Topic"]
        self.config = config
        self.n_accounts = accounts.height
        self.n_topics = topics.height

        self.account_ids = accounts[ID]
        self.topic_ids = topics[ID]
        self.account_community = accounts[COMMUNITY].to_numpy()
        self.topic_community = np.asarray(
            topics[COMMUNITY].to_numpy() if COMMUNITY in topics.columns else np.zeros(self.n_topics)
        )
        self.activity = accounts["activity"].to_numpy().astype(np.float64)
        self.reshare_rate = accounts["reshare_rate"].to_numpy().astype(np.float64)
        self.topic_prominence = topics["prominence"].to_numpy().astype(np.float64)
        self.created_at = accounts["created_at"].to_numpy().astype("datetime64[D]")

        # Who authors an event: activity, normalised. An account created
        # part-way through the period is weighted down proportionally so it is
        # not busier than its lifetime allows.
        self.period_start = np.datetime64(config.period_start, "s")
        self.period_days = config.period_days
        self.period_end = self.period_start + np.timedelta64(config.period_days * 86_400, "s")

        self.author_weight = self.activity.copy()
        self.author_weight[self.author_weight < 0] = 0.0
        if self.author_weight.sum() <= 0:
            self.author_weight = np.ones(self.n_accounts)
        # Lurkers are chosen deterministically from the account's position so
        # the set does not depend on how many events are drawn.
        lurkers = (np.arange(self.n_accounts) % 100) < int(LURKER_SHARE * 100)
        self.author_weight = np.where(lurkers, 0.0, self.author_weight)
        if self.author_weight.sum() <= 0:  # tiny graphs: everyone posts
            self.author_weight = np.ones(self.n_accounts)
        self.lurkers = lurkers

        # Topic choice per community: prominence, restricted to the topics of
        # that community where it has any.
        self.topics_by_community: dict[int, np.ndarray] = {}
        for group in np.unique(self.account_community):
            members = np.flatnonzero(self.topic_community == group)
            self.topics_by_community[int(group)] = (
                members if len(members) else np.arange(self.n_topics)
            )

    def assign_topic_communities(self, rng: np.random.Generator) -> np.ndarray:
        """Topics belong to an interest community.

        The schema has no latent reference for Topic, so the assignment is made
        here and written back onto the table; it is what makes an account's
        topic choice community-local.
        """
        groups = np.unique(self.account_community)
        self.topic_community = rng.choice(groups, size=self.n_topics)
        self.topics_by_community = {
            int(g): (
                np.flatnonzero(self.topic_community == g)
                if int((self.topic_community == g).sum())
                else np.arange(self.n_topics)
            )
            for g in groups
        }
        return self.topic_community


def _timestamps(rng: np.random.Generator, pop: Population, size: int) -> np.ndarray:
    """Draw event times over the period, following the hour and day profiles."""
    if size == 0:
        return np.empty(0, dtype="datetime64[s]")
    day = rng.integers(0, pop.period_days, size=size)
    weekday = (np.datetime64(pop.config.period_start).astype("datetime64[D]").astype(int) + day) % 7
    keep_day = rng.random(size) < (DAY_PROFILE[weekday] / DAY_PROFILE.max())
    # Rejection on the day profile is cheap and keeps the marginal exact;
    # rejected draws simply fall on a different day.
    day = np.where(keep_day, day, rng.integers(0, pop.period_days, size=size))

    hour = rng.choice(24, size=size, p=HOUR_PROFILE / HOUR_PROFILE.sum())
    minute = rng.integers(0, 60, size=size)
    second = rng.integers(0, 60, size=size)
    offsets = day.astype(np.int64) * 86_400 + hour.astype(np.int64) * 3_600 + minute * 60 + second
    return pop.period_start + offsets.astype("timedelta64[s]")


def _authors(rng: np.random.Generator, pop: Population, size: int) -> np.ndarray:
    weight = pop.author_weight
    return rng.choice(pop.n_accounts, size=size, p=weight / weight.sum())


def _topics_for(rng: np.random.Generator, pop: Population, authors: np.ndarray) -> np.ndarray:
    """A topic per event, drawn community-locally and weighted by prominence."""
    out = np.empty(len(authors), dtype=np.int64)
    author_group = pop.account_community[authors]
    for group, candidates in pop.topics_by_community.items():
        mask = author_group == group
        count = int(mask.sum())
        if not count:
            continue
        w = pop.topic_prominence[candidates]
        total = w.sum()
        out[mask] = (
            rng.choice(candidates, size=count, p=w / total)
            if total > 0
            else rng.choice(candidates, size=count)
        )
    return out


def build_audience(pop: Population, follows: pl.DataFrame) -> dict[int, np.ndarray]:
    """Who each account can reshare or reply to: the accounts it follows.

    Indexed by account position. An account following nobody reshares from the
    population at large, which is what a logged-out-style feed does.
    """
    if follows.height == 0:
        return {}
    index = {str(a): i for i, a in enumerate(pop.account_ids.to_list())}
    src = np.array([index.get(s, -1) for s in follows["source"].to_list()], dtype=np.int64)
    dst = np.array([index.get(t, -1) for t in follows["target"].to_list()], dtype=np.int64)
    keep = (src >= 0) & (dst >= 0)
    src, dst = src[keep], dst[keep]
    order = np.argsort(src, kind="stable")
    src, dst = src[order], dst[order]
    bounds = np.searchsorted(src, np.arange(pop.n_accounts + 1))
    return {
        i: dst[bounds[i] : bounds[i + 1]]
        for i in range(pop.n_accounts)
        if bounds[i + 1] > bounds[i]
    }


def _interaction_targets(
    rng: np.random.Generator,
    pop: Population,
    authors: np.ndarray,
    audience: dict[int, np.ndarray],
) -> np.ndarray:
    """Pick who each event is directed at.

    Mostly someone the author follows, which is what puts the hubs of the
    follow graph at the centre of the interaction graph too. Falling back to
    the population keeps accounts that follow nobody from being inert.
    """
    size = len(authors)
    out = rng.choice(
        pop.n_accounts, size=size, p=pop.author_weight / pop.author_weight.sum()
    )
    for position, author in enumerate(authors):
        pool = audience.get(int(author))
        if pool is not None and len(pool):
            out[position] = pool[rng.integers(0, len(pool))]
    return out


def organic_events(
    rng: np.random.Generator,
    pop: Population,
    budget: int,
    audience: dict[int, np.ndarray],
    finish=None,
) -> dict[str, list[pl.DataFrame]]:
    """The organic event stream, as a list of parts per channel.

    Parts rather than one frame per channel: the caller concatenates without
    holding a second copy, which is what keeps peak memory near the block size.

    ``finish`` is applied to each part as it is produced, so events that a
    campaign has retrospectively made impossible (an account created after the
    event, or one whose organic activity is stripped) never take up memory.
    """
    parts: dict[str, list[pl.DataFrame]] = {channel: [] for channel in CHANNELS}
    if budget <= 0 or pop.n_accounts == 0:
        return parts

    for channel in CHANNELS:
        remaining = int(budget * CHANNEL_MIX[channel])
        while remaining > 0:
            take = min(BLOCK_ROWS, remaining)
            remaining -= take

            authors = _authors(rng, pop, take)
            stamps = _timestamps(rng, pop, take)

            if channel == POSTED:
                topics = _topics_for(rng, pop, authors)
                frame = pl.DataFrame(
                    {
                        "source": pop.account_ids.gather(authors),
                        "target": pop.topic_ids.gather(topics),
                        "timestamp": stamps.astype("datetime64[us]"),
                        "template_id": rng.integers(0, TEMPLATES_PER_TOPIC, size=take),
                        "campaign_id": pl.Series([None] * take, dtype=pl.String),
                    }
                )
            else:
                targets = _interaction_targets(rng, pop, authors, audience)
                topics = _topics_for(rng, pop, authors)
                frame = pl.DataFrame(
                    {
                        "source": pop.account_ids.gather(authors),
                        "target": pop.account_ids.gather(targets),
                        "timestamp": stamps.astype("datetime64[us]"),
                        "topic": pop.topic_ids.gather(topics),
                        "campaign_id": pl.Series([None] * take, dtype=pl.String),
                    }
                )

            if finish is not None:
                frame = finish(frame)
            if frame.height:
                parts[channel].append(frame)
    return parts


def drop_before_creation(frame: pl.DataFrame, created: pl.DataFrame) -> pl.DataFrame:
    """Remove events authored by an account before it existed.

    A campaign that opens fresh accounts moves their ``created_at`` forward,
    and the organic activity drawn for them beforehand has to go: an account
    posting a month before signup is the kind of tell that makes a dataset
    unusable, and it would be the strongest feature in the whole graph.
    """
    if frame.height == 0 or created.height == 0:
        return frame
    return (
        frame.join(created.rename({"account": "source"}), on="source", how="left")
        .filter(pl.col("created").is_null() | (pl.col("timestamp").dt.date() >= pl.col("created")))
        .drop("created")
    )


def drop_while_dormant(frame: pl.DataFrame, dormant: pl.DataFrame) -> pl.DataFrame:
    """Remove an account's organic activity from before it woke up.

    Distinct from :func:`drop_before_creation`: the account existed and simply
    was not being used. Keeping the activity after the wake-up moment is what
    makes a revived account look ordinary once it is running, instead of
    carrying an all-zero activity profile that gives the playbook away.
    """
    if frame.height == 0 or dormant.height == 0:
        return frame
    return (
        frame.join(dormant.rename({"account": "source"}), on="source", how="left")
        .filter(pl.col("awake").is_null() | (pl.col("timestamp") >= pl.col("awake")))
        .drop("awake")
    )


def period_bounds(config: CoordinationConfig) -> tuple[np.datetime64, np.datetime64]:
    start = np.datetime64(config.period_start, "s")
    return start, start + np.timedelta64(config.period_days * 86_400, "s")


def to_date(value: np.datetime64) -> dt.date:
    return value.astype("datetime64[D]").astype(dt.date)
