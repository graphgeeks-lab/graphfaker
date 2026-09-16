"""Vectorised drawing from Faker's own tables.

Faker is the right source of names, addresses and emails: its locale tables
are large, weighted and maintained. It is also about a millisecond per call,
because every call re-parses a format string and re-normalises a weight
table, and a bank at ``scale=1.0`` has seven million customers with six such
columns each. This module draws the same values from the same tables, a
column at a time, with numpy.

It works the way Faker's providers do underneath: a provider method picks a
format (``"{{first_name_male}} {{last_name}}"``), resolves each token from an
element table (``first_names_male``, weighted) or from another format list
(``street_name_formats``), and fills digit placeholders (``#``, ``%``, ``$``,
``!``, ``@``) and letter placeholders (``?``). :class:`FastFaker` reads those
tables off the Faker instance it is given, so the vocabulary and the
weights are Faker's, and the locale is whatever the instance was built with.
Providers that do more than that (``iban`` with its checksum, ``catch_phrase``
with three word lists) are reported as unsupported and drawn one row at a time
by the engine as before.

The random source is the shard's numpy ``Generator``, so a run is
reproducible from its seed and shard size like everything else. The values
are not the ones Faker would have produced call by call; datasets generated
before this module existed differ in their names, not in their structure.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import OrderedDict
from typing import Any

import numpy as np
from faker.decode import unidecode

_TOKEN = re.compile(r"\{\{(\w+)\}\}")
_DIGIT_RULES = {"#": (0, 10, False), "%": (1, 10, False), "$": (2, 10, False), "!": (0, 10, True), "@": (1, 10, True)}
_LETTERS = np.array(list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"))
_DIGITS = np.array(list("0123456789"))
_PLACEHOLDERS = "#%$!@?"

#: Providers with no ``{{token}}`` formats, handled directly.
_DIRECT = frozenset({"date_of_birth", "date", "uuid4", "latitude", "longitude", "words", "word", "country_code", "email", "user_name", "job"})

#: Provider kwargs the vectorised path understands.
_KWARGS = {"date_of_birth": {"minimum_age", "maximum_age"}, "words": {"nb"}, "email": {"safe"}, "country_code": {"representation"}}


class Unsupported(Exception):
    """The provider (or its arguments) has no vectorised form."""


class FastFaker:
    def __init__(self, fake: Any):
        self.fake = fake
        self.providers = list(fake.factories[0].providers)
        self._elements: dict[str, tuple[list[str], np.ndarray | None]] = {}
        self._formats: dict[str, tuple[list[str], np.ndarray | None]] = {}

    # ------------------------------------------------------------ tables

    def _find(self, *names: str) -> Any:
        for provider in self.providers:
            for name in names:
                value = getattr(provider, name, None)
                if value is not None and not callable(value):
                    return value
        return None

    @staticmethod
    def _weighted(table: Any) -> tuple[list[str], np.ndarray | None]:
        if isinstance(table, (dict, OrderedDict)):
            values = list(table.keys())
            weights = np.asarray(list(table.values()), dtype=np.float64)
            return values, weights / weights.sum()
        return list(table), None

    def elements(self, token: str) -> tuple[list[str], np.ndarray | None] | None:
        """The element table a token draws from: ``first_name`` ->
        ``first_names``, ``prefix_male`` -> ``prefixes_male``, ``word`` ->
        ``word_list``, ``job`` -> ``jobs``."""
        if token in self._elements:
            return self._elements[token]
        candidates = [token + "s", token + "es", token + "_list"]
        for suffix in ("_male", "_female", "_nonbinary"):
            if token.endswith(suffix):
                stem = token[: -len(suffix)]
                candidates += [stem + "s" + suffix, stem + "es" + suffix]
        if token == "country_code":
            candidates = ["alpha_2_country_codes"]
        table = self._find(*candidates)
        if table is None:
            return None
        self._elements[token] = self._weighted(table)
        return self._elements[token]

    def formats(self, token: str) -> tuple[list[str], np.ndarray | None] | None:
        if token in self._formats:
            return self._formats[token]
        table = self._find(token + "_formats")
        if table is None:
            return None
        self._formats[token] = self._weighted(table)
        return self._formats[token]

    def _entry_formats(self, provider: str) -> tuple[list[str], np.ndarray | None] | None:
        """``name``, ``company`` and ``phone_number`` keep their formats in a
        plain ``formats`` attribute on their own provider."""
        module = {"name": "person", "company": "company", "phone_number": "phone_number"}.get(provider)
        if module is None:
            return self.formats(provider)
        for candidate in self.providers:
            if f".{module}" in type(candidate).__module__:
                return self._weighted(candidate.formats)
        return None

    # ----------------------------------------------------------- drawing

    def supports(self, provider: str, kwargs: dict[str, Any]) -> bool:
        if kwargs and not set(kwargs) <= _KWARGS.get(provider, set()):
            return False
        if provider in _DIRECT:
            return True
        return self._entry_formats(provider) is not None or self.elements(provider) is not None

    def draw(self, provider: str, kwargs: dict[str, Any], n: int, rng: np.random.Generator) -> list[Any]:
        """``n`` values of ``provider``; raises :class:`Unsupported`."""
        if not self.supports(provider, kwargs):
            raise Unsupported(provider)
        if provider == "email":
            return self._email(n, rng, safe=kwargs.get("safe", True))
        if provider == "user_name":
            return self._user_name(n, rng)
        if provider == "date_of_birth":
            return self._date_of_birth(n, rng, kwargs.get("minimum_age", 0), kwargs.get("maximum_age", 115))
        if provider == "date":
            return self._date(n, rng)
        if provider == "uuid4":
            return self._uuids(n, rng)
        if provider == "latitude":
            return [round(v, 6) for v in rng.uniform(-90, 90, n).tolist()]
        if provider == "longitude":
            return [round(v, 6) for v in rng.uniform(-180, 180, n).tolist()]
        if provider == "words":
            nb = int(kwargs.get("nb", 3))
            words = self._choice("word", n * nb, rng)
            return [words[i * nb : (i + 1) * nb] for i in range(n)]
        if provider in ("word", "job", "country_code"):
            return self._choice(provider, n, rng)
        formats = self._entry_formats(provider)
        if formats is not None:
            return self._render(formats, n, rng)
        return self._choice(provider, n, rng)

    def _uuids(self, n: int, rng: np.random.Generator) -> list[str]:
        raw = rng.bytes(16 * n)
        return [str(uuid.UUID(bytes=raw[i * 16 : (i + 1) * 16], version=4)) for i in range(n)]

    def _choice(self, token: str, n: int, rng: np.random.Generator) -> list[str]:
        table = self.elements(token)
        if table is None:
            raise Unsupported(token)
        values, weights = table
        index = rng.choice(len(values), size=n, p=weights)
        return [values[i] for i in index.tolist()]

    def _token(self, token: str, n: int, rng: np.random.Generator) -> list[str]:
        """``n`` renderings of one ``{{token}}``."""
        if token == "user_name":
            return self._user_name(n, rng)
        if token in ("safe_domain_name", "free_email_domain"):
            return self._choice(token, n, rng)
        formats = self.formats(token)
        if formats is not None:
            return self._render(formats, n, rng)
        return self._choice(token, n, rng)

    def _render(self, formats: tuple[list[str], np.ndarray | None], n: int, rng: np.random.Generator) -> list[str]:
        """Pick a format per row, resolve its tokens for all rows that share
        it, fill placeholders, and put the rows back in order."""
        patterns, weights = formats
        chosen = rng.choice(len(patterns), size=n, p=weights)
        out: list[str | None] = [None] * n
        for which in np.unique(chosen).tolist():
            rows = np.flatnonzero(chosen == which)
            pattern = patterns[which]
            tokens = _TOKEN.findall(pattern)
            pieces = _TOKEN.split(pattern)  # literal, token, literal, token, ..., literal
            columns = [self._token(token, len(rows), rng) for token in tokens]
            literals = pieces[0::2]
            if columns:
                # placeholders live in the literal parts, never in a name,
                # so fill them there, where every row shares the template
                filled = [self._fill([literal] * len(rows), rng) if any(c in literal for c in _PLACEHOLDERS) else None for literal in literals]
                rendered = []
                for row in range(len(rows)):
                    parts = [filled[0][row] if filled[0] else literals[0]]
                    for k, column in enumerate(columns):
                        parts.append(column[row])
                        parts.append(filled[k + 1][row] if filled[k + 1] else literals[k + 1])
                    rendered.append("".join(parts))
            else:
                rendered = self._fill([pattern] * len(rows), rng)
            for row, value in zip(rows.tolist(), rendered):
                out[row] = value
        return out  # type: ignore[return-value]

    @staticmethod
    def _fill(strings: list[str], rng: np.random.Generator) -> list[str]:
        """``numerify`` and ``lexify`` for a batch that shares one template:
        the placeholders sit at the same positions in every string, so each
        position is one column of random digits or letters."""
        template = strings[0]
        slots = [(j, c) for j, c in enumerate(template) if c in _DIGIT_RULES or c == "?"]
        if not slots:
            return strings
        n = len(strings)
        columns: list[np.ndarray] = []
        for _, c in slots:
            if c == "?":
                columns.append(_LETTERS[rng.integers(0, len(_LETTERS), size=n)])
                continue
            low, high, may_be_empty = _DIGIT_RULES[c]
            digits = _DIGITS[rng.integers(low, high, size=n)]
            if may_be_empty:
                digits = np.where(rng.random(n) < 0.5, "", digits)
            columns.append(digits)
        pieces = []
        last = 0
        for j, _ in slots:
            pieces.append(template[last:j])
            last = j + 1
        pieces.append(template[last:])
        out = []
        for values in zip(*(column.tolist() for column in columns)):
            parts = [pieces[0]]
            for k, value in enumerate(values):
                parts.append(value)
                parts.append(pieces[k + 1])
            out.append("".join(parts))
        return out

    def _user_name(self, n: int, rng: np.random.Generator) -> list[str]:
        formats = self.formats("user_name")
        if formats is None:
            raise Unsupported("user_name")
        return [unidecode(v).lower() for v in self._render(formats, n, rng)]

    def _email(self, n: int, rng: np.random.Generator, safe: bool = True) -> list[str]:
        users = self._user_name(n, rng)
        if safe:
            domains = self._choice("safe_domain_name", n, rng)
        else:
            formats = self.formats("email")
            if formats is None:
                raise Unsupported("email")
            return [v.replace(" ", "") for v in self._render(formats, n, rng)]
        return [f"{u}@{d}" for u, d in zip(users, domains)]

    @staticmethod
    def _date_of_birth(n: int, rng: np.random.Generator, minimum_age: int, maximum_age: int) -> list[dt.date]:
        today = dt.datetime.now(tz=dt.timezone.utc).date()
        start = _change_year(today, -(maximum_age + 1)) + dt.timedelta(days=1)
        end = _change_year(today, -minimum_age)
        span = (end - start).days
        offsets = rng.integers(0, span + 1, size=n)
        return [start + dt.timedelta(days=int(d)) for d in offsets.tolist()]

    @staticmethod
    def _date(n: int, rng: np.random.Generator) -> list[str]:
        start = dt.date(1970, 1, 1)
        span = (dt.datetime.now(tz=dt.timezone.utc).date() - start).days
        offsets = rng.integers(0, span + 1, size=n)
        return [(start + dt.timedelta(days=int(d))).isoformat() for d in offsets.tolist()]


def _change_year(date: dt.date, delta: int) -> dt.date:
    year = date.year + delta
    try:
        return date.replace(year=year)
    except ValueError:  # 29 February
        return date.replace(year=year, day=28)
