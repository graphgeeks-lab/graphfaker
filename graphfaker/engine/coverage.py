"""Identifiers that are mostly missing, the way a register's are.

A generated company with every column filled is easier to work with and
less like the world, and the difference matters most for the thing people
build these datasets to try: entity resolution is hard because the
attributes that would settle a match are absent. Measured on the 1,033,773
companies registered in Nevada, cut out of the national open data export of
2026-03-05 (``benchmarks/realism/corporate-nevada.json``), a company record
carries a latitude 67.3% of the time, a postcode 61.9%, a LinkedIn page
33.1%, a Placekey 29.2%, a website 14.7%, a second address line 2.8%, some
other identifier 1.9%, an NPI 0.4% and an LEI 0.06%. The Las Vegas register
agrees within a point on all of them.

So almost every pair of companies has at most one or two attributes in
common, and which ones is close to arbitrary. A resolver cannot plan on
having a postcode. That is the shape, and the reason this module exists is
that GraphFaker had the opposite one: every column present on every row,
which turns a hard problem into a lookup.

Two properties beyond the rates:

* **An identifier that describes a place belongs to the place.** Two
  companies at one door have the same coordinates and the same Placekey
  ``where``, because they are the same door. Drawing them per company would
  hand a resolver a key that address reuse cannot confuse, which is exactly
  the trap the shared addresses exist to set.
* **An identifier that describes the company is derived from its name.** A
  website and a LinkedIn page are built from the name's stem, so a company
  called twice in two spellings has one website, and two different
  companies that happen to share a stem collide on it. Both of those happen
  in registers.

The rates are measured. The formats are not: an LEI here has the right
shape and the correct ISO 17064 check digits but is not registered to
anyone, an NPI passes the Luhn check the real ones do, and a Placekey's
``where`` is not a real H3 cell. They are plausible, checkable strings, not
lookups into anything.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from graphfaker.engine.names import stem

#: Characters a Placekey is written in: digits and consonants, with the
#: letters that read as digits left out.
PLACEKEY_ALPHABET = "23456789bcdfghjkmnpqrstvwxyz"

#: Where a company's website lives, in the shares a register shows.
TLDS = (("com", 0.82), ("net", 0.06), ("org", 0.05), ("co", 0.03), ("biz", 0.02), ("us", 0.02))

#: A unit already written into the street line, which is where a register
#: usually puts one. A second address line only goes on the records that do
#: not have one, so nothing ends up with two suite numbers. The flag is
#: inline because polars compiles the pattern itself and never sees
#: Python's ``re`` flags.
UNIT_IN_LINE1 = r"(?i)\b(suite|ste|apt|apartment|unit|floor|rm|room|box)\b|#"

#: How a second address line is written when there is one.
UNIT_FORMS = (("Suite {n}", 0.42), ("Ste {n}", 0.18), ("Unit {n}", 0.16), ("# {n}", 0.14), ("Floor {n}", 0.10))


@dataclass(frozen=True)
class Attribute:
    """One identifier, how often it is there, and what it describes.

    ``columns`` travel together: a latitude without a longitude is not a
    half-measurement, it is a broken row, so the pair is present or absent
    as one thing.
    """

    name: str
    columns: tuple[str, ...]
    #: Share of company records carrying it.
    rate: float
    #: True when the value describes the address rather than the company,
    #: and so is identical for every company at that address.
    of_the_place: bool = False
    #: False for an attribute this module only thins. A postcode is part of
    #: the address the pack already drew, so taking it away is a coverage
    #: decision and putting it back would be inventing a different address.
    generated: bool = True


@dataclass(frozen=True)
class CoverageProfile:
    """How complete a company record is, and where the numbers came from."""

    name: str
    source: str
    attributes: tuple[Attribute, ...]

    def rate(self, name: str) -> float:
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute.rate
        raise KeyError(name)


#: The measured profile. Order is the order the identifiers are generated
#: in, which is fixed because moving it would move every dataset.
REGISTER = CoverageProfile(
    name="us-register",
    source="Nevada, 1,033,773 companies in the 2026-03-05 open data export",
    attributes=(
        Attribute("geo", ("geo_latitude", "geo_longitude"), 0.6734, of_the_place=True),
        Attribute("postal_code", ("address_postal_code",), 0.6194, of_the_place=True, generated=False),
        Attribute("linkedin", ("linkedin",), 0.3313),
        Attribute("placekey", ("placekey",), 0.2919, of_the_place=True),
        Attribute("website", ("website",), 0.1467),
        Attribute("address_line2", ("address_line2",), 0.0282),
        Attribute("registration_number", ("registration_number",), 0.0191),
        Attribute("npi_number", ("npi_number",), 0.0038),
        Attribute("lei_number", ("lei_number",), 0.0006),
    ),
)

#: Every identifier on every row. What GraphFaker did before this module,
#: kept because a domain that wants complete records should say so rather
#: than get them by accident.
COMPLETE = CoverageProfile(
    name="complete",
    source="not a register",
    attributes=tuple(
        Attribute(a.name, a.columns, 1.0, a.of_the_place, a.generated) for a in REGISTER.attributes
    ),
)


def address_index(frame: pl.DataFrame) -> np.ndarray:
    """A number per distinct address, so rows at one door agree.

    Dense and in order of first appearance, which makes it a stable key
    inside one run without having to hash anything.
    """
    parts = [c for c in ("address_line1", "address_city") if c in frame.columns]
    if not parts:
        return np.arange(frame.height, dtype=np.int64)
    key = frame.select(pl.concat_str(parts, separator="|", ignore_nulls=True).alias("k"))["k"]
    return key.cast(pl.Categorical).to_physical().to_numpy().astype(np.int64)


def _present(rng: np.random.Generator, size: int, rate: float) -> np.ndarray:
    """Which rows carry an attribute."""
    if rate >= 1.0:
        return np.ones(size, dtype=bool)
    if rate <= 0.0:
        return np.zeros(size, dtype=bool)
    return rng.random(size) < rate


def _place_present(rng: np.random.Generator, places: np.ndarray, rate: float) -> np.ndarray:
    """The same, decided once per address rather than once per row.

    An address either has been geocoded or has not, and every company at it
    inherits the answer. Which addresses are covered is drawn in proportion
    to how many companies sit at them, for two reasons. The register's rate
    is a share of records, and a coin flip per address misses it by several
    points when a handful of agent addresses hold half the rows. And the
    bias is the right way round: an address with ninety thousand companies
    registered at it is a commercial building someone has certainly
    geocoded, not a hard case.
    """
    if not len(places):
        return np.zeros(0, dtype=bool)
    load = np.bincount(places)
    if rate >= 1.0:
        return np.ones(len(places), dtype=bool)
    if rate <= 0.0:
        return np.zeros(len(places), dtype=bool)

    # Weighted sampling without replacement (Efraimidis-Spirakis): a
    # uniform raised to one over the weight orders the addresses with the
    # busiest likeliest first, and the cut falls where the covered records
    # reach the rate.
    keys = rng.random(len(load)) ** (1.0 / np.maximum(load, 1))
    order = np.argsort(-keys)
    reached = np.cumsum(load[order])
    target = rate * len(places)
    take = int(np.searchsorted(reached, target, side="left"))
    if take < len(order) and abs(reached[take] - target) < abs(target - (reached[take - 1] if take else 0)):
        take += 1
    covered = np.zeros(len(load), dtype=bool)
    covered[order[:take]] = True
    return covered[places]


def _second_line(rng: np.random.Generator, frame: pl.DataFrame, rate: float) -> np.ndarray:
    """Which rows get a second address line, among those with room for one."""
    taken = frame["address_line1"].str.contains(UNIT_IN_LINE1).fill_null(False).to_numpy()
    free = ~taken
    share = float(free.mean())
    if share <= 0.0:
        return np.zeros(frame.height, dtype=bool)
    return free & _present(rng, frame.height, min(1.0, rate / share))


def _slug(name: str) -> str:
    """A company name as it would appear in a domain or a profile URL."""
    bare = stem(str(name)).lower()
    return "".join(c for c in bare if c.isalnum() or c == " ").strip().replace(" ", "-")[:40] or "company"


def _pick(rng: np.random.Generator, options: tuple[tuple[str, float], ...], size: int) -> np.ndarray:
    values = np.array([v for v, _ in options])
    weights = np.array([w for _, w in options], dtype=float)
    return values[rng.choice(len(values), size=size, p=weights / weights.sum())]


def websites(rng: np.random.Generator, names: list[str]) -> list[str]:
    """A domain per name. Two companies with one stem get one domain."""
    tlds = _pick(rng, TLDS, len(names))
    return [f"www.{_slug(name)}.{tld}" for name, tld in zip(names, tlds, strict=True)]


def linkedin_urls(names: list[str]) -> list[str]:
    """A company page per name, which is how a register records one."""
    return [f"https://www.linkedin.com/company/{_slug(name)}" for name in names]


def _triples(rng: np.random.Generator, size: int, groups: int) -> np.ndarray:
    """``groups`` dash-separated triples from the Placekey alphabet."""
    letters = np.array(list(PLACEKEY_ALPHABET))
    picked = letters[rng.integers(0, len(letters), size=(size, groups * 3))]
    joined = ["".join(row) for row in picked]
    return np.array(["-".join(s[i : i + 3] for i in range(0, len(s), 3)) for s in joined])


def placekeys(rng: np.random.Generator, places: np.ndarray, what_rate: float = 0.3) -> list[str]:
    """A Placekey per row, sharing its ``where`` with the same address.

    Most of a register's Placekeys are address-only and written ``@where``;
    the rest name a point of interest at that address and carry a ``what``
    as well. The 30% split is assumed, not measured.
    """
    size = len(places)
    if not size:
        return []
    where = _triples(rng, int(places.max()) + 1, 3)[places]
    what = _triples(rng, size, 2)
    has_what = _present(rng, size, what_rate)
    return [f"{w}@{p}" if keep else f"@{p}" for w, p, keep in zip(what, where, has_what, strict=True)]


#: A bounding box the coordinates land in. Deliberately one box rather than
#: a per-country atlas: these are not real places, and a generated latitude
#: that looked like a real lookup would be worse than one that does not.
GEO_BOX = (24.5, 49.0, -124.5, -66.9)


def geo(rng: np.random.Generator, places: np.ndarray) -> tuple[list[float], list[float]]:
    """A latitude and longitude per row, one pair per address.

    Rounded to five decimals, which is about a metre and the precision a
    register publishes at.
    """
    if not len(places):
        return [], []
    count = int(places.max()) + 1
    lat_lo, lat_hi, lon_lo, lon_hi = GEO_BOX
    lat = np.round(rng.uniform(lat_lo, lat_hi, size=count), 5)[places]
    lon = np.round(rng.uniform(lon_lo, lon_hi, size=count), 5)[places]
    return lat.tolist(), lon.tolist()


def unit_numbers(rng: np.random.Generator, size: int) -> list[str]:
    """Second address lines: the suite, floor or unit at a shared door."""
    forms = _pick(rng, UNIT_FORMS, size)
    numbers = rng.integers(1, 900, size=size)
    return [form.format(n=n) for form, n in zip(forms, numbers, strict=True)]


def _mod97(body: str) -> int:
    """ISO 7064 MOD 97-10 over a string, letters counted as A=10..Z=35."""
    digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in body)
    remainder = 0
    for chunk in (digits[i : i + 9] for i in range(0, len(digits), 9)):
        remainder = int(str(remainder) + chunk) % 97
    return remainder


def lei_numbers(rng: np.random.Generator, size: int) -> list[str]:
    """Twenty-character LEIs with the check digits a real one carries.

    Four characters of local operating unit, two reserved zeros, twelve of
    entity, then the ISO 7064 check. Shaped and checkable, registered to
    nobody.
    """
    alphabet = np.array(list("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    bodies = alphabet[rng.integers(0, len(alphabet), size=(size, 16))]
    out = []
    for row in bodies:
        body = "".join(row[:4]) + "00" + "".join(row[4:])
        out.append(f"{body}{98 - _mod97(body + '00'):02d}")
    return out


def npi_numbers(rng: np.random.Generator, size: int) -> list[str]:
    """Ten-digit NPIs, Luhn-checked over the 80840 prefix the standard adds.

    Starting at 2, which is the range reserved for organisations; a 1 would
    be a person's NPI on a company record.
    """
    out = []
    for value in rng.integers(200_000_000, 299_999_999, size=size):
        digits = [int(c) for c in f"80840{value}"]
        total = 0
        for position, digit in enumerate(reversed(digits)):
            doubled = digit * 2 if position % 2 == 0 else digit
            total += doubled - 9 if doubled > 9 else doubled
        out.append(f"{value}{(10 - total % 10) % 10}")
    return out


def registration_numbers(rng: np.random.Generator, size: int) -> list[str]:
    """A state filing number, which is what a register's other id usually is."""
    years = rng.integers(1988, 2027, size=size)
    serials = rng.integers(1, 9_999_999, size=size)
    return [f"E{serial:07d}-{year}" for serial, year in zip(serials, years, strict=True)]


