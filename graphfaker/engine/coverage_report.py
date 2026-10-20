"""What is actually in a dataset: the third metric, next to hardness and realism.

Hardness says how hard the injected patterns are to find. Realism says how
much the ordinary process looks like the world. Neither says what the
dataset *contains*, and that turns out to be the question people ask first
and the one nothing answered: are all eleven typologies in here, or did a
small run produce nine copies of the most common one? Did any pattern
recruit a savings account? Did any of them happen at three in the morning?
Is there a decoy for every shape, or only for the three that had budget
left?

Those are all answerable from the truth tables plus the catalogue, and they
are answerable the same way for every pack, because every pack writes the
same four truth frames (:mod:`graphfaker.sinks.truth` finds them by shape)
and declares the same :class:`~graphfaker.schema.patterns.PatternCatalog`.
So this module is domain-agnostic on purpose: a pack gets the report by
existing rather than by implementing anything.

The catalogue matters more than it looks. The truth tables hold what
happened; only the catalogue knows what was *supposed* to be possible, so
without it this can report what a run contains and not what it is missing,
which is the half worth having.

Five things, in the order they matter:

* **Shapes.** Every shape in the catalogue, how many patterns of it exist,
  how big they were, and which shapes produced nothing at all.
* **Roles.** Which roles the members filled, per shape. Observed rather than
  declared: a ``PatternSpec`` does not list its roles, so a role that a
  shape can produce but did not is invisible here.
* **Regimes.** Where the pattern events sit in the ordinary process:
  which channels, which part of the day and week, which amount deciles,
  each against the background of every event in the run. A typology that
  only ever fires at noon on a Tuesday for a four-figure sum is a typology
  a detector can learn the shortcut to.
* **Segments.** Which parts of the population were recruited, against the
  population's own mix. Patterns drawn only from checking accounts make
  ``account_type`` a free feature.
* **Twins.** Each shape against the decoy that imitates it. A decoy with no
  injected twin, or an injected shape whose decoy never appeared, is a
  precision measurement that will not mean what it says.

Nothing here is a verdict. A gap can be the right answer: a small run cannot
carry fourteen shapes at a floor of two, and some typologies genuinely only
happen on weekdays. The report says what is there so a reader can decide,
which is the same contract the hardness report has.

Not to be confused with :mod:`graphfaker.engine.coverage`, which is about
how often a generated company carries an identifier at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from graphfaker.engine.run import GraphRun
from graphfaker.schema.patterns import PatternCatalog
from graphfaker.sinks.truth import TruthLayout, layout

#: Hours of the day, named. Four buckets rather than twenty-four because the
#: question is which regimes were touched, not the shape of the curve, and
#: the hardness report already measures the hour as a feature.
DAY_PARTS = (("night", 0, 6), ("morning", 6, 12), ("afternoon", 12, 18), ("evening", 18, 24))

#: How many distinct values a column can have and still be a segment. Above
#: this it is an identifier or a measurement, not a part of the population.
MAX_SEGMENT_VALUES = 12

#: How much of the population a column has to describe to be a segment. A
#: sparse identifier looks low-cardinality for the wrong reason: an LEI on
#: nine rows out of a thousand has nine distinct values, and none of them is
#: a part of the population anybody belongs to.
MIN_SEGMENT_COVERAGE = 0.5

#: Columns that are never segments whatever their cardinality.
NOT_SEGMENTS = frozenset({"id", "name", "handle", "display_name", "fingerprint", "email", "phone", "iban"})


@dataclass(frozen=True)
class CoverageReport:
    """What a run contains, as five frames and the headline counts."""

    #: shape, kind, patterns, members, events, and the size quantiles.
    shapes: pl.DataFrame
    #: shape, role, members.
    roles: pl.DataFrame
    #: dimension, bucket, pattern_events, pattern_share, background_share, lift.
    regimes: pl.DataFrame
    #: attribute, value, recruited, recruited_share, population_share, lift.
    segments: pl.DataFrame
    #: shape, patterns, decoy, decoy_patterns.
    twins: pl.DataFrame
    #: Catalogue shapes that produced no pattern at all.
    missing: tuple[str, ...] = ()
    #: Set when the run was read without its pack, so "missing" is unknown.
    catalogue_known: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def shapes_covered(self) -> tuple[int, int]:
        """Shapes with at least one pattern, out of the catalogue."""
        present = int((self.shapes["patterns"] > 0).sum()) if self.shapes.height else 0
        return present, self.shapes.height

    @property
    def empty(self) -> bool:
        return self.shapes.height == 0

    def thin(self, lift: float = 0.25) -> pl.DataFrame:
        """Regime buckets the patterns barely touched, worst first.

        A bucket the background uses and the patterns do not is where a
        detector finds a shortcut that has nothing to do with the typology.
        """
        if not self.regimes.height:
            return self.regimes
        return self.regimes.filter(pl.col("lift") < lift).sort("background_share", descending=True)

    def as_dict(self) -> dict[str, Any]:
        """Plain data, for ``--json`` and for saving beside a run."""
        present, total = self.shapes_covered
        return {
            "shapes_covered": present,
            "shapes_in_catalogue": total,
            "missing": list(self.missing),
            "catalogue_known": self.catalogue_known,
            "shapes": self.shapes.to_dicts(),
            "roles": self.roles.to_dicts(),
            "regimes": self.regimes.to_dicts(),
            "segments": self.segments.to_dicts(),
            "twins": self.twins.to_dicts(),
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        if self.empty:
            return "coverage report: no injected patterns in this run"
        present, total = self.shapes_covered
        lines = [f"coverage report: {present} of {total} shapes present", ""]

        lines.append(
            f"{'shape':<24}{'kind':<10}{'patterns':>9}{'members':>9}{'events':>8}"
            f"{'biggest':>9}  {'roles filled'}"
        )
        for row in self.shapes.iter_rows(named=True):
            lines.append(
                f"{row['shape']:<24}{row['kind']:<10}{row['patterns']:>9}"
                f"{row['members']:>9}{row['events']:>8}{row['largest']:>9}  {row['roles']}"
            )
        if self.missing:
            lines.append(f"  nothing injected for: {', '.join(self.missing)}")
        elif not self.catalogue_known:
            lines.append("  catalogue unknown, so this is what the run contains and not what it is missing")

        if self.twins.height:
            lines += ["", f"{'shape':<24}{'patterns':>9}   {'decoy':<24}{'patterns':>9}"]
            for row in self.twins.iter_rows(named=True):
                decoy = row["decoy"] or "(none declared)"
                count = "-" if row["decoy"] is None else str(row["decoy_patterns"])
                lines.append(f"{row['shape']:<24}{row['patterns']:>9}   {decoy:<24}{count:>9}")

        thin = self.thin()
        if thin.height:
            lines += ["", "regimes the patterns barely touched (lift against the whole run)"]
            for row in thin.head(8).iter_rows(named=True):
                lines.append(
                    f"  {row['dimension']:<14}{row['bucket']:<14}"
                    f"patterns {row['pattern_share']:>6.1%}   all {row['background_share']:>6.1%}"
                )

        cold = self.segments.filter(pl.col("recruited") == 0) if self.segments.height else self.segments
        if cold.height:
            lines += ["", "population segments no pattern recruited from"]
            for row in cold.iter_rows(named=True):
                lines.append(
                    f"  {row['attribute']}={row['value']:<18}{row['population_share']:>6.1%} of the population"
                )
        for note in self.notes:
            lines += ["", note]
        return "\n".join(lines)


def coverage_report(run: GraphRun, catalog: PatternCatalog | None = None) -> CoverageReport:
    """What ``run`` contains, measured from its truth tables.

    ``catalog`` defaults to the one declared by the pack that generated the
    run, found through the domain registry. Pass it explicitly for a run
    read back from Parquet by something that does not have the pack.
    """
    truth = layout(run.truth, run.tables)
    catalog = catalog if catalog is not None else _catalog_for(run)
    if not truth.has_patterns:
        return CoverageReport(*(_empty(k) for k in ("shapes", "roles", "regimes", "segments", "twins")))

    patterns = truth.frame("patterns")
    shape_column = _shape_column(truth, catalog)
    if shape_column is None:
        return CoverageReport(
            *(_empty(k) for k in ("shapes", "roles", "regimes", "segments", "twins")),
            notes=("the pattern frame has no shape column, so there is nothing to group by",),
        )

    named = _resolve_names(patterns, truth, shape_column, catalog)
    roles = _roles(truth, named)
    shapes, missing = _shapes(truth, named, catalog, roles)
    return CoverageReport(
        shapes=shapes,
        roles=roles,
        regimes=_regimes(run, truth),
        segments=_segments(run, truth),
        twins=_twins(shapes, catalog),
        missing=missing,
        catalogue_known=catalog is not None,
    )


# ------------------------------------------------------------------ shapes


def _resolve_names(
    patterns: pl.DataFrame, truth: TruthLayout, shape: str, catalog: PatternCatalog | None
) -> pl.DataFrame:
    """``pattern_id`` and the catalogue shape each pattern actually is.

    Two conventions are in the packs and both are readable here. The fraud
    and supply chain packs record a decoy under the name of the shape it
    imitates, on purpose: the shape column says what the structure looks
    like and the label says whether it is the thing being looked for, so a
    detector working from the shape alone cannot tell them apart. The
    coordination pack records a decoy under its own playbook name. Either
    way an unlabelled pattern is the decoy, so resolving it needs the label
    and not the shape column alone.
    """
    names = patterns[shape].to_list()
    if truth.label is None or truth.label not in patterns.columns or catalog is None:
        resolved = names
    else:
        imitated = {name: decoy for decoy, name in catalog.twins.items()}
        resolved = [
            imitated.get(name, name) if not labelled else name
            for name, labelled in zip(names, patterns[truth.label].to_list(), strict=True)
        ]
    return pl.DataFrame({truth.pattern_id: patterns[truth.pattern_id], "shape": resolved})


def _shapes(
    truth: TruthLayout, named: pl.DataFrame, catalog: PatternCatalog | None, roles: pl.DataFrame
) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """One row per catalogue shape, whether or not it produced anything."""
    per_pattern = named.group_by("shape").agg(pl.len().alias("patterns"))
    counts = dict(zip(per_pattern["shape"].to_list(), per_pattern["patterns"].to_list(), strict=True))

    members = _per_pattern_counts(truth.frame("members"), truth.pattern_id, named)
    events = _per_pattern_counts(truth.frame("events"), truth.pattern_id, named)

    filled: dict[str, list[str]] = {}
    for shape, role in zip(roles["shape"].to_list(), roles["role"].to_list(), strict=True):
        filled.setdefault(shape, []).append(role)

    decoys = dict(catalog.twins) if catalog else {}
    names = list(catalog.names) if catalog else sorted(counts)
    rows = []
    for name in names:
        member_sizes = members.get(name, [])
        rows.append({
            "shape": name,
            "kind": "decoy" if name in decoys else "injected",
            "patterns": counts.get(name, 0),
            "members": int(sum(member_sizes)),
            "events": int(sum(events.get(name, []))),
            "smallest": int(min(member_sizes)) if member_sizes else 0,
            "median": float(np.median(member_sizes)) if member_sizes else 0.0,
            "largest": int(max(member_sizes)) if member_sizes else 0,
            "roles": ", ".join(filled.get(name, [])),
        })
    frame = pl.DataFrame(rows, schema=_SHAPE_SCHEMA) if rows else _empty("shapes")
    missing = tuple(name for name in names if counts.get(name, 0) == 0) if catalog else ()
    return frame, missing


def _per_pattern_counts(
    frame: pl.DataFrame | None, pattern_id: str, named: pl.DataFrame
) -> dict[str, list[int]]:
    """Rows per pattern, grouped by the pattern's resolved shape."""
    if frame is None or not frame.height or pattern_id not in frame.columns:
        return {}
    sizes = frame.group_by(pattern_id).agg(pl.len().alias("n")).join(named, on=pattern_id, how="left")
    out: dict[str, list[int]] = {}
    for name, count in zip(sizes["shape"].to_list(), sizes["n"].to_list(), strict=True):
        out.setdefault(name, []).append(int(count))
    return out


