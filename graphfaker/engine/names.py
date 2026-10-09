"""Company names shaped like a register's, including the parts that hurt.

Two things about names in a real company register, both measured on the
631,846 companies of the Las Vegas file and reported alongside the Nevada
numbers in ``benchmarks/realism``.

**Different companies almost never share a name.** 0.14% of distinct company
names are used by more than one company, 1.02% once the legal form is
stripped, and the busiest stem belongs to 18 companies. Nevada, measured out
of the national export, says 0.18% and 1.37% with 36 on its busiest stem, so
these are properties of registers rather than of one file. The much larger
number that an earlier version of this project quoted, 86.7%, counts every
organisation *record*, and a company with five locations writes its name five
times. That is a real resolution problem and a different one: one entity
written repeatedly, not two entities colliding.

So a generator has two jobs here, and the easy one is not the one that was
missing. Faker's company provider draws from about a thousand surnames, which
is fine at 500 rows (1.6% of names repeat) and wrong at 200,000, where 10.3%
repeat and one stem carries 1,451 companies. Names here are built to a
measured reuse rate instead, the same way addresses are.

**The legal form is a distribution, and its spelling varies.** LLC 53.9%,
INC 20.5%, nothing at all 11.9%, then ``L.L.C`` 2.6%, ``CORPORATION`` 2.4%,
``LTD`` 1.8%, ``CORP`` 1.5%. Half of all names put a comma before the suffix
and half do not, which is as close to a coin flip as a convention gets.
Faker's provider offers ``PLC``, ``Group`` and ``and Sons`` instead, at rates
no US register would recognise: before this, 81% of our companies had no
suffix at all and 5.6% were LLCs.

:func:`variants` writes one company's name the several ways a register writes
it. That is the thing an entity resolver has to survive, and the thing a
dataset of unique names cannot test.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field

import numpy as np

from graphfaker.engine.seeding import Streams

#: Words a real register's names are full of, in the order they are common:
#: measured as the most frequent stem tokens over the same companies. A
#: generator that draws only surnames produces names no one would file.
INDUSTRY_WORDS = (
    "GROUP", "MANAGEMENT", "SERVICES", "PARTNERSHIP", "ENTERPRISES", "SOLUTIONS",
    "HOLDINGS", "PROPERTIES", "INVESTMENTS", "CAPITAL", "CONSULTING",
    "INTERNATIONAL", "PROPERTY", "DEVELOPMENT", "CONSTRUCTION", "REALTY",
    "TRANSPORT", "LOGISTICS", "SUPPLY", "INDUSTRIES", "MANUFACTURING",
    "TRADING", "DISTRIBUTION", "PARTNERS", "VENTURES", "ASSOCIATES",
)

#: ``form -> spellings``, so a variant of one company's name keeps its legal
#: form and changes only how it is written. The shares inside a form are the
#: measured ones: ``L.L.C`` is 4.7% of all LLCs, ``CORPORATION`` is 61% of
#: all CORPs, ``INCORPORATED`` 2% of all INCs.
FORM_SPELLINGS = {
    "LLC": (("LLC", 0.953), ("L.L.C", 0.047)),
    "INC": (("INC", 0.971), ("INC.", 0.016), ("INCORPORATED", 0.013)),
    "CORP": (("CORPORATION", 0.611), ("CORP", 0.389)),
    "LTD": (("LTD", 0.903), ("LIMITED", 0.097)),
    "CO": (("CO", 0.268), ("COMPANY", 0.732)),
}


@dataclass(frozen=True)
class NameProfile:
    """What company names look like, as numbers.

    Everything here was measured rather than assumed, and ``source`` says on
    what. The one exception is noted on the field.
    """

    #: Suffix spelling to share of companies, as written. ``""`` is no suffix.
    spellings: tuple[tuple[str, float], ...] = (
        ("LLC", 0.5394), ("INC", 0.2051), ("", 0.1194), ("L.L.C", 0.0264),
        ("CORPORATION", 0.0242), ("LTD", 0.0182), ("CORP", 0.0154),
        ("COMPANY", 0.0079), ("INCORPORATED", 0.0065), ("PLLC", 0.0057),
        ("LP", 0.0054), ("FOUNDATION", 0.0049), ("ASSOCIATION", 0.0044),
        ("INC.", 0.0035), ("LIMITED", 0.0031), ("CO", 0.0029), ("PC", 0.0019),
        ("LLP", 0.0018),
    )
    #: Share of names with a comma before the suffix: a measured coin flip.
    comma: float = 0.4978
    #: Words in the stem, by share: one word 10.3%, two 33.3%, three 33.7%.
    stem_words: tuple[tuple[int, float], ...] = (
        (1, 0.1030), (2, 0.3326), (3, 0.3374), (4, 0.1369), (5, 0.0539), (6, 0.0362),
    )
    #: Share of distinct stems used by more than one company.
    stem_reuse: float = 0.0102
    #: The most companies on any one stem.
    max_per_stem: int = 18
    source: str = "Las Vegas company register (631,846 companies)"
    _spelling_cache: dict = field(default_factory=dict, repr=False, compare=False)


#: The measured profile.
REGISTER = NameProfile()

#: Every company its own name, suffixes still realistic. For a domain that
#: wants names as keys, which no register gives you.
UNIQUE = NameProfile(stem_reuse=0.0, max_per_stem=1, source="not a register")

#: A legal form at the end of a name. Deliberately the same expression as
#: ``TRAILING_FORM`` in ``benchmarks/realism/corporate.py``, and
#: ``tests/test_names.py`` fails if the two stop agreeing, because the
#: measured reuse rate and the generated one have to be one statistic.
#:
#: ``GROUP`` is not in it. It is not a legal form, it is the most common word
#: in a register's names, and stripping it would take a word out of the middle
#: of the names this module builds on purpose.
_SUFFIX = re.compile(
    r"[\s,]+(L\.?L\.?C\.?|INC\.?|INCORPORATED|CORP\.?|CORPORATION|LTD\.?|LIMITED"
    r"|PLLC|LLP|L\.?L\.?P\.?|LP|L\.?P\.?|PC|PA|CO\.?|COMPANY|FOUNDATION"
    r"|ASSOCIATION|TRUST|PLC)$",
    re.IGNORECASE,
)


def stem(name: str) -> str:
    """A name with its legal form and punctuation taken off.

    The thing two records have in common when they are the same company
    written differently, and what the reference measures reuse on.
    """
    bare = _SUFFIX.sub("", str(name).strip().upper())
    bare = re.sub(r"[^A-Z0-9 ]", " ", bare)
    return " ".join(t for t in bare.split() if t not in {"AND", "THE", "OF", "A"})


def _choose(rng: np.random.Generator, options: tuple[tuple[object, float], ...], size: int):
    values = [value for value, _ in options]
    weights = np.array([weight for _, weight in options], dtype=np.float64)
    weights /= weights.sum()
    return rng.choice(len(values), size=size, p=weights), values


def suffixes(rng: np.random.Generator, rows: int, profile: NameProfile = REGISTER) -> list[str]:
    """``rows`` legal forms, as written, in the measured proportions."""
    picks, values = _choose(rng, profile.spellings, rows)
    return [values[i] for i in picks.tolist()]


def _stems(streams: Streams, count: int, profile: NameProfile) -> list[str]:
    """``count`` stems, built rather than hoped for.

    Faker's vocabulary is too small to give a quarter of a million distinct
    company names on its own, so a stem that is already taken grows a word
    instead of being redrawn forever.

    One-word stems are where that shows: a register has 10.3% of them and
    this produces 1.9%, because a thousand surnames cannot be 20,000 distinct
    one-word names and the rest grow a second word. Letting them repeat
    instead was tried and was worse: it put name reuse anywhere between 0.55%
    and 2.17% depending on the scale, and a rate that moves with the row
    count is not a rate.
    """
    rng = streams.rng
    pool = max(count * 2, 64)
    surnames = streams.fast.draw("last_name", {}, pool, rng)
    # Places, not arbitrary English: a register is full of surnames, towns and
    # trade words, and Faker's ``word`` provider produces "MEDINA IF PARTNERS".
    # The distinctive token of a place, because "New Lisafurt" as one
    # component makes a two-component stem four words long and the measured
    # stem is two or three.
    words = [w.upper().split()[-1] for w in streams.fast.draw("city", {}, pool, rng)]
    lengths, sizes = _choose(rng, profile.stem_words, count)
    picks = rng.integers(0, pool, size=(count, 6))

    seen: set[str] = set()
    out: list[str] = []
    extra = 0
    for row in range(count):
        size = int(sizes[int(lengths[row])])
        parts = [str(surnames[int(picks[row, 0])]).upper().split()[-1]]
        for slot in range(1, size):
            source = words if slot % 2 else surnames
            parts.append(str(source[int(picks[row, slot])]).upper().split()[-1])
        if size > 2:
            parts[-1] = INDUSTRY_WORDS[int(picks[row, 5]) % len(INDUSTRY_WORDS)]
        candidate = " ".join(parts)
        while candidate in seen:
            # Another word rather than another throw of the dice: at 200,000
            # companies a thousand surnames collide no matter how often you
            # redraw them.
            candidate = f"{candidate} {INDUSTRY_WORDS[extra % len(INDUSTRY_WORDS)]}"
            extra += 1
            if candidate not in seen:
                break
            candidate = f"{' '.join(parts)} {words[extra % pool]}"
            extra += 1
        seen.add(candidate)
        out.append(candidate)
    return out


def _multiplicities(rng: np.random.Generator, distinct: int, rows: int, profile: NameProfile) -> np.ndarray:
    """How many companies sit on each stem: mostly one, a measured few more."""
    counts = np.ones(distinct, dtype=np.int64)
    spare = rows - distinct
    if spare <= 0 or profile.stem_reuse <= 0:
        return counts
    shared = max(1, min(distinct, round(profile.stem_reuse * distinct)))
    chosen = rng.choice(distinct, size=shared, replace=False)
    # Hand out the spare rows over the chosen stems, a little unevenly and
    # never past the measured maximum.
    order = chosen[np.argsort(rng.random(shared))]
    at = 0
    while spare > 0:
        target = order[at % shared]
        if counts[target] < profile.max_per_stem:
            counts[target] += 1
            spare -= 1
        at += 1
        if at > shared * profile.max_per_stem:
            break
    return counts


def company_names(streams: Streams, rows: int, profile: NameProfile = REGISTER) -> list[str]:
    """``rows`` company names: measured suffixes, measured reuse.

    Two companies on one stem get their suffixes drawn separately, because
    ``ACME HOLDINGS LLC`` and ``ACME HOLDINGS INC`` are how a register says
    two different companies with the same idea for a name.
    """
    if rows <= 0:
        return []
    rng = streams.rng
    distinct = min(rows, max(1, round(rows / (1.0 + 1.2 * profile.stem_reuse))))
    stems = _stems(streams, distinct, profile)
    counts = _multiplicities(rng, distinct, rows, profile)
    expanded = [stems[i] for i, n in enumerate(counts.tolist()) for _ in range(n)][:rows]
    while len(expanded) < rows:  # pragma: no cover - only if the cap bites
        expanded.append(stems[len(expanded) % distinct])
    order = rng.permutation(len(expanded))
    forms = suffixes(rng, rows, profile)
    commas = rng.random(rows) < profile.comma
    return [
        expanded[int(order[i])]
        if not forms[i]
        else f"{expanded[int(order[i])]}{',' if commas[i] else ''} {forms[i]}"
        for i in range(rows)
    ]


def variants(name: str, rng: np.random.Generator, count: int = 2) -> list[str]:
    """The same company, written the other ways a register writes it.

    A resolver's actual problem: one entity across several records, where the
    legal form is spelled differently or punctuated differently or left off.
    The alternatives come from :data:`FORM_SPELLINGS`, so they are spellings
    the register really contains rather than corruptions invented here.
    """
    base = stem(name)
    written = str(name).strip().upper()
    tail = written[len(base):].strip(" ,") if written.startswith(base) else ""
    form = next(
        (f for f, spellings in FORM_SPELLINGS.items() if tail in {s for s, _ in spellings}),
        None,
    )
    options: list[str] = []
    if form:
        for spelling, _ in FORM_SPELLINGS[form]:
            options += [f"{base}, {spelling}", f"{base} {spelling}"]
    options.append(base)
    options = [o for o in dict.fromkeys(options) if o != written]
    if not options:
        return []
    picks = rng.permutation(len(options))[:count]
    return [options[int(i)] for i in picks]


def collision_stats(names: list[str]) -> dict[str, float]:
    """What the reference reports about names, for a generated set of them."""
    if not names:
        return {}
    exact = collections.Counter(str(n).strip().upper() for n in names)
    stems = collections.Counter(stem(n) for n in names)
    forms = collections.Counter()
    for name in names:
        written = str(name).strip().upper()
        match = _SUFFIX.search(written)
        forms[match.group(1).upper() if match else "(none)"] += 1
    return {
        "names": len(names),
        "distinct": len(exact),
        "repeated_exact_share": round(
            sum(1 for v in exact.values() if v > 1) / len(exact), 4
        ),
        "distinct_stems": len(stems),
        "repeated_stem_share": round(
            sum(1 for v in stems.values() if v > 1) / len(stems), 4
        ),
        "max_per_stem": max(stems.values()),
        "form_mix": {k: round(v / len(names), 4) for k, v in forms.most_common(8)},
    }