def apply(
    rng: np.random.Generator,
    frame: pl.DataFrame,
    profile: CoverageProfile = REGISTER,
    *,
    name_column: str = "name",
) -> pl.DataFrame:
    """Add a register's identifiers to ``frame``, at a register's coverage.

    Columns the frame already has are thinned rather than replaced, which
    is how the postcode goes from on every row to on 61.9% of them. Columns
    it does not have are generated for the rows that carry them and left
    null for the rest. The order the attributes are generated in is the
    order they are declared in, because changing it changes every dataset.
    """
    rows = frame.height
    if not rows:
        return frame
    places = address_index(frame)
    names = frame[name_column].to_list() if name_column in frame.columns else [""] * rows

    for attribute in profile.attributes:
        if attribute.columns[0] not in frame.columns and not attribute.generated:
            continue
        if attribute.name == "address_line2" and "address_line1" in frame.columns:
            present = _second_line(rng, frame, attribute.rate)
        elif attribute.of_the_place:
            present = _place_present(rng, places, attribute.rate)
        else:
            present = _present(rng, rows, attribute.rate)
        kept = int(present.sum())
        if attribute.columns[0] in frame.columns:
            # Already generated: thin it, so a dataset loses the column on
            # the rows a register would not have it for.
            frame = frame.with_columns(
                [
                    pl.when(pl.Series(present)).then(pl.col(column)).otherwise(None).alias(column)
                    for column in attribute.columns
                ]
            )
            continue
        frame = frame.with_columns(
            [
                pl.Series(column, values, dtype=dtype)
                for column, values, dtype in _generate(rng, attribute, present, kept, places, names)
            ]
        )
    return frame


