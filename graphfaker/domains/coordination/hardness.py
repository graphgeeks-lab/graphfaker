"""Measure, rather than assert, how hard the injected coordination is to find.

For every playbook, each single feature a naive detector might threshold on is
scored by the AUC it achieves separating that playbook's accounts from accounts
in no campaign at all. ``max_auc`` is the number to quote: at
``tradecraft="high"`` no single feature should carry much, which means the
campaign is only findable by looking at structure and timing together.

Two things here that the fraud pack's report does not need:

* **Decoys are scored too.** An organic structure is doing its job only if it
  is *hard to separate from its twin*. ``decoy_separability`` scores each
  decoy's accounts against the accounts of the inauthentic playbook it
  imitates. A low number is the good outcome; a high one means the decoy is
  not actually a decoy and the benchmark is easier than it looks.
* **A follow-only playbook has no events.** A follow farm barely posts, so any
  report built on activity features alone will say it is undetectable. The
  feature set therefore spans the follow graph, the event stream, the device
  graph and account age, and the report says which family carried the signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID
from graphfaker.domains.coordination.config import DECOY_TWIN
from graphfaker.domains.coordination.process import POSTED, REPLIED, RESHARED
from graphfaker.engine.run import GraphRun

#: Features grouped by the family they come from, so a report can say *which*
#: kind of evidence found a playbook rather than only how much.
FEATURE_FAMILIES = {
    "activity": ["event_count", "post_count", "reshare_count", "reply_count", "events_per_day"],
    "structure": ["following", "follower_count", "reciprocity", "clustering_proxy"],
    "timing": ["burst_share", "active_hours"],
    "content": ["topic_concentration", "template_reuse"],
    "identity": ["account_age_days", "device_shared_with"],
}
ACCOUNT_FEATURES = [name for names in FEATURE_FAMILIES.values() for name in names]


def auc(positive: np.ndarray, negative: np.ndarray) -> float:
    """Rank-based AUC (Mann-Whitney), returned as distance from chance.

    Symmetrised to ``max(a, 1 - a)``: a feature that is strongly *low* for a
    playbook is just as useful to a detector as one that is strongly high, and
    reporting 0.05 as "weak" would be wrong.
    """
    n_pos, n_neg = len(positive), len(negative)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    joined = np.concatenate([positive, negative])
    order = joined.argsort(kind="stable")
    ranks = np.empty(len(joined), dtype=np.float64)
    ranks[order] = np.arange(1, len(joined) + 1)
    # Average ranks within ties, or a constant feature scores 1.0.
    _, inverse, counts = np.unique(joined, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inverse, ranks)
    ranks = (sums / counts)[inverse]
    value = (ranks[:n_pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return float(max(value, 1.0 - value))


def account_features(run: GraphRun) -> pl.DataFrame:
    """One row per account, with everything a detector could legitimately use.

    Nothing derived from ``run.truth`` appears here. That is the whole contract:
    these are the features, the truth is the answer, and the two must not touch.
    """
    accounts = run.tables.nodes["Account"]
    ids = accounts[ID]
    n = accounts.height
    index = {str(a): i for i, a in enumerate(ids.to_list())}

    counts = {name: np.zeros(n, dtype=np.float64) for name in ("post", "reshare", "reply")}
    channel_names = {POSTED: "post", RESHARED: "reshare", REPLIED: "reply"}
    topic_pairs: list[tuple[int, str]] = []
    template_pairs: list[tuple[int, int]] = []
    hour_pairs: list[tuple[int, int]] = []

    for channel, key in channel_names.items():
        frame = run.tables.edges.get(channel)
        if frame is None or frame.height == 0:
            continue
        sources = np.array([index.get(s, -1) for s in frame["source"].to_list()])
        valid = sources >= 0
        np.add.at(counts[key], sources[valid], 1.0)

        topic_column = "target" if channel == POSTED else "topic"
        if topic_column in frame.columns:
            topics = frame[topic_column].to_list()
            topic_pairs.extend(
                (int(s), str(t)) for s, t, ok in zip(sources, topics, valid) if ok
            )
        if channel == POSTED and "template_id" in frame.columns:
            templates = frame["template_id"].to_list()
            template_pairs.extend(
                (int(s), int(t)) for s, t, ok in zip(sources, templates, valid) if ok
            )
        stamps = frame["timestamp"].cast(pl.Datetime("us")).to_numpy().astype("datetime64[h]")
        hours = stamps.view(np.int64)
        hour_pairs.extend((int(s), int(h)) for s, h, ok in zip(sources, hours, valid) if ok)

    event_count = counts["post"] + counts["reshare"] + counts["reply"]

    # Follow graph: out-degree, in-degree, reciprocity, and a cheap clustering
    # proxy (how many of an account's followees also follow each other).
    following = np.zeros(n, dtype=np.float64)
    reciprocal = np.zeros(n, dtype=np.float64)
    follows = run.tables.edges.get("FOLLOWS")
    pairs: set[tuple[int, int]] = set()
    out_neighbours: dict[int, set[int]] = {}
    if follows is not None and follows.height:
        src = np.array([index.get(s, -1) for s in follows["source"].to_list()])
        dst = np.array([index.get(t, -1) for t in follows["target"].to_list()])
        ok = (src >= 0) & (dst >= 0)
        src, dst = src[ok], dst[ok]
        np.add.at(following, src, 1.0)
        pairs = set(zip(src.tolist(), dst.tolist()))
        for a, b in pairs:
            out_neighbours.setdefault(a, set()).add(b)
        for a, b in pairs:
            if (b, a) in pairs:
                reciprocal[a] += 1.0

    clustering_proxy = np.zeros(n, dtype=np.float64)
    for account, neighbours in out_neighbours.items():
        if len(neighbours) < 2:
            continue
        linked = sum(
            1
            for other in neighbours
            if out_neighbours.get(other) and out_neighbours[other] & neighbours
        )
        clustering_proxy[account] = linked / len(neighbours)

    follower_count = (
        accounts["follower_count"].to_numpy().astype(np.float64)
        if "follower_count" in accounts.columns
        else np.zeros(n)
    )

    # Content concentration and template reuse.
    topic_concentration = np.zeros(n, dtype=np.float64)
    if topic_pairs:
        by_account: dict[int, dict[str, int]] = {}
        for account, topic in topic_pairs:
            by_account.setdefault(account, {}).setdefault(topic, 0)
            by_account[account][topic] += 1
        for account, topics in by_account.items():
            total = sum(topics.values())
            topic_concentration[account] = max(topics.values()) / total if total else 0.0

    template_reuse = np.zeros(n, dtype=np.float64)
    if template_pairs:
        shared: dict[int, set[int]] = {}
        for account, template in template_pairs:
            shared.setdefault(template, set()).add(account)
        per_account: dict[int, list[int]] = {}
        for account, template in template_pairs:
            per_account.setdefault(account, []).append(template)
        for account, templates in per_account.items():
            if not templates:
                continue
            # Share of this account's posts whose template another account also
            # used: the duplicate-text signal, measured across accounts rather
            # than within one.
            template_reuse[account] = sum(
                1 for t in templates if len(shared.get(t, ())) > 1
            ) / len(templates)

    # Timing: the largest share of an account's events falling in one hour, and
    # how many distinct hours it was active in.
    burst_share = np.zeros(n, dtype=np.float64)
    active_hours = np.zeros(n, dtype=np.float64)
    if hour_pairs:
        by_account_hours: dict[int, dict[int, int]] = {}
        for account, hour in hour_pairs:
            by_account_hours.setdefault(account, {}).setdefault(hour, 0)
            by_account_hours[account][hour] += 1
        for account, hours in by_account_hours.items():
            total = sum(hours.values())
            burst_share[account] = max(hours.values()) / total if total else 0.0
            active_hours[account] = len(hours)

    period_days = max(1, int(run.manifest.extra.get("coordination", {}).get("period_days", 90)))
    created = accounts["created_at"].to_numpy().astype("datetime64[D]")
    period_start = np.datetime64(
        run.manifest.extra.get("coordination", {}).get("period_start", "2026-01-01")
    ).astype("datetime64[D]")
    account_age_days = (period_start - created).astype(np.int64).astype(np.float64)

    device_shared_with = np.zeros(n, dtype=np.float64)
    uses = run.tables.edges.get("USES")
    if uses is not None and uses.height:
        per_device = uses.group_by("target").len().rename({"len": "sharers"})
        joined = uses.join(per_device, on="target", how="left")
        src = np.array([index.get(s, -1) for s in joined["source"].to_list()])
        sharers = joined["sharers"].to_numpy().astype(np.float64)
        ok = src >= 0
        np.maximum.at(device_shared_with, src[ok], sharers[ok])

    return pl.DataFrame(
        {
            "account_id": ids,
            "event_count": event_count,
            "post_count": counts["post"],
            "reshare_count": counts["reshare"],
            "reply_count": counts["reply"],
            "events_per_day": event_count / period_days,
            "following": following,
            "follower_count": follower_count,
            "reciprocity": np.divide(
                reciprocal, following, out=np.zeros(n), where=following > 0
            ),
            "clustering_proxy": clustering_proxy,
            "burst_share": burst_share,
            "active_hours": active_hours,
            "topic_concentration": topic_concentration,
            "template_reuse": template_reuse,
            "account_age_days": account_age_days,
            "device_shared_with": device_shared_with,
        }
    )


@dataclass
class PlaybookHardness:
    playbook: str
    is_coordinated: bool
    n_accounts: int
    feature_auc: dict[str, float] = field(default_factory=dict)

    @property
    def max_auc(self) -> float:
        values = [v for v in self.feature_auc.values() if not np.isnan(v)]
        return max(values) if values else float("nan")

    @property
    def best_feature(self) -> str | None:
        ranked = [(v, k) for k, v in self.feature_auc.items() if not np.isnan(v)]
        return max(ranked)[1] if ranked else None

    @property
    def best_family(self) -> str | None:
        best = self.best_feature
        if best is None:
            return None
        for family, names in FEATURE_FAMILIES.items():
            if best in names:
                return family
        return None


@dataclass
class HardnessReport:
    tradecraft: str
    playbooks: list[PlaybookHardness]
    #: decoy playbook -> (AUC, feature) separating it from the playbook it
    #: imitates, by the best single feature. Low is good: it means no one
    #: feature tells the organic structure from the inauthentic one, and a
    #: detector has to combine evidence. It should not be near 0.5 either —
    #: then the task is impossible rather than hard. The feature name matters
    #: as much as the number: separability on ``account_age_days`` is a real
    #: signal a platform would use, while separability on ``template_reuse``
    #: means the decoy's text is not organic enough.
    decoy_separability: dict[str, tuple[float, str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tradecraft": self.tradecraft,
            "playbooks": [
                {
                    "playbook": p.playbook,
                    "is_coordinated": p.is_coordinated,
                    "n_accounts": p.n_accounts,
                    "max_auc": None if np.isnan(p.max_auc) else round(p.max_auc, 4),
                    "best_feature": p.best_feature,
                    "best_family": p.best_family,
                    "feature_auc": {
                        k: None if np.isnan(v) else round(v, 4)
                        for k, v in p.feature_auc.items()
                    },
                }
                for p in self.playbooks
            ],
            "decoy_separability": {
                k: {
                    "auc": None if np.isnan(v) else round(v, 4),
                    "feature": feature,
                    "twin": DECOY_TWIN.get(k),
                }
                for k, (v, feature) in self.decoy_separability.items()
            },
        }

    def summary(self) -> str:
        lines = [
            f"tradecraft: {self.tradecraft}",
            f"{'playbook':<24}{'kind':<14}{'n':>6}{'max AUC':>10}  best feature (family)",
        ]
        for p in sorted(self.playbooks, key=lambda p: -(0 if np.isnan(p.max_auc) else p.max_auc)):
            kind = "coordinated" if p.is_coordinated else "organic"
            auc_text = "n/a" if np.isnan(p.max_auc) else f"{p.max_auc:.3f}"
            lines.append(
                f"{p.playbook:<24}{kind:<14}{p.n_accounts:>6}{auc_text:>10}  "
                f"{p.best_feature or '-'} ({p.best_family or '-'})"
            )
        if self.decoy_separability:
            lines.append("")
            lines.append("decoy separability from its twin (lower is better):")
            for decoy, (value, feature) in sorted(self.decoy_separability.items()):
                twin = DECOY_TWIN.get(decoy, "?")
                text = "n/a" if np.isnan(value) else f"{value:.3f}"
                lines.append(f"  {decoy:<24} vs {twin:<20}{text:>7}  by {feature}")
        return "\n".join(lines)


def hardness_report(run: GraphRun, features: pl.DataFrame | None = None) -> HardnessReport:
    """Score every playbook's separability from uninvolved accounts."""
    features = account_features(run) if features is None else features
    truth = run.truth.get("accounts")
    tradecraft = str(run.manifest.extra.get("coordination", {}).get("tradecraft", "?"))
    if truth is None or truth.height == 0:
        return HardnessReport(tradecraft=tradecraft, playbooks=[])

    position = {a: i for i, a in enumerate(features["account_id"].to_list())}
    members: dict[str, set[int]] = {}
    coordinated: dict[str, bool] = {}
    for account, playbook, is_coordinated in zip(
        truth["account_id"].to_list(),
        truth["playbook"].to_list(),
        truth["is_coordinated"].to_list(),
    ):
        index = position.get(account)
        if index is None:
            continue
        members.setdefault(playbook, set()).add(index)
        coordinated[playbook] = bool(is_coordinated)

    involved = {index for group in members.values() for index in group}
    clean = np.array(sorted(set(range(features.height)) - involved), dtype=np.int64)

    columns = {name: features[name].to_numpy().astype(np.float64) for name in ACCOUNT_FEATURES}
    reports = []
    for playbook, group in sorted(members.items()):
        rows = np.array(sorted(group), dtype=np.int64)
        reports.append(
            PlaybookHardness(
                playbook=playbook,
                is_coordinated=coordinated.get(playbook, True),
                n_accounts=len(rows),
                feature_auc={
                    name: auc(values[rows], values[clean]) for name, values in columns.items()
                },
            )
        )

    separability = {}
    for decoy, twin in DECOY_TWIN.items():
        if decoy in members and twin in members:
            decoy_rows = np.array(sorted(members[decoy]), dtype=np.int64)
            twin_rows = np.array(sorted(members[twin]), dtype=np.int64)
            scored = [
                (auc(values[decoy_rows], values[twin_rows]), name)
                for name, values in columns.items()
            ]
            scored = [(v, n) for v, n in scored if not np.isnan(v)]
            separability[decoy] = max(scored) if scored else (float("nan"), "-")
    return HardnessReport(
        tradecraft=tradecraft, playbooks=reports, decoy_separability=separability
    )


