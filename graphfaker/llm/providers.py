"""Somewhere to send a prompt, in as few lines as the job needs.

One method. The task is "turn this description into a schema", which is one
request and one response, so there is no streaming, no tool loop, no
conversation state and no retry policy beyond what the vendor's own client
already does. A provider is anything with :meth:`Provider.complete`, which
means bringing your own is passing an object rather than inheriting from
one.

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

    def complete(self, system: str, user: str, *, max_tokens: int = 8192, temperature: float = 0.0) -> str:
        """The model's reply as text, with no wrapper of any kind."""
        ...


@dataclass
class AnthropicProvider:
    """Anthropic's Messages API through the official client."""

    model: str = DEFAULT_ANTHROPIC_MODEL
    api_key: str | None = None
    name: str = "anthropic"

    def complete(self, system: str, user: str, *, max_tokens: int = 8192, temperature: float = 0.0) -> str:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised by hand
            raise ProviderError(
                "the anthropic client is not installed; pip install 'graphfaker[llm]'"
            ) from exc
        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise ProviderError(f"anthropic ({self.model}): {exc}") from exc
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

    def complete(self, system: str, user: str, *, max_tokens: int = 8192, temperature: float = 0.0) -> str:
        """One request, retried without whatever the endpoint rejected.

        The protocol has drifted. ``gpt-5`` and the reasoning models renamed
        ``max_tokens`` to ``max_completion_tokens`` and accept only the
        default temperature; an older local server knows ``max_tokens`` and
        nothing else. Sending the union fails on both, and picking one by
        looking at the model name means a table of model names that is wrong
        the week after it is written.

        So: send the usual request, and when the endpoint says which
        parameter it will not take, rename or drop that one and ask again.
        At most one retry per parameter, and anything that is not an
        unsupported-parameter complaint is raised as it arrived.
        """
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - exercised by hand
            raise ProviderError(
                "the openai client is not installed; pip install 'graphfaker[llm]'"
            ) from exc
        # A local server usually wants no key and the client insists on one.
        key = self.api_key or os.environ.get("OPENAI_API_KEY") or ("none" if self.base_url else None)
        client = openai.OpenAI(api_key=key, base_url=self.base_url)

        request: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        for _ in range(len(_ADAPTATIONS) + 1):
            try:
                reply = client.chat.completions.create(**request)
            except Exception as exc:
                adapted = _adapt(request, str(exc))
                if adapted is None:
                    where = self.base_url or "api.openai.com"
                    raise ProviderError(f"openai ({self.model} at {where}): {exc}") from exc
                continue
            return reply.choices[0].message.content or ""
        raise ProviderError(f"openai ({self.model}): the endpoint rejected every form of the request")


#: Parameter the endpoint refused, and what to send instead. ``None`` drops
#: it: a model that will not take a temperature has one it insists on.
_ADAPTATIONS: dict[str, str | None] = {
    "max_tokens": "max_completion_tokens",
    "max_completion_tokens": "max_tokens",
    "temperature": None,
}


def _adapt(request: dict[str, object], message: str) -> str | None:
    """Rename or drop the parameter the endpoint named, or give up.

    Matching on the message is unlovely and is the only thing available:
    the error is a 400 whose body names the parameter, and the alternative
    is a table of which models take which arguments.
    """
    lowered = message.lower()
    if "unsupported" not in lowered and "unrecognized" not in lowered and "not supported" not in lowered:
        return None
    for parameter, replacement in _ADAPTATIONS.items():
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

    def complete(self, system: str, user: str, *, max_tokens: int = 8192, temperature: float = 0.0) -> str:
        del max_tokens, temperature
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
