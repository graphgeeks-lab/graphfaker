"""Officers and contacts, attached to companies the way a register attaches them.

Three measured facts, and the first is the one a generator usually gets wrong
by not thinking about it at all.

**Most companies have nobody.** 4.5% of the companies in a register have a
single person attached to them: 46,201 of Nevada's 1,033,773 and 27,727 of
the Las Vegas file's 631,846. Board density is not a number, it is a
distribution with a floor of zero, and a generator that gives every company
an officer gets the common case wrong.

**The ones that do have a person have a crowd.** Among companies with
anybody at all the median is 1, the 90th percentile 14, the 99th 151, and the
busiest holds 68,945, which is 10% of every person-company link in the
register on its own. The top 1% of them account for 61%. Those are filing
agents and large employers, and they are why "shares a director" is not
evidence by itself.

**A person is a record per company, not a person.** A register has no person
entity: it has one row per person-company association, so somebody on three
boards appears three times, written slightly differently each time. That is
the resolution problem, and it is why 1.17 organisations share the average
person's name, with a 99th percentile of 4 and a maximum of 84.

What this module produces is entities and attachments: people, and which
companies they are attached to with which role. Turning one attachment into
one register record is :mod:`graphfaker.sinks.senzing`'s job, because that is
a property of how a register is written rather than of the world.

Roles are the register's own: ``Contact`` 76% to 79% of links and
``Executive`` 21% to 24%, measured in both files.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from graphfaker.engine.seeding import Streams

#: Share of person records carrying ``NAME_FIRST`` and ``NAME_LAST`` as well
#: as a full name. In a register most rows have only the full name, which
#: matters because a resolver with the parts has an easier job than one
#: without and real data mostly withholds them.
PARTS_SHARE = 0.233


@dataclass(frozen=True)
class PeopleProfile:
    """How a register attaches people to companies.

    How many people a company has is a rank curve fitted to the measured
    mean and concentration; how many companies a person has is the measured
    quantiles, interpolated. Each shape got the treatment that reproduced it:
    see the comments on the fields.
    """

    #: Companies with anybody attached at all.
    attached_share: float = 0.0447
    #: People per attached company, averaged over the register.
    mean_per_company: float = 14.5
    #: Exponent of the rank curve that spreads them. A rank curve rather than
    #: a list of quantiles, for the same reason addresses use one: it hits the
    #: mean exactly at every scale and keeps the concentration, where
    #: interpolating between a 99th percentile of 151 and a maximum of 68,945
    #: puts so much weight on the head that the mean goes with it. At 1.03 and
    #: the register's 46,201 attached companies the busiest holds 10.2% of
    #: every link against a measured 10.3%, and the top 1% hold 63% against
    #: 61%.
    alpha: float = 1.03
    #: Companies per person, as ``(quantile, value)``: 89% of people appear
    #: once. Fitted the same way, so the maximum is 8 against a measured 84
    #: over 561,166 people, and the mean, median, 90th, 99th and top 1% share
    #: all land.
    per_person: tuple[tuple[float, float], ...] = (
        (0.0, 1), (0.89, 1), (0.9, 2), (0.99, 4), (1.0, 8),
    )
    #: Share of links that are an ``Executive`` rather than a ``Contact``.
    executive_share: float = 0.213
    #: Share of people whose first and last name are recorded separately.
    parts_share: float = PARTS_SHARE
    source: str = (
        "Nevada (1,033,773 companies) and Las Vegas (631,846), "
        "benchmarks/realism/corporate-*.json"
    )


#: The measured profile.
REGISTER = PeopleProfile()

#: Every company with a board, for a domain that wants one. Rarely true of
#: anything real, so a domain using it should be saying something.
EVERY_COMPANY = PeopleProfile(
    attached_share=1.0,
    mean_per_company=3.0,
    alpha=0.4,
    source="assumed, not measured",
)


def allocate(attached: int, profile: PeopleProfile = REGISTER) -> np.ndarray:
    """How many people sit at each attached company, largest first.

    Deterministic: the shape is the same at every scale and the randomness is
    in which company lands where, which is :func:`attach`'s job. The counts
    sum to ``mean_per_company * attached`` exactly.
    """
    if attached <= 0:
        return np.empty(0, dtype=np.int64)
    weights = np.power(np.arange(1, attached + 1, dtype=np.float64), -profile.alpha)
    weights /= weights.sum()
    total = profile.mean_per_company * attached
    exact = total * weights
    counts = np.floor(exact).astype(np.int64)
    short = round(total) - int(counts.sum())
    if short > 0:
        # Largest remainder, so the links add up instead of drifting.
        counts[np.argsort(-(exact - counts))[:short]] += 1
    return counts[counts > 0]


def _draw(rng: np.random.Generator, size: int, curve: tuple[tuple[float, float], ...]) -> np.ndarray:
    """``size`` values from a curve of measured quantiles.

    Interpolated in log space between the given points, because the gap from
    the 99th percentile to the maximum spans three orders of magnitude and a
    straight line through it would put far too much weight on the head.
    """
    quantiles = np.array([q for q, _ in curve], dtype=np.float64)
    values = np.log(np.array([v for _, v in curve], dtype=np.float64))
    drawn = np.interp(rng.random(size), quantiles, values)
    return np.maximum(1, np.round(np.exp(drawn))).astype(np.int64)


def attach(
    streams: Streams,
    company_ids: list[str],
    profile: PeopleProfile = REGISTER,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """People, and which companies they are attached to.

    Returns ``(people, links)``. A person has an ``id``, a ``name``, and
    ``first``/``last``/``middle`` where the register would have recorded them
    separately. A link has ``person``, ``company`` and ``role``.

    The company order is not used: which companies get a board is drawn, so a
    caller can pass its node ids in any order.
    """
    rng = streams.rng
    if not company_ids:
        return [], []

    attached_count = round(profile.attached_share * len(company_ids))
    if attached_count <= 0:
        # Too few companies for the measured share to reach one. Attaching
        # nobody is the honest answer and the common case in the register.
        return [], []
    per_company = allocate(attached_count, profile)
    chosen = rng.choice(len(company_ids), size=len(per_company), replace=False)
    # Which company is the busy one is random; how busy the busiest is, is not.
    rng.shuffle(per_company)
    total_links = int(per_company.sum())

    # How many distinct people those links need: the per-person curve says
    # how many companies one person is attached to.
    spread = _draw(rng, max(1, total_links), profile.per_person)
    people_needed = 0
    covered = 0
    while covered < total_links:
        covered += int(spread[people_needed % len(spread)])
        people_needed += 1

    first_names = streams.fast.draw("first_name", {}, people_needed, rng)
    last_names = streams.fast.draw("last_name", {}, people_needed, rng)
    middles = streams.fast.draw("first_name", {}, people_needed, rng)
    has_parts = rng.random(people_needed) < profile.parts_share
    has_middle = rng.random(people_needed) < 0.065  # NAME_MIDDLE on 1.5% of records

    people: list[dict[str, object]] = []
    seats: list[str] = []
    for n in range(people_needed):
        person_id = f"per_{n}"
        first, last = str(first_names[n]).upper(), str(last_names[n]).upper()
        middle = str(middles[n]).upper() if has_middle[n] else None
        people.append({
            "id": person_id,
            "name": " ".join(part for part in (first, middle, last) if part),
            "first": first if has_parts[n] else None,
            "middle": middle if has_parts[n] else None,
            "last": last if has_parts[n] else None,
        })
        seats += [person_id] * int(spread[n % len(spread)])

    seats = seats[:total_links]
    rng.shuffle(seats)
    executive = rng.random(total_links) < profile.executive_share

    links: list[dict[str, object]] = []
    at = 0
    for index, count in zip(chosen.tolist(), per_company.tolist(), strict=True):
        company = company_ids[index]
        for _ in range(count):
            if at >= len(seats):
                break
            links.append({
                "person": seats[at],
                "company": company,
                "role": "Executive" if executive[at] else "Contact",
            })
            at += 1

    # A person nobody ended up attached to is not in the register at all.
    seated = {link["person"] for link in links}
    people = [person for person in people if person["id"] in seated]
    return people, links


def person_variants(person: dict[str, object], rng: np.random.Generator, count: int = 1) -> list[str]:
    """One person's name, written the other ways a register writes it.

    Conventions rather than corruption: the surname first, a middle initial
    instead of a middle name, a first initial. A register holds all of these
    because its sources do. There are no typos here, deliberately; synthetic
    error is far easier than real error and a benchmark built on it measures
    its own noise model.
    """
    name = str(person.get("name") or "")
    parts = name.split()
    if len(parts) < 2:
        return []
    first, last = parts[0], parts[-1]
    middle = parts[1] if len(parts) > 2 else None

    options = [f"{last}, {first}", f"{first[0]} {last}", f"{first} {last}"]
    if middle:
        options += [f"{first} {middle[0]} {last}", f"{first} {middle[0]}. {last}"]
    options = [option for option in dict.fromkeys(options) if option != name]
    if not options:
        return []
    picks = rng.permutation(len(options))[:count]
    return [options[int(i)] for i in picks]
