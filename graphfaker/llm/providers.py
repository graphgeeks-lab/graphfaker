"""Somewhere to send a prompt, in as few lines as the job needs.

One method. The task is "turn this description into a schema", which is one
request and one response, so there is no tool loop, no conversation state
and no retry policy beyond what the vendor's own client already does. A
provider is anything with :meth:`Provider.complete`, which means bringing
your own is passing an object rather than inheriting from one.

What is here instead of simplicity is one thing: the request shape adapts
to what the far end will take. Both APIs have drifted and neither
announces which way, so picking a shape from the model's name means a
table of model names that is wrong the week after it is written. Send the
current shape, read the rejection, drop what it named, ask again.

The two that ship cover what people actually have: Anthropic's API, and
anything speaking the OpenAI chat-completions protocol, which is the hosted
OpenAI API and also Ollama, vLLM, LM Studio, Together, Groq and most of the
gateways. A third, :class:`RecordedProvider`, replays canned text and is how
the tests run without a network or a key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Anthropic's most capable model, which is the right default for a task
#: that runs once, is read by a human before it is used, and whose output
#: quality decides how much hand-editing the schema needs.
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"

#: Environment variables, so a key never has to appear on a command line
#: and end up in a shell history.
PROVIDER_VAR = "GRAPHFAKER_LLM_PROVIDER"
MODEL_VAR = "GRAPHFAKER_LLM_MODEL"
BASE_URL_VAR = "GRAPHFAKER_LLM_BASE_URL"


def _require_key(variable: str, given: str | None) -> str:
    """The API key, or a message saying exactly where to put one.

    The key is usually already in a ``.env`` next to the code, which the
    CLI reads on the way in, so the useful error names the variable and the
    file that was looked at rather than leaving someone to guess which of
    the two is wrong.
    """
    import os

    key = given or os.environ.get(variable)
    if key:
        return key
    from graphfaker.env import find

    found = find()
    where = f"{found} has no {variable}" if found else "no .env was found"
    raise ProviderError(
        f"no API key: set {variable} in the environment or in a .env next to your project ({where})"
    )


class ProviderError(RuntimeError):
    """The model could not be reached, or refused the request.

    Separate from a validation failure on what it returned: one of them
    means try again later and the other means the draft was wrong.
    """


@runtime_checkable
class Provider(Protocol):
    """Anything that can answer one prompt with one string."""

    #: For error messages and for saying what drafted a file.
    name: str
    model: str

    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        """The model's reply as text, with no wrapper of any kind.

        No temperature. The current Anthropic models removed the sampling
        controls outright and replaced them with an effort level, so a
        temperature is not a parameter every provider has; the one that
        still takes it sets its own.
        """
        ...


@dataclass
class AnthropicProvider:
    """Anthropic's Messages API through the official client.

    No temperature. Current models removed the sampling controls
    (``temperature``, ``top_p``, ``top_k``) and replaced them with
    ``output_config.effort``, so asking for a temperature is not a
    conservative default, it is a request the model will not take: the
    client raises before anything reaches the network.

    ``effort`` is how deeply it works instead, and ``high`` is right for
    this job. The default on the current model is ``medium``, and a schema
    draft runs once, is read by a human, and decides how much hand-editing
    the file needs, so the extra thinking is worth paying for. Pass a
    different one to the constructor for a cheaper draft.
    """

    model: str = DEFAULT_ANTHROPIC_MODEL
    api_key: str | None = None
    #: low | medium | high | xhigh | max.
    effort: str = "high"
    name: str = "anthropic"

    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised by hand
            raise ProviderError(
                "the anthropic client is not installed; pip install 'graphfaker[llm]'"
            ) from exc
        client = anthropic.Anthropic(api_key=_require_key("ANTHROPIC_API_KEY", self.api_key))

        request: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # Adaptive thinking, said explicitly rather than left to the
            # default, because the default differs across the family: on the
            # current models omitting it thinks anyway, on some older ones it
            # does not. A schema is worth thinking about either way.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

        def send(**kwargs: object):
            # Streamed, because adaptive thinking makes a request take long
            # enough that a non-streaming call can hit the client's own
            # timeout before the model has finished.
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()

        message = _send(send, request, _ANTHROPIC_ADAPTATIONS, f"anthropic ({self.model})")
        return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


@dataclass
class OpenAICompatibleProvider:
    """The chat-completions protocol, wherever it is being served.

    ``base_url`` is what makes this more than an OpenAI client: point it at
    a local Ollama or vLLM and the rest works unchanged. There is no default
    model, on purpose, because the endpoint decides what it serves and
    guessing produces a worse error than asking.

    "OpenAI-compatible" is a family rather than a protocol, so the request
    adapts to what the endpoint will take. See :meth:`complete`.
    """

    model: str
    base_url: str | None = None
    api_key: str | None = None
    name: str = "openai"

    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        """One request, retried without whatever the endpoint rejected.

        The protocol has drifted: ``gpt-5`` and the reasoning models renamed
        ``max_tokens`` to ``max_completion_tokens`` and accept only the
        default temperature, while an older local server knows ``max_tokens``
        and nothing else. See :func:`_send`.

        A near-zero temperature is asked for and given up without complaint.
        It does not make a draft reproducible, and nothing here depends on
        that; it only leans the model towards the common shape of a schema
        rather than an inventive one.
        """
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - exercised by hand
            raise ProviderError(
                "the openai client is not installed; pip install 'graphfaker[llm]'"
            ) from exc
        # A local server usually wants no key and the client insists on one.
        key = (
            self.api_key or os.environ.get("OPENAI_API_KEY") or "none"
            if self.base_url
            else _require_key("OPENAI_API_KEY", self.api_key)
        )
        client = openai.OpenAI(api_key=key, base_url=self.base_url)

        request: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        where = self.base_url or "api.openai.com"
        reply = _send(
            client.chat.completions.create, request, _OPENAI_ADAPTATIONS, f"openai ({self.model} at {where})"
        )
        return reply.choices[0].message.content or ""


#: Parameter the endpoint refused, and what to send instead. ``None`` drops
#: it. Both families have drifted and neither announces which way: ``gpt-5``
#: renamed ``max_tokens`` and fixed the temperature, while an older local
#: server knows only the old name.
_OPENAI_ADAPTATIONS: dict[str, str | None] = {
    "max_tokens": "max_completion_tokens",
    "max_completion_tokens": "max_tokens",
    "temperature": None,
}

#: The same for Anthropic, where the modern request shape is sent first and
#: dropped a field at a time for an older model or an older installed
#: client: ``thinking`` and ``output_config`` do not exist before a certain
#: generation, and a client old enough not to know the keyword raises a
#: ``TypeError`` rather than returning a 400.
_ANTHROPIC_ADAPTATIONS: dict[str, str | None] = {
    "output_config": None,
    "thinking": None,
    "temperature": None,
}


def _send(call, request: dict[str, object], adaptations: dict[str, str | None], label: str):
    """Make the request, dropping whatever the far end says it will not take.

    Picking the request shape by looking at the model name means a table of
    model names that is wrong the week after it is written, and sending the
    union of every shape fails everywhere at once. So send the current one
    and let the rejection say what to remove. At most one retry per
    parameter; anything that is not a complaint about a parameter is raised
    as it arrived.
    """
    for _ in range(len(adaptations) + 1):
        try:
            return call(**request)
        except Exception as exc:
            if _adapt(request, str(exc), adaptations) is None:
                raise ProviderError(f"{label}: {exc}") from exc
    raise ProviderError(f"{label}: every form of the request was rejected")


def _adapt(request: dict[str, object], message: str, adaptations: dict[str, str | None]) -> str | None:
    """Rename or drop the parameter the far end named, or give up.

    Matching on the message is unlovely and is the only thing available:
    the failure is either a 400 whose body names the parameter or a
    ``TypeError`` from a client that has removed the keyword, and both put
    the name in the text.
    """
    lowered = message.lower()
    complaints = ("unsupported", "unrecognized", "not supported", "unexpected keyword", "removed")
    if not any(word in lowered for word in complaints):
        return None
    for parameter, replacement in adaptations.items():
        if parameter not in request or parameter not in lowered:
            continue
        value = request.pop(parameter)
        if replacement is not None:
            request[replacement] = value
        return parameter
    return None


@dataclass
class RecordedProvider:
    """Replays prepared replies, in order, and records what it was asked.

    How the tests run: a drafting round trip is a prompt, a reply and a
    validation, and only the middle one needs a model. Running out of
    replies raises rather than repeating the last one, so a test that
    triggers an unexpected second call fails loudly instead of passing for
    the wrong reason.
    """

    replies: list[str] = field(default_factory=list)
    name: str = "recorded"
    model: str = "recorded"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        del max_tokens
        self.calls.append((system, user))
        if len(self.calls) > len(self.replies):
            raise ProviderError(
                f"the recorded provider has {len(self.replies)} replies and was asked {len(self.calls)} times"
            )
        return self.replies[len(self.calls) - 1]


def get_provider(
    provider: str | None = None, model: str | None = None, base_url: str | None = None
) -> Provider:
    """Build a provider from the arguments, then the environment.

    ``GRAPHFAKER_LLM_PROVIDER`` picks between ``anthropic`` and ``openai``,
    ``GRAPHFAKER_LLM_MODEL`` names the model and
    ``GRAPHFAKER_LLM_BASE_URL`` points the OpenAI-compatible one somewhere
    else. With nothing set at all it is Anthropic on the default model,
    which fails with a clear message if no key is present rather than
    quietly choosing something cheaper.
    """
    chosen = (provider or os.environ.get(PROVIDER_VAR) or "anthropic").strip().lower()
    model = model or os.environ.get(MODEL_VAR) or None
    given_base_url = base_url
    base_url = base_url or os.environ.get(BASE_URL_VAR) or None

    if chosen == "anthropic":
        if given_base_url:
            # Passed on purpose, so say so. One left in the environment for a
            # local OpenAI-compatible server is not an error here: it belongs
            # to the other provider and this one ignores it.
            raise ProviderError("a base url applies to the openai provider; the anthropic one has its own")
        return AnthropicProvider(model=model or DEFAULT_ANTHROPIC_MODEL)
    if chosen in {"openai", "openai-compatible", "compatible"}:
        if not model:
            raise ProviderError(
                f"the openai provider needs a model name: pass --model or set {MODEL_VAR}. "
                "The endpoint decides what it serves, so there is no sensible default."
            )
        return OpenAICompatibleProvider(model=model, base_url=base_url)
    raise ProviderError(f"unknown provider {chosen!r}; it is 'anthropic' or 'openai'")
