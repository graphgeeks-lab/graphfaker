"""Using a language model to compile intent, never to generate data.

That sentence is the whole architecture and it is worth being precise about,
because the obvious thing to build is the wrong one. The obvious thing is to
ask a model for ten thousand transactions. What you get is plausible rows
with no process behind them: no degree distribution, no repeat partners, no
seasonality, no ground truth, and no way to make the same dataset twice. The
engine already knows how to produce all of that. What it cannot do is work
out what you meant by "a hospital with patients, doctors and admissions".

So the model's job is to turn a sentence into a **declaration** the engine
runs, and nothing else. It writes a schema; the engine writes the data.

**The reproducibility boundary.** A model never runs inside ``generate``.
It runs in ``graphfaker schema draft``, which writes a YAML file you read,
edit and commit, and the dataset is then a pure function of that file, the
seed and the shard size, exactly as it was before any of this existed. Two
runs of the drafter will not agree, because no sampling temperature makes a
model deterministic; two runs of the generator on the drafted file always
will. Everything built on this module has to keep that line: anything a
model produces becomes a file on disk that a human has seen, and generation
reads the file.

**Everything is validated before it is used.** A draft that does not parse
as a :class:`~graphfaker.schema.graph.GraphSchema` is an error, not a
dataset. The pydantic errors go back to the model once, because the failures
are usually mechanical (a sampler kind that does not exist, an edge pointing
at a node type that was renamed) and one repair round fixes most of them.
After that it gives up and says what was wrong, which is more useful than a
third attempt.

**Providers are deliberately thin.** One method, one call, text in and text
out: no streaming, no tools, no agent loop. A provider is anything with a
``complete`` method, so bringing your own is passing an object rather than
subclassing anything. The two that ship are an Anthropic client and an
OpenAI-compatible one, which between them cover the hosted APIs and
everything that speaks the same protocol locally (Ollama, vLLM, LM Studio).

Install with ``pip install graphfaker[llm]``. Nothing in the package imports
this unless you ask for it, and nothing in a generated dataset depends on it.
"""

from __future__ import annotations

from graphfaker.llm.providers import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    RecordedProvider,
    get_provider,
)

__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderError",
    "RecordedProvider",
    "get_provider",
]
