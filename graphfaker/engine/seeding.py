"""Reproducible randomness across shards.

One seed has to be enough to reproduce a run whatever the worker count, and
two shards must never share a stream. ``numpy.random.SeedSequence`` gives
both: children spawned from a root are statistically independent, and the
tree is a pure function of the root seed and the spawn path. Every consumer
(numpy, ``random``, Faker) is fed from the same child so a stage's draws stay
together.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
from faker import Faker


@dataclass
class Streams:
    """The random sources for one stage or shard."""

    sequence: np.random.SeedSequence
    rng: np.random.Generator
    rand: random.Random
    fake: Faker

    @classmethod
    def from_sequence(cls, sequence: np.random.SeedSequence) -> Streams:
        state = int(sequence.generate_state(1, dtype=np.uint64)[0])
        fake = Faker()
        fake.seed_instance(state)
        return cls(
            sequence=sequence,
            rng=np.random.default_rng(sequence),
            rand=random.Random(state),
            fake=fake,
        )

    @classmethod
    def root(cls, seed: int | None) -> Streams:
        return cls.from_sequence(np.random.SeedSequence(seed))

    def spawn(self, n: int) -> list[Streams]:
        """``n`` independent children. Calling twice yields different children,
        so spawn exactly what a stage needs, in a fixed order."""
        return [self.from_sequence(child) for child in self.sequence.spawn(n)]

    @property
    def entropy(self) -> int | None:
        entropy = self.sequence.entropy
        return int(entropy) if entropy is not None else None
