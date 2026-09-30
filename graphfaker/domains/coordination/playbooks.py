"""Injected, labelled campaigns.

Each playbook has a *signature*: the thing a detector was written to catch.
Identical text across accounts. A burst inside a minute. A cluster whose
follows are all mutual. A bloc of accounts created on the same day. A set of
accounts posting from one device. Tradecraft decides how much of that signature
survives: ``text_blend`` swaps identical templates for draws from the topic's
organic distribution, ``timing_jitter_hours`` stretches a burst from seconds to
days, ``activity_camouflage`` keeps ordinary activity on campaign accounts,
``account_age_blend`` uses aged accounts instead of fresh ones, ``overlap``
lets campaigns share members, and ``decoy_ratio`` adds organic structures with
the same shape.

Every campaign records its accounts with roles and every event it creates; the
truth tables are built from those records.

Scope
-----
These are coordination *shapes* — who acts with whom, when, and how densely —
and every one of them is described in the public literature on platform
manipulation. Nothing here generates message content: a post carries a
``template_id``, an integer, and nothing else. The pack exists to measure
detectors, and what it withholds (text, personas, targeting) is what a detector
does not need.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from graphfaker.domains.coordination.config import (
    DECOY_PLAYBOOKS,
    NATURAL_SPAN,
    PLAYBOOKS,
    CoordinationConfig,
    TradecraftProfile,
)
from graphfaker.domains.coordination.process import (
    POSTED,
    REPLIED,
    RESHARED,
    TEMPLATES_PER_TOPIC,
    Population,
)


@dataclass
class Campaign:
    campaign_id: str
    playbook: str
    is_coordinated: bool
    roles: dict[int, str] = field(default_factory=dict)  # account idx -> role
    start: np.datetime64 | None = None
    end: np.datetime64 | None = None
    n_events: int = 0
    #: The topic a campaign pushes, where it pushes one.
    topic: int | None = None


@dataclass
class Injection:
    """Everything the playbooks produced, to be merged into the run."""

    campaigns: list[Campaign]
    events: dict[str, pl.DataFrame]  # channel -> frame with campaign_id
    #: (account idx, device idx) pairs to add as USES edges.
    shared_devices: list[tuple[int, int]]
    #: (follower idx, followee idx) pairs to add as FOLLOWS edges.
    extra_follows: list[tuple[int, int]]
    #: account idx -> new created_at, for campaigns that open fresh accounts.
    fresh_accounts: dict[int, np.datetime64]
    #: account idx whose organic activity is removed (no camouflage).
    stripped_accounts: set[int]
    #: account idx -> the moment it woke up. Organic activity *before* this is
    #: removed and activity after it is kept, which is what dormancy means.
    dormant_until: dict[int, np.datetime64]


class PlaybookContext:
    def __init__(
        self,
        rng: np.random.Generator,
        pop: Population,
        config: CoordinationConfig,
        n_devices: int,
    ):
        self.rng = rng
        self.pop = pop
        self.config = config
        self.profile: TradecraftProfile = config.profile
        self.n_devices = n_devices
        self.used: set[int] = set()
        #: While True, ``pick`` may reuse campaign accounts (cluster overlap).
        #: Decoys turn it off: an organic fandom must not share members with an
        #: amplification ring, or its label would be ambiguous.
        self.allow_overlap = True

        # Recruitment is uniform over accounts. Weighting it towards active
        # accounts was tried in the fraud pack as camouflage and measured to do
        # the opposite: hubs are outliers already, so a cluster built from hubs
        # is found by degree alone. Ordinary accounts plus a few extra edges
        # hide better, and ``size_scale`` is the lever for cluster size.
        self.eligible = np.arange(pop.n_accounts)
        self.rows: dict[str, list[tuple]] = {POSTED: [], RESHARED: [], REPLIED: []}
        self.shared_devices: list[tuple[int, int]] = []
        self.extra_follows: list[tuple[int, int]] = []
        self.fresh_accounts: dict[int, np.datetime64] = {}
        self.stripped_accounts: set[int] = set()
        self.dormant_until: dict[int, np.datetime64] = {}
        self.period_start = pop.period_start
        self.period_end = pop.period_end

    # ------------------------------------------------------------- selection

    def size(self, base: int, spread: int = 0) -> int:
        """A campaign size, scaled by tradecraft and never below two."""
        drawn = base + (self.rng.integers(0, spread + 1) if spread else 0)
        return max(2, round(drawn * self.profile.size_scale))

    def pick(self, count: int, community: int | None = None) -> np.ndarray:
        """Recruit ``count`` accounts.

        Draws from one interest community when asked, which is what makes a
        campaign sit inside the social graph rather than across it. Accounts
        already in a campaign are avoided unless ``overlap`` says otherwise.
        """
        pool = self.eligible
        if community is not None:
            members = np.flatnonzero(self.pop.account_community == community)
            if len(members) >= count:
                pool = members
        if not self.allow_overlap or self.rng.random() > self.profile.overlap:
            free = pool[~np.isin(pool, list(self.used))] if self.used else pool
            if len(free) >= count:
                pool = free
        if len(pool) == 0:
            return np.empty(0, dtype=np.int64)
        chosen = self.rng.choice(pool, size=min(count, len(pool)), replace=False)
        self.used.update(int(c) for c in chosen)
        return np.asarray(chosen, dtype=np.int64)

    def a_community(self) -> int:
        return int(self.rng.choice(np.unique(self.pop.account_community)))

    def a_topic(self, community: int | None = None) -> int:
        """A topic to push, preferring the campaign's own community."""
        if community is not None:
            candidates = self.pop.topics_by_community.get(community)
            if candidates is not None and len(candidates):
                return int(self.rng.choice(candidates))
        return self.rng.integers(0, self.pop.n_topics).item()

    # ------------------------------------------------------------------ time

    def window(self, playbook: str) -> tuple[np.datetime64, np.timedelta64]:
        """Start and width of a campaign's activity window.

        Width is the playbook's natural span stretched by
        ``timing_jitter_hours``. At ``low`` a copypasta burst is inside three
        minutes; at ``high`` it is spread over days and there is no burst left
        to find.
        """
        span_days = NATURAL_SPAN.get(playbook, 1.0)
        width_s = max(60.0, span_days * 86_400 * (self.profile.timing_jitter_hours / 1.0))
        total = float((self.period_end - self.period_start) / np.timedelta64(1, "s"))
        width_s = min(width_s, max(60.0, total * 0.9))
        start_offset = self.rng.uniform(0.0, max(1.0, total - width_s))
        return (
            self.period_start + np.timedelta64(int(start_offset), "s"),
            np.timedelta64(int(width_s), "s"),
        )

    def stamps(self, start: np.datetime64, width: np.timedelta64, count: int) -> np.ndarray:
        if count <= 0:
            return np.empty(0, dtype="datetime64[s]")
        seconds = int(width / np.timedelta64(1, "s"))
        offsets = self.rng.integers(0, max(1, seconds), size=count)
        return start + offsets.astype("timedelta64[s]")

    # ------------------------------------------------------------------ text

    def templates(self, topic: int, count: int) -> np.ndarray:
        """Template ids for a campaign's posts.

        At ``text_blend=0`` every post carries the same id, which is the
        duplicate-text signature. At 1 the ids are drawn as an organic post
        about that topic would draw them, so text similarity carries nothing
        and only structure and timing remain.
        """
        signature = self.rng.integers(0, TEMPLATES_PER_TOPIC).item()
        organic = self.rng.integers(0, TEMPLATES_PER_TOPIC, size=count)
        blended = self.rng.random(count) < self.profile.text_blend
        return np.where(blended, organic, signature)

    # ----------------------------------------------------------- bookkeeping

    def post(self, campaign: Campaign, authors: np.ndarray, topic: int, stamps: np.ndarray) -> None:
        templates = self.templates(topic, len(authors))
        for author, stamp, template in zip(authors, stamps, templates):
            self.rows[POSTED].append(
                (int(author), int(topic), stamp, int(template), campaign.campaign_id)
            )
        campaign.n_events += len(authors)

    def interact(
        self,
        campaign: Campaign,
        channel: str,
        sources: np.ndarray,
        targets: np.ndarray,
        topic: int,
        stamps: np.ndarray,
    ) -> None:
        for source, target, stamp in zip(sources, targets, stamps):
            if int(source) == int(target):
                continue
            self.rows[channel].append(
                (int(source), int(target), stamp, int(topic), campaign.campaign_id)
            )
            campaign.n_events += 1

    def age(self, campaign: Campaign, accounts: np.ndarray, start: np.datetime64) -> None:
        """Decide when a campaign's accounts were created.

        At ``account_age_blend=0`` they are all created a few days before the
        campaign: a bloc of same-day signups is the loudest single signal in
        real platform data, and at ``low`` it is left in deliberately. At 1
        they keep the population's ages, which is what buying aged accounts
        buys.
        """
        for account in accounts:
            if self.rng.random() < self.profile.account_age_blend:
                continue
            lead_days = self.rng.integers(1, 8).item()
            created = start - np.timedelta64(lead_days * 86_400, "s")
            if created < self.period_start:
                created = self.period_start
            day = created.astype("datetime64[D]")
            # An account can belong to two campaigns when ``overlap`` allows
            # it. Keep the earliest date, or the second campaign would move
            # creation after the first campaign's events.
            existing = self.fresh_accounts.get(int(account))
            self.fresh_accounts[int(account)] = min(day, existing) if existing else day

    def camouflage(self, campaign: Campaign, accounts: np.ndarray) -> None:
        """Strip organic activity from the accounts tradecraft says are bare.

        At ``activity_camouflage=1`` nothing is stripped and every campaign
        account also behaves normally. At 0 they do nothing but the campaign,
        which makes a single-purpose account obvious by its topic breadth.
        """
        for account in accounts:
            if self.rng.random() >= self.profile.activity_camouflage:
                self.stripped_accounts.add(int(account))