def realism_report(run: GraphRun) -> dict[str, Any]:
    """The properties of the organic platform a reader would check first."""
    features = account_features(run)
    follows = run.tables.edges.get("FOLLOWS")
    followers = features["follower_count"].to_numpy().astype(np.float64)
    events = features["event_count"].to_numpy().astype(np.float64)

    def gini(values: np.ndarray) -> float:
        if len(values) == 0 or values.sum() == 0:
            return 0.0
        ordered = np.sort(values)
        n = len(ordered)
        weighted = ((np.arange(1, n + 1)) * ordered).sum()
        return float((2 * weighted) / (n * ordered.sum()) - (n + 1) / n)

    reciprocal_share = float("nan")
    if follows is not None and follows.height:
        pairs = set(zip(follows["source"].to_list(), follows["target"].to_list()))
        reciprocal_share = sum(1 for a, b in pairs if (b, a) in pairs) / len(pairs)

    hour_counts = np.zeros(24)
    for channel in (POSTED, RESHARED, REPLIED):
        frame = run.tables.edges.get(channel)
        if frame is None or frame.height == 0:
            continue
        hours = (
            frame["timestamp"].cast(pl.Datetime("us")).dt.hour().to_numpy().astype(np.int64)
        )
        np.add.at(hour_counts, hours, 1)

    return {
        "accounts": int(features.height),
        "events": int(events.sum()),
        "follows": 0 if follows is None else int(follows.height),
        "follower_gini": round(gini(followers), 4),
        "follower_max": int(followers.max()) if len(followers) else 0,
        "follower_mean": round(float(followers.mean()), 2) if len(followers) else 0.0,
        "event_gini": round(gini(events), 4),
        "silent_account_share": round(float((events == 0).mean()), 4) if len(events) else 0.0,
        "reciprocal_follow_share": (
            None if np.isnan(reciprocal_share) else round(reciprocal_share, 4)
        ),
        "mean_reciprocity": round(float(features["reciprocity"].mean() or 0.0), 4),
        "mean_clustering_proxy": round(float(features["clustering_proxy"].mean() or 0.0), 4),
        # Peak-to-trough of the diurnal rhythm: flat means the hour profile
        # never made it into the data.
        "diurnal_ratio": (
            round(float(hour_counts.max() / max(hour_counts.min(), 1)), 2)
            if hour_counts.sum()
            else None
        ),
    }
