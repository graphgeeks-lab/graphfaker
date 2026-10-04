"""Finding the ground truth in a dataset, whatever the domain calls it.

Every pack that injects patterns writes the same four truth tables, and each
one names them after its own subject. The fraud pack writes ``patterns``
(``pattern_id``, ``typology``, ``is_fraud``), ``accounts``, ``transactions``
(``tx_id``) and a latent ``region``. The coordination pack writes
``campaigns`` (``campaign_id``, ``playbook``, ``is_coordinated``),
``accounts``, ``events`` (``event_id``) and a latent ``community``. Column
for column and position for position they are the same layout.

The sinks used to read that layout by name, which meant they worked for the
fraud pack and silently did the wrong thing for anything else: a coordination
dataset failed to load into DuckDB and LadybugDB, and loaded into Neo4j with
its ground truth quietly missing. This module finds the frames by shape
instead, the way the PyTorch Geometric sink already did, so a domain keeps
its own vocabulary in the database rather than borrowing a bank's.

What stays fixed is the structure: the patterns become ``Pattern`` nodes and
the memberships ``IN_PATTERN`` relationships, in every domain, because that
is what the verifier and every query in the documentation are written
against. What varies is the properties on them, which are the domain's own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from graphfaker.backends.tables import ID, GraphTables

#: The node label a pattern becomes, in any domain.
PATTERN_LABEL = "Pattern"
#: The relationship a membership becomes, in any domain.
MEMBER_REL = "IN_PATTERN"


def _id_columns(frame: pl.DataFrame) -> list[str]:
    return [c for c in frame.columns if c.endswith("_id")]


@dataclass(frozen=True)
class TruthLayout:
    """Which truth frame is which, and what each one calls its columns.

    Empty when a dataset has no injected patterns, which is the case for a
    plain schema run: every sink then skips the truth entirely.
    """

    #: Frame key (and so the Parquet file name) to frame, for each role.
    patterns: tuple[str, pl.DataFrame] | None = None
    members: tuple[str, pl.DataFrame] | None = None
    events: tuple[str, pl.DataFrame] | None = None
    #: ``pattern_id`` or ``campaign_id``: the pattern's own id column, and the
    #: column that refers to it from the membership and event frames.
    pattern_id: str = "pattern_id"
    #: ``account_id``: which entity the memberships are for.
    member_id: str = "account_id"
    #: The node label those ids belong to, resolved against the node tables.
    member_label: str = "Account"
    #: ``tx_id`` or ``event_id``: how the event frame refers to an edge.
    event_id: str = "tx_id"
    #: The single boolean saying whether a pattern is the thing being looked
    #: for: ``is_fraud``, ``is_coordinated``.
    label: str | None = None
    #: Latent factor frames, keyed by the node label they become.
    latent: dict[str, pl.DataFrame] = field(default_factory=dict)

    @property
    def has_patterns(self) -> bool:
        return self.patterns is not None and self.patterns[1].height > 0

    @property
    def has_members(self) -> bool:
        return self.members is not None and self.members[1].height > 0

    @property
    def has_events(self) -> bool:
        return self.events is not None and self.events[1].height > 0

    @property
    def event_columns(self) -> list[str]:
        """The columns an event frame copies onto the edges it labels.

        Everything except the id used to find the edge, so a bank's edges end
        up with ``pattern_id``, ``typology`` and ``is_fraud`` and a platform's
        with ``campaign_id``, ``playbook`` and ``is_coordinated``.
        """
        if self.events is None:
            return []
        return [c for c in self.events[1].columns if c != self.event_id]

    def frame(self, role: str) -> pl.DataFrame | None:
        found = getattr(self, role)
        return None if found is None else found[1]

    def file(self, role: str) -> str:
        """The Parquet file a role lives in, for loaders that read the
        directory rather than the frames."""
        found = getattr(self, role)
        return "" if found is None else f"{found[0]}.parquet"


def layout(truth: dict[str, pl.DataFrame] | None, tables: GraphTables | None = None) -> TruthLayout:
    """Work out the truth layout of a run.

    By shape, in this order, because each rule depends on the one before it:

    * a **latent** frame is keyed by ``group`` and has no id column; it
      describes how a hidden group was drawn rather than labelling anything.
    * the **patterns** frame is the one with a ``roles`` column. Its first
      ``*_id`` column is the pattern id, and its single boolean is the label.
    * the **members** frame has a ``role`` column and refers to the pattern
      id; its other id column says which entity is a member.
    * the **events** frame is whatever is left that refers to the pattern id;
      its other id column is how it finds the edge to label.
    """
    if not truth:
        return TruthLayout()

    latent = {
        name.title(): frame
        for name, frame in truth.items()
        if "group" in frame.columns and not _id_columns(frame)
    }
    rest = {name: frame for name, frame in truth.items() if name.title() not in latent}

    patterns = next(((n, f) for n, f in rest.items() if "roles" in f.columns), None)
    if patterns is None:
        # A pack that does not denormalise the roles onto the pattern frame
        # still has one thing the others point at: the frame whose own id
        # column appears in another truth frame is the patterns.
        patterns = next(
            (
                (n, f)
                for n, f in rest.items()
                if _id_columns(f)
                and any(
                    _id_columns(f)[0] in other.columns for m, other in rest.items() if m != n
                )
            ),
            None,
        )
    if patterns is None:
        return TruthLayout(latent=latent)
    pattern_frame = patterns[1]
    pattern_id = next(iter(_id_columns(pattern_frame)), "pattern_id")
    booleans = [c for c, t in pattern_frame.schema.items() if t == pl.Boolean]

    members = next(
        (
            (n, f)
            for n, f in rest.items()
            if n != patterns[0] and "role" in f.columns and pattern_id in f.columns
        ),
        None,
    )
    member_id = "account_id"
    if members is not None:
        others = [c for c in _id_columns(members[1]) if c != pattern_id]
        member_id = others[0] if others else member_id

    events = next(
        (
            (n, f)
            for n, f in rest.items()
            if n not in {patterns[0], members[0] if members else ""}
            and pattern_id in f.columns
            and any(c != pattern_id for c in _id_columns(f))
        ),
        None,
    )
    event_id = "tx_id"
    if events is not None:
        event_id = next(c for c in _id_columns(events[1]) if c != pattern_id)

    return TruthLayout(
        patterns=patterns,
        members=members,
        events=events,
        pattern_id=pattern_id,
        member_id=member_id,
        member_label=_member_label(members, member_id, tables),
        event_id=event_id,
        label=booleans[0] if len(booleans) == 1 else None,
        latent=latent,
    )


def _member_label(
    members: tuple[str, pl.DataFrame] | None, member_id: str, tables: GraphTables | None
) -> str:
    """Which node label the members are, found by matching their ids.

    Falls back to the column's own name (``account_id`` -> ``Account``) when
    there are no tables to match against, which is the case for a loader
    writing SQL from a directory it has not read.
    """
    guess = member_id.removesuffix("_id").replace("_", " ").title().replace(" ", "")
    if members is None or tables is None:
        return guess
    sample = set(members[1][member_id].drop_nulls().to_list()[:200])
    if not sample:
        return guess
    for label, frame in tables.nodes.items():
        if sample <= set(frame[ID].to_list()):
            return label
    return guess