# --------------------------------------------------------------- inauthentic


def copypasta(ctx: PlaybookContext, campaign: Campaign) -> None:
    """Many accounts post the same text about one topic, near-simultaneously."""
    community = ctx.a_community()
    members = ctx.pick(ctx.size(14, 10), community=community)
    if len(members) < 2:
        return
    topic = ctx.a_topic(community)
    start, width = ctx.window("copypasta")
    stamps = ctx.stamps(start, width, len(members))
    for member in members:
        campaign.roles[int(member)] = "poster"
    campaign.topic = topic
    ctx.post(campaign, members, topic, stamps)
    campaign.start, campaign.end = stamps.min(), stamps.max()
    ctx.age(campaign, members, start)
    ctx.camouflage(campaign, members)


def amplification_ring(ctx: PlaybookContext, campaign: Campaign) -> None:
    """A cluster repeatedly reshares one account's output."""
    community = ctx.a_community()
    members = ctx.pick(ctx.size(12, 8), community=community)
    if len(members) < 3:
        return
    target, amplifiers = int(members[0]), members[1:]
    campaign.roles[target] = "target"
    for member in amplifiers:
        campaign.roles[int(member)] = "amplifier"
    topic = ctx.a_topic(community)
    campaign.topic = topic
    start, width = ctx.window("amplification_ring")
    # Each amplifier boosts several times: volume on one target is the shape.
    per = max(2, round(4 * ctx.profile.size_scale))
    sources = np.repeat(amplifiers, per)
    stamps = ctx.stamps(start, width, len(sources))
    ctx.interact(campaign, RESHARED, sources, np.full(len(sources), target), topic, stamps)
    campaign.start, campaign.end = stamps.min(), stamps.max()
    ctx.age(campaign, amplifiers, start)
    ctx.camouflage(campaign, amplifiers)