def _roles(truth: TruthLayout, named: pl.DataFrame) -> pl.DataFrame:
    """Which roles each shape's members filled.

    Observed rather than declared: a ``PatternSpec`` does not list the roles
    its shape can produce, so a role that never appeared is not visible as a
    gap here the way a missing shape is.
    """
    members = truth.frame("members")
    if members is None or not members.height or "role" not in members.columns:
        return _empty("roles")
    if truth.pattern_id not in members.columns:
        return _empty("roles")
    return (
        members.join(named, on=truth.pattern_id, how="left")
        .group_by(["shape", "role"])
        .agg(pl.len().alias("members"))
        .sort(["shape", "members"], descending=[False, True])
    )


# ----------------------------------------------------------------- regimes


def _regimes(run: GraphRun, truth: TruthLayout) -> pl.DataFrame:
    """Where the pattern events sit against every event in the run."""
    events = truth.frame("events")
    if events is None or not events.height:
        return _empty("regimes")
    flagged = events[truth.event_id].unique().implode()

    rows: list[dict[str, Any]] = []
    channels = {
        name: frame
        for name, frame in run.tables.edges.items()
        if truth.event_id in frame.columns and frame.height
    }
    if not channels:
        return _empty("regimes")

    total_all = sum(frame.height for frame in channels.values())
    marks: list[tuple[str, pl.DataFrame, np.ndarray]] = []
    for name, frame in channels.items():
        inside = frame[truth.event_id].is_in(flagged).to_numpy()
        marks.append((name, frame, inside))
        rows.append(_row("channel", name, int(inside.sum()), frame.height))
    total_pattern = sum(int(inside.sum()) for _, _, inside in marks)
    if not total_pattern:
        return _empty("regimes")

    stamps = [
        (frame["timestamp"].to_numpy(), inside)
        for _, frame, inside in marks
        if "timestamp" in frame.columns
    ]
    if stamps:
        hours = np.concatenate([s.astype("datetime64[h]").astype(np.int64) % 24 for s, _ in stamps])
        days = np.concatenate([
            (s.astype("datetime64[D]").astype(np.int64) + 3) % 7 for s, _ in stamps
        ])
        flags = np.concatenate([inside for _, inside in stamps])
        for label, start, stop in DAY_PARTS:
            bucket = (hours >= start) & (hours < stop)
            rows.append(_row("day part", label, int((bucket & flags).sum()), int(bucket.sum())))
        for label, bucket in (("weekday", days < 5), ("weekend", days >= 5)):
            rows.append(_row("week part", label, int((bucket & flags).sum()), int(bucket.sum())))

    amounts = [
        (frame["amount"].to_numpy(), inside) for _, frame, inside in marks if "amount" in frame.columns
    ]
    if amounts:
        values = np.concatenate([a for a, _ in amounts])
        flags = np.concatenate([inside for _, inside in amounts])
        # Deciles of the run's own amounts, so the buckets mean "small for
        # this dataset" rather than a number out of the air.
        edges = np.quantile(values, np.linspace(0, 1, 11))
        index = np.clip(np.searchsorted(edges, values, side="right") - 1, 0, 9)
        for decile in range(10):
            bucket = index == decile
            rows.append(
                _row(
                    "amount decile",
                    f"{decile + 1} (<= {edges[decile + 1]:,.0f})",
                    int((bucket & flags).sum()),
                    int(bucket.sum()),
                )
            )

    frame = pl.DataFrame(rows, schema=_REGIME_SCHEMA)
    return frame.with_columns(
        (pl.col("pattern_events") / max(total_pattern, 1)).alias("pattern_share"),
        (pl.col("all_events") / max(total_all, 1)).alias("background_share"),
    ).with_columns(
        pl.when(pl.col("background_share") > 0)
        .then(pl.col("pattern_share") / pl.col("background_share"))
        .otherwise(None)
        .alias("lift")
    )