def _generate(
    rng: np.random.Generator,
    attribute: Attribute,
    present: np.ndarray,
    kept: int,
    places: np.ndarray,
    names: list[str],
) -> list[tuple[str, list, pl.DataType]]:
    """The values for one attribute, null where the row does not carry it."""
    def scatter(values: list, dtype: pl.DataType) -> list:
        out: list = [None] * len(present)
        for index, value in zip(np.flatnonzero(present).tolist(), values, strict=True):
            out[index] = value
        del dtype
        return out

    if attribute.name == "geo":
        lat, lon = geo(rng, places[present])
        return [
            ("geo_latitude", scatter(lat, pl.Float64), pl.Float64),
            ("geo_longitude", scatter(lon, pl.Float64), pl.Float64),
        ]
    if attribute.name == "placekey":
        return [("placekey", scatter(placekeys(rng, places[present]), pl.String), pl.String)]
    if attribute.name == "linkedin":
        chosen = [names[i] for i in np.flatnonzero(present).tolist()]
        return [("linkedin", scatter(linkedin_urls(chosen), pl.String), pl.String)]
    if attribute.name == "website":
        chosen = [names[i] for i in np.flatnonzero(present).tolist()]
        return [("website", scatter(websites(rng, chosen), pl.String), pl.String)]
    if attribute.name == "address_line2":
        return [("address_line2", scatter(unit_numbers(rng, kept), pl.String), pl.String)]
    if attribute.name == "registration_number":
        return [("registration_number", scatter(registration_numbers(rng, kept), pl.String), pl.String)]
    if attribute.name == "npi_number":
        return [("npi_number", scatter(npi_numbers(rng, kept), pl.String), pl.String)]
    if attribute.name == "lei_number":
        return [("lei_number", scatter(lei_numbers(rng, kept), pl.String), pl.String)]
    raise KeyError(f"no generator for {attribute.name!r}")


def coverage_stats(frame: pl.DataFrame, profile: CoverageProfile = REGISTER) -> dict[str, float]:
    """Measured coverage of each declared attribute, for checking a run."""
    out = {}
    for attribute in profile.attributes:
        column = attribute.columns[0]
        if column in frame.columns:
            out[attribute.name] = round(float(frame[column].is_not_null().mean() or 0.0), 4)
    return out