def reply_brigade(ctx: PlaybookContext, campaign: Campaign) -> None:
    """Coordinated accounts flood one account's replies in a short window."""
    members = ctx.pick(ctx.size(18, 12))
    if len(members) < 3:
        return
    target, brigaders = int(members[0]), members[1:]
    campaign.roles[target] = "target"
    for member in brigaders:
        campaign.roles[int(member)] = "brigader"
    topic = ctx.a_topic()
    campaign.topic = topic
    start, width = ctx.window("reply_brigade")
    per = max(1, round(3 * ctx.profile.size_scale))
    sources = np.repeat(brigaders, per)
    stamps = ctx.stamps(start, width, len(sources))
    ctx.interact(campaign, REPLIED, sources, np.full(len(sources), target), topic, stamps)
    campaign.start, campaign.end = stamps.min(), stamps.max()
    ctx.age(campaign, brigaders, start)
    ctx.camouflage(campaign, brigaders)


def follow_farm(ctx: PlaybookContext, campaign: Campaign) -> None:
    """A dense mutual-follow cluster, inflating each member's audience.

    The signature is reciprocity and density, not activity: the cluster barely
    posts. That makes it the one playbook an event-only detector cannot see at
    all, which is the point of including it.
    """
    members = ctx.pick(ctx.size(16, 10))
    if len(members) < 3:
        return
    for member in members:
        campaign.roles[int(member)] = "member"
    # Near-complete mutual follow. Density falls with size_scale so a high
    # tradecraft farm is not a clique.
    density = 0.95 * ctx.profile.size_scale + 0.35
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            if ctx.rng.random() < min(1.0, density):
                ctx.extra_follows.append((int(a), int(b)))
                ctx.extra_follows.append((int(b), int(a)))
    start, width = ctx.window("follow_farm")
    # A little posting, so the cluster is not inert.
    posters = members[: max(1, len(members) // 3)]
    topic = ctx.a_topic()
    campaign.topic = topic
    stamps = ctx.stamps(start, width, len(posters))
    ctx.post(campaign, posters, topic, stamps)
    campaign.start, campaign.end = start, start + width
    ctx.age(campaign, members, start)
    ctx.camouflage(campaign, members)


def hashtag_flood(ctx: PlaybookContext, campaign: Campaign) -> None:
    """A group pushes one topic hard enough to trend.

    Unlike copypasta the text varies; it is volume on a single topic in a
    narrow window that is the signal, which is exactly what a breaking-news
    decoy also looks like.
    """
    community = ctx.a_community()
    members = ctx.pick(ctx.size(20, 14), community=community)
    if len(members) < 3:
        return
    for member in members:
        campaign.roles[int(member)] = "poster"
    topic = ctx.a_topic(community)
    campaign.topic = topic
    start, width = ctx.window("hashtag_flood")
    per = max(2, round(5 * ctx.profile.size_scale))
    authors = np.repeat(members, per)
    stamps = ctx.stamps(start, width, len(authors))
    ctx.post(campaign, authors, topic, stamps)
    campaign.start, campaign.end = stamps.min(), stamps.max()
    ctx.age(campaign, members, start)
    ctx.camouflage(campaign, members)


def sockpuppet_cluster(ctx: PlaybookContext, campaign: Campaign) -> None:
    """Several accounts run by one operator, sharing a device.

    The device fingerprint is the signature, and it is the hardest one to
    blend: the accounts have to post from somewhere. Organic device sharing
    (households, one person with two accounts) is what it hides in.
    """
    members = ctx.pick(ctx.size(6, 4))
    if len(members) < 2 or ctx.n_devices == 0:
        return
    device = ctx.rng.integers(0, ctx.n_devices).item()
    for member in members:
        campaign.roles[int(member)] = "puppet"
        ctx.shared_devices.append((int(member), device))
    start, width = ctx.window("sockpuppet_cluster")
    # Puppets post across a few topics, staggered: a single-topic cluster is
    # easier to find by topic concentration alone.
    topics = [ctx.a_topic() for _ in range(max(1, len(members) // 2))]
    campaign.topic = topics[0]
    per = max(2, round(6 * ctx.profile.size_scale))
    stamps_all = []
    for topic in topics:
        authors = ctx.rng.choice(members, size=per, replace=True)
        stamps = ctx.stamps(start, width, per)
        ctx.post(campaign, authors, topic, stamps)
        stamps_all.append(stamps)
    joined = np.concatenate(stamps_all)
    campaign.start, campaign.end = joined.min(), joined.max()
    ctx.age(campaign, members, start)
    ctx.camouflage(campaign, members)


def astroturf_campaign(ctx: PlaybookContext, campaign: Campaign) -> None:
    """A sustained push on one topic over weeks, posting and resharing.

    Long and low: no burst, no duplicate text, members spread across
    communities. It is the playbook designed to be missed, and at high
    tradecraft it should be.
    """
    members = ctx.pick(ctx.size(24, 16))
    if len(members) < 4:
        return
    topic = ctx.a_topic()
    campaign.topic = topic
    start, width = ctx.window("astroturf_campaign")
    split = max(1, len(members) // 2)
    posters, amplifiers = members[:split], members[split:]
    for member in posters:
        campaign.roles[int(member)] = "poster"
    for member in amplifiers:
        campaign.roles[int(member)] = "amplifier"

    per = max(2, round(4 * ctx.profile.size_scale))
    authors = np.repeat(posters, per)
    post_stamps = ctx.stamps(start, width, len(authors))
    ctx.post(campaign, authors, topic, post_stamps)

    if len(amplifiers):
        sources = np.repeat(amplifiers, per)
        targets = ctx.rng.choice(posters, size=len(sources), replace=True)
        share_stamps = ctx.stamps(start, width, len(sources))
        ctx.interact(campaign, RESHARED, sources, targets, topic, share_stamps)
        joined = np.concatenate([post_stamps, share_stamps])
    else:
        joined = post_stamps
    campaign.start, campaign.end = joined.min(), joined.max()
    ctx.age(campaign, members, start)
    ctx.camouflage(campaign, members)


def account_handover(ctx: PlaybookContext, campaign: Campaign) -> None:
    """Aged, dormant accounts activated together.

    Ages are left alone whatever ``account_age_blend`` says: the accounts being
    old is the whole premise. The signal is the simultaneous end of dormancy,
    so their organic activity before the activation date is stripped.
    """
    members = ctx.pick(ctx.size(10, 8))
    if len(members) < 2:
        return
    for member in members:
        campaign.roles[int(member)] = "revived"
    topic = ctx.a_topic()
    campaign.topic = topic
    start, width = ctx.window("account_handover")
    per = max(2, round(4 * ctx.profile.size_scale))
    authors = np.repeat(members, per)
    stamps = ctx.stamps(start, width, len(authors))
    ctx.post(campaign, authors, topic, stamps)
    campaign.start, campaign.end = stamps.min(), stamps.max()
    # Dormant until activation, then ordinary. Removing *all* of an account's
    # organic activity instead left its counts at exactly zero, which separated
    # the playbook at AUC 0.97 on reshare count at every tradecraft level: an
    # artifact of the implementation, not a property of account handover. Real
    # revived accounts have a quiet history and a normal present.
    for member in members:
        ctx.dormant_until[int(member)] = campaign.start


# ---------------------------------------------------------------- organic


def fandom_burst(ctx: PlaybookContext, campaign: Campaign) -> None:
    """A real community posting hard about one topic. Twin of hashtag_flood.

    Same shape: one community, one topic, one window, sustained volume. The
    differences a detector has to find are that the text varies as organic text
    does, the accounts are aged normally, and they keep their ordinary activity.
    Flagging this is a false positive.
    """
    community = ctx.a_community()
    members = ctx.pick(ctx.size(16, 12), community=community)
    if len(members) < 2:
        return
    for member in members:
        campaign.roles[int(member)] = "fan"
    topic = ctx.a_topic(community)
    campaign.topic = topic
    start, width = ctx.window("fandom_burst")
    # Matched to hashtag_flood, its twin: a decoy separable by how much it
    # posts is not testing anything a detector should have to get right.
    per = max(2, round(5 * ctx.profile.size_scale))
    authors = np.repeat(members, per)
    stamps = ctx.stamps(start, width, len(authors))
    # Organic text spread whatever the tradecraft level: this is not tradecraft,
    # it is how people actually write.
    for author, stamp in zip(authors, stamps):
        ctx.rows[POSTED].append(
            (
                int(author),
                int(topic),
                stamp,
                ctx.rng.integers(0, TEMPLATES_PER_TOPIC).item(),
                campaign.campaign_id,
            )
        )
    campaign.n_events += len(authors)
    campaign.start, campaign.end = stamps.min(), stamps.max()
    # A fandom follows each other, which is why it clusters.
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            if ctx.rng.random() < 0.3:
                ctx.extra_follows.append((int(a), int(b)))


def breaking_news(ctx: PlaybookContext, campaign: Campaign) -> None:
    """Everyone posts about one event inside an hour. Twin of copypasta.

    One post each, in a tight window, about a single topic: structurally the
    same as a copypasta ring. Drawn across communities rather than within one,
    which is the only structural difference, and a detector keying on a
    synchronous single-topic burst will not see it.
    """
    members = ctx.pick(ctx.size(26, 18))
    if len(members) < 3:
        return
    for member in members:
        campaign.roles[int(member)] = "witness"
    topic = ctx.a_topic()
    campaign.topic = topic
    start, width = ctx.window("breaking_news")
    # One post each, matching copypasta.
    per = 1
    authors = np.repeat(members, per)
    stamps = ctx.stamps(start, width, len(authors))
    for author, stamp in zip(authors, stamps):
        ctx.rows[POSTED].append(
            (
                int(author),
                int(topic),
                stamp,
                ctx.rng.integers(0, TEMPLATES_PER_TOPIC).item(),
                campaign.campaign_id,
            )
        )
    campaign.n_events += len(authors)
    campaign.start, campaign.end = stamps.min(), stamps.max()


def mutual_follow_community(ctx: PlaybookContext, campaign: Campaign) -> None:
    """A tight-knit real community. Twin of follow_farm.

    Dense and reciprocal, because that is what a close community is. A
    reciprocity-and-density rule cannot separate this from a farm; the
    difference is that these accounts talk to each other rather than only
    following.
    """
    community = ctx.a_community()
    members = ctx.pick(ctx.size(14, 10), community=community)
    if len(members) < 3:
        return
    for member in members:
        campaign.roles[int(member)] = "member"
    density = 0.7 * ctx.profile.size_scale + 0.3
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            if ctx.rng.random() < min(1.0, density):
                ctx.extra_follows.append((int(a), int(b)))
                ctx.extra_follows.append((int(b), int(a)))
    start, width = ctx.window("mutual_follow_community")
    topic = ctx.a_topic(community)
    campaign.topic = topic
    # They interact, which a farm does not.
    per = max(1, round(3 * ctx.profile.size_scale))
    sources = np.repeat(members, per)
    targets = ctx.rng.choice(members, size=len(sources), replace=True)
    stamps = ctx.stamps(start, width, len(sources))
    ctx.interact(campaign, REPLIED, sources, targets, topic, stamps)
    campaign.start, campaign.end = start, start + width


PLAYBOOK_FUNCTIONS = {
    "copypasta": copypasta,
    "amplification_ring": amplification_ring,
    "reply_brigade": reply_brigade,
    "follow_farm": follow_farm,
    "hashtag_flood": hashtag_flood,
    "sockpuppet_cluster": sockpuppet_cluster,
    "astroturf_campaign": astroturf_campaign,
    "account_handover": account_handover,
    "fandom_burst": fandom_burst,
    "breaking_news": breaking_news,
    "mutual_follow_community": mutual_follow_community,
}

POST_COLUMNS = ["source", "target", "timestamp", "template_id", "campaign_id"]
INTERACTION_COLUMNS = ["source", "target", "timestamp", "topic", "campaign_id"]


def inject(
    rng: np.random.Generator,
    pop: Population,
    config: CoordinationConfig,
    n_devices: int,
) -> Injection:
    """Run every campaign the config asks for, then the organic decoys."""
    ctx = PlaybookContext(rng, pop, config, n_devices)
    campaigns: list[Campaign] = []

    counts = config.campaign_counts
    for playbook in PLAYBOOKS:
        for index in range(counts.get(playbook, 0)):
            campaign = Campaign(
                campaign_id=f"{playbook}_{index}", playbook=playbook, is_coordinated=True
            )
            PLAYBOOK_FUNCTIONS[playbook](ctx, campaign)
            if campaign.roles:
                campaigns.append(campaign)

    # Decoys last, and without overlap: an organic structure that shared
    # members with a campaign would have an ambiguous label, and the whole
    # point of it is to be unambiguously legitimate.
    ctx.allow_overlap = False
    total_decoys = round(len(campaigns) * config.profile.decoy_ratio)
    if total_decoys:
        per_decoy = max(1, total_decoys // len(DECOY_PLAYBOOKS))
        for playbook in DECOY_PLAYBOOKS:
            for index in range(per_decoy):
                campaign = Campaign(
                    campaign_id=f"{playbook}_{index}",
                    playbook=playbook,
                    is_coordinated=False,
                )
                PLAYBOOK_FUNCTIONS[playbook](ctx, campaign)
                if campaign.roles:
                    campaigns.append(campaign)

    events: dict[str, pl.DataFrame] = {}
    for channel, rows in ctx.rows.items():
        if not rows:
            continue
        if channel == POSTED:
            sources, targets, stamps, templates, ids = zip(*rows)
            events[channel] = pl.DataFrame(
                {
                    "source": pop.account_ids.gather(list(sources)),
                    "target": pop.topic_ids.gather(list(targets)),
                    "timestamp": np.array(stamps, dtype="datetime64[us]"),
                    "template_id": np.array(templates, dtype=np.int64),
                    "campaign_id": list(ids),
                }
            )
        else:
            sources, targets, stamps, topics, ids = zip(*rows)
            events[channel] = pl.DataFrame(
                {
                    "source": pop.account_ids.gather(list(sources)),
                    "target": pop.account_ids.gather(list(targets)),
                    "timestamp": np.array(stamps, dtype="datetime64[us]"),
                    "topic": pop.topic_ids.gather(list(topics)),
                    "campaign_id": list(ids),
                }
            )

    return Injection(
        campaigns=campaigns,
        events=events,
        shared_devices=ctx.shared_devices,
        extra_follows=ctx.extra_follows,
        fresh_accounts=ctx.fresh_accounts,
        stripped_accounts=ctx.stripped_accounts,
        dormant_until=ctx.dormant_until,
    )