def _row(dimension: str, bucket: str, pattern_events: int, all_events: int) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "bucket": bucket,
        "pattern_events": pattern_events,
        "all_events": all_events,
    }


# ---------------------------------------------------------------- segments


def _segments(run: GraphRun, truth: TruthLayout) -> pl.DataFrame:
    """Which parts of the population the patterns recruited from."""
    members = truth.frame("members")
    population = run.tables.nodes.get(truth.member_label)
    if members is None or not members.height or population is None or not population.height:
        return _empty("segments")

    recruited = members[truth.member_id].unique()
    population = population.with_columns(pl.col("id").is_in(recruited.implode()).alias("_recruited"))
    rows = []
    for column in _segment_columns(population):
        counts = population.group_by(column).agg(
            pl.len().alias("population"), pl.col("_recruited").sum().alias("recruited")
        )
        for row in counts.sort(column).iter_rows(named=True):
            rows.append({
                "attribute": column,
                "value": str(row[column]),
                "recruited": int(row["recruited"]),
                "population": int(row["population"]),
            })
    if not rows:
        return _empty("segments")

    frame = pl.DataFrame(rows, schema=_SEGMENT_SCHEMA)
    total_recruited = max(int(frame["recruited"].sum() / max(frame["attribute"].n_unique(), 1)), 1)
    total_population = max(int(population.height), 1)
    return frame.with_columns(
        (pl.col("recruited") / total_recruited).alias("recruited_share"),
        (pl.col("population") / total_population).alias("population_share"),
    ).with_columns(
        pl.when(pl.col("population_share") > 0)
        .then(pl.col("recruited_share") / pl.col("population_share"))
        .otherwise(None)
        .alias("lift")
    )


def _segment_columns(frame: pl.DataFrame) -> list[str]:
    """Low-cardinality columns that describe a part of the population."""
    out = []
    for column, dtype in frame.schema.items():
        if column in NOT_SEGMENTS or column.endswith("_id") or column.startswith(("address", "_")):
            continue
        if dtype not in (pl.String, pl.Boolean, pl.Categorical, pl.Int64, pl.Int32):
            continue
        present = frame[column].is_not_null()
        if present.mean() < MIN_SEGMENT_COVERAGE:
            continue
        distinct = frame[column].drop_nulls().n_unique()
        if 2 <= distinct <= MAX_SEGMENT_VALUES:
            out.append(column)
    return out


# ------------------------------------------------------------------- twins


def _twins(shapes: pl.DataFrame, catalog: PatternCatalog | None) -> pl.DataFrame:
    """Every injected shape against the decoy that imitates it."""
    if catalog is None or not shapes.height:
        return _empty("twins")
    counts = dict(zip(shapes["shape"].to_list(), shapes["patterns"].to_list(), strict=True))
    imitated = {imitates: decoy for decoy, imitates in catalog.twins.items()}
    rows = [
        {
            "shape": name,
            "patterns": counts.get(name, 0),
            "decoy": imitated.get(name),
            "decoy_patterns": counts.get(imitated.get(name, ""), 0),
        }
        for name in catalog.injected
    ]
    return pl.DataFrame(rows, schema=_TWIN_SCHEMA)


# ------------------------------------------------------------------ plumbing


_SHAPE_SCHEMA = {
    "shape": pl.String, "kind": pl.String, "patterns": pl.Int64, "members": pl.Int64,
    "events": pl.Int64, "smallest": pl.Int64, "median": pl.Float64, "largest": pl.Int64,
    "roles": pl.String,
}
_REGIME_SCHEMA = {
    "dimension": pl.String, "bucket": pl.String, "pattern_events": pl.Int64, "all_events": pl.Int64,
}
_SEGMENT_SCHEMA = {
    "attribute": pl.String, "value": pl.String, "recruited": pl.Int64, "population": pl.Int64,
}
_TWIN_SCHEMA = {"shape": pl.String, "patterns": pl.Int64, "decoy": pl.String, "decoy_patterns": pl.Int64}
_ROLE_SCHEMA = {"shape": pl.String, "role": pl.String, "members": pl.Int64}
_SCHEMAS = {
    "shapes": _SHAPE_SCHEMA, "roles": _ROLE_SCHEMA, "regimes": _REGIME_SCHEMA,
    "segments": _SEGMENT_SCHEMA, "twins": _TWIN_SCHEMA,
}


def _empty(role: str) -> pl.DataFrame:
    return pl.DataFrame(schema=_SCHEMAS[role])


def _shape_column(truth: TruthLayout, catalog: PatternCatalog | None) -> str | None:
    """The column naming which shape a pattern is.

    The one string column the patterns, the members and the events all
    carry: a pack denormalises its shape onto all three so a sink can label
    an edge without a join, and nothing else is on all three but the
    pattern id and the label.
    """
    patterns = truth.frame("patterns")
    if patterns is None:
        return None
    shared = set(patterns.columns)
    for role in ("members", "events"):
        frame = truth.frame(role)
        if frame is not None:
            shared &= set(frame.columns)
    candidates = [
        column
        for column in patterns.columns
        if column in shared
        and column not in {truth.pattern_id, truth.label}
        and patterns.schema[column] == pl.String
    ]
    if catalog is not None:
        known = set(catalog.names)
        for column in candidates:
            if set(patterns[column].to_list()) <= known:
                return column
    return candidates[0] if candidates else None


def _catalog_for(run: GraphRun) -> PatternCatalog | None:
    """The catalogue of the pack that generated ``run``, if it is installed."""
    from graphfaker.domains import available

    domains = available()
    for name in (*run.manifest.extra, run.manifest.schema_name):
        pack = domains.get(name)
        if pack is not None and pack.patterns is not None:
            return pack.patterns
    return None
