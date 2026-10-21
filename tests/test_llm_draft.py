"""Drafting a schema from a description.

No network and no key: every test here uses :class:`RecordedProvider`, which
replays prepared replies. That is not only a convenience. The thing worth
testing is not whether a model can write YAML, it is what happens to the YAML
afterwards: that it is validated before anybody sees it, that a broken draft
goes back once with the errors, that a second failure is an error and not a
dataset, and that nothing in the path from a drafted file to a generated
graph asks a model anything.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from graphfaker.cli import app
from graphfaker.llm import ProviderError, RecordedProvider, get_provider
from graphfaker.llm.draft import (
    DraftError,
    build_prompt,
    draft,
    extract_yaml,
    sampler_contract,
    worked_example,
)
from graphfaker.schema.graph import GraphSchema
from tests.helpers import said

runner = CliRunner()

GOOD = """
name: clinic
version: '1'
topology:
  kind: social
  group: ward
  prominence: null
latent:
  - name: ward
    groups: 4
    params:
      acuity:
        kind: gaussian
        mean: 0.5
        sd: 0.2
nodes:
  - name: Patient
    count: 2000
    attributes:
      name:
        kind: faker
        provider: name
      age:
        kind: gaussian
        mean: 52
        sd: 19
        low: 0
        high: 101
        integer: true
  - name: Doctor
    count: 80
    attributes:
      name:
        kind: faker
        provider: name
      specialty:
        kind: category
        values: [cardiology, oncology, general]
        weights: [0.3, 0.3, 0.4]
edges:
  - source: Doctor
    target: Patient
    share: 1.0
    relationships:
      - name: TREATS
total_edges: 6000
"""

#: A sampler kind that does not exist, which is the commonest way a draft
#: fails: the model reaches for something reasonable that the engine has no
#: word for.
BROKEN = GOOD.replace("kind: gaussian\n        mean: 52", "kind: normal\n        mean: 52")


def test_a_good_draft_validates_and_is_an_ordinary_schema():
    provider = RecordedProvider(replies=[GOOD])
    result = draft("a clinic: patients and doctors", provider)
    assert isinstance(result.schema, GraphSchema)
    assert result.schema.name == "clinic"
    assert result.schema.total_nodes == 2080
    assert not result.repaired and len(provider.calls) == 1
    # The yaml round-trips, which is what makes the file the deliverable.
    assert GraphSchema.from_yaml(result.schema.to_yaml()) == result.schema


def test_a_broken_draft_goes_back_once_with_the_errors():
    provider = RecordedProvider(replies=[BROKEN, GOOD])
    result = draft("a clinic", provider)
    assert result.repaired and len(provider.calls) == 2
    assert result.schema.name == "clinic"

    # The second prompt carries the model's own answer and pydantic's
    # complaint, because "it did not validate" is not actionable on its own.
    _, second = provider.calls[1]
    assert "kind: normal" in second
    assert "did not validate" in second
    assert "Patient" in second and "age" in second


def test_two_failures_is_an_error_and_not_a_dataset():
    provider = RecordedProvider(replies=[BROKEN, BROKEN])
    with pytest.raises(DraftError) as caught:
        draft("a clinic", provider)
    assert len(caught.value.attempts) == 2 and len(caught.value.errors) == 2
    # The attempts come back, because seeing what it was reaching for is
    # usually enough to fix it by hand.
    assert "kind: normal" in caught.value.attempts[-1]


def test_repair_can_be_turned_off():
    provider = RecordedProvider(replies=[BROKEN])
    with pytest.raises(DraftError):
        draft("a clinic", provider, repair=False)
    assert len(provider.calls) == 1


def test_a_fenced_reply_is_unwrapped():
    assert extract_yaml("here you go:\n```yaml\nname: a\n```\nhope that helps") == "name: a"
    assert extract_yaml("```\nname: a\n```") == "name: a"
    assert extract_yaml("  name: a  ") == "name: a"


def test_the_prompt_describes_the_samplers_the_engine_actually_has():
    """Generated from the models, so it cannot drift.

    A hand-written list of sampler kinds is wrong the first time one is
    added, and the failure is a draft that uses a word the engine does not
    know.
    """
    contract = sampler_contract()
    import typing

    from graphfaker.schema.samplers import Sampler

    for model in typing.get_args(typing.get_args(Sampler)[0]):
        kind = model.model_fields["kind"].default
        assert f"- {kind}:" in contract, kind

    system, user = build_prompt("a clinic", name="clinic")
    assert contract in system
    assert "clinic" in user
    # The worked example is a real schema rather than one written in the
    # prompt, for the same reason.
    assert worked_example() in system
    assert GraphSchema.from_yaml(worked_example()).name == "social"


def test_the_name_is_passed_through_when_asked():
    provider = RecordedProvider(replies=[GOOD])
    draft("a clinic", provider, name="my_clinic")
    assert "'my_clinic'" in provider.calls[0][1]


def test_running_out_of_replies_fails_loudly():
    """So a test that triggers an unexpected call cannot pass by accident."""
    provider = RecordedProvider(replies=[])
    with pytest.raises(ProviderError, match="0 replies"):
        provider.complete("s", "u")


# ---------------------------------------------------------------- providers


def test_the_provider_comes_from_the_arguments_then_the_environment(monkeypatch):
    monkeypatch.delenv("GRAPHFAKER_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GRAPHFAKER_LLM_MODEL", raising=False)
    monkeypatch.delenv("GRAPHFAKER_LLM_BASE_URL", raising=False)

    default = get_provider()
    assert default.name == "anthropic" and default.model.startswith("claude-")

    monkeypatch.setenv("GRAPHFAKER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("GRAPHFAKER_LLM_MODEL", "local-model")
    monkeypatch.setenv("GRAPHFAKER_LLM_BASE_URL", "http://localhost:11434/v1")
    local = get_provider()
    assert local.name == "openai" and local.base_url == "http://localhost:11434/v1"
    # An argument beats the environment.
    assert get_provider("anthropic", "claude-haiku-5-5").model == "claude-haiku-5-5"


def test_a_provider_that_cannot_be_built_says_why(monkeypatch):
    monkeypatch.delenv("GRAPHFAKER_LLM_MODEL", raising=False)
    with pytest.raises(ProviderError, match="needs a model name"):
        get_provider("openai")
    with pytest.raises(ProviderError, match="unknown provider"):
        get_provider("llama.cpp")
    with pytest.raises(ProviderError, match="applies to the openai provider"):
        get_provider("anthropic", base_url="http://localhost:11434/v1")


# ---------------------------------------------------------------------- cli


def test_the_cli_writes_a_file_that_generate_can_read(tmp_path, monkeypatch):
    """The whole point, end to end.

    A model writes a schema, it goes to a file, and the file generates a
    dataset like any other. Nothing in the second half knows a model was
    involved, which is the boundary the whole semantic layer rests on.
    """
    monkeypatch.setattr(
        "graphfaker.llm.get_provider", lambda *a, **k: RecordedProvider(replies=[GOOD])
    )
    out = tmp_path / "clinic.yaml"
    result = runner.invoke(app, ["schema", "draft", "a clinic:", "patients and doctors", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()

    written = out.read_text(encoding="utf-8")
    assert written.startswith("# Drafted by recorded")
    assert "a clinic: patients and doctors" in written, "the description is recorded in the file"
    assert GraphSchema.from_yaml(written).name == "clinic"

    data = tmp_path / "graph"
    result = runner.invoke(app, ["generate", "--schema", str(out), "--seed", "1", "--out", str(data), "--quiet"])
    assert result.exit_code == 0, result.output
    assert (data / "nodes" / "Patient.parquet").is_file()


def test_the_cli_prints_the_yaml_when_there_is_no_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "graphfaker.llm.get_provider", lambda *a, **k: RecordedProvider(replies=[GOOD])
    )
    result = runner.invoke(app, ["schema", "draft", "a clinic"])
    assert result.exit_code == 0, result.output
    assert "name: clinic" in result.stdout


def test_the_cli_needs_a_description():
    result = runner.invoke(app, ["schema", "draft"])
    assert result.exit_code != 0
    assert "describe the graph" in said(result)


def test_a_draft_that_will_not_validate_exits_non_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "graphfaker.llm.get_provider", lambda *a, **k: RecordedProvider(replies=[BROKEN, BROKEN])
    )
    out = tmp_path / "clinic.yaml"
    result = runner.invoke(app, ["schema", "draft", "a clinic", "--out", str(out)])
    assert result.exit_code == 1
    assert not out.exists(), "a schema that does not validate is not written"


# ------------------------------------------------ the request that is sent


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Message:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


class _Stream:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return _Message(self._text)


class _FakeAnthropic:
    """Records every request shape it was asked for, and refuses some."""

    #: Parameter name to the exception a stand-in client raises for it, which
    #: is how the two ways this fails are reproduced: a 400 from the API and
    #: a TypeError from a client that has removed the keyword.
    refuse: dict[str, Exception] = {}
    calls: list[dict] = []

    def __init__(self, api_key=None, **_):
        self.messages = self

    def stream(self, **kwargs):
        type(self).calls.append(kwargs)
        for parameter, error in type(self).refuse.items():
            if parameter in kwargs:
                raise error
        return _Stream("name: ok\nnodes:\n- {name: A, count: 1}\n")


def _fake_anthropic(monkeypatch, refuse=None):
    """Stand in for the whole ``anthropic`` module, installed or not.

    The provider imports it inside ``complete``, so putting a stub in
    ``sys.modules`` is enough and means these tests need no network, no key
    and no optional dependency. That last one matters: CI installs the dev
    and examples extras, not ``graphfaker[llm]``, and a test that imported
    the real client to monkeypatch it passed here and failed there.
    """
    _FakeAnthropic.calls = []
    _FakeAnthropic.refuse = refuse or {}
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return _FakeAnthropic


def test_the_anthropic_request_carries_effort_and_no_temperature(monkeypatch):
    """The bug this exists for.

    Current Anthropic models removed the sampling controls and replaced them
    with an effort level, and the client raises a TypeError before anything
    reaches the network: ``Messages.create() got an unexpected keyword
    argument 'temperature'``. Asking for a near-zero temperature was not a
    conservative default, it was a request the model will not take.
    """
    from graphfaker.llm import AnthropicProvider

    fake = _fake_anthropic(monkeypatch)
    AnthropicProvider().complete("system", "user")

    assert len(fake.calls) == 1
    sent = fake.calls[0]
    assert "temperature" not in sent and "top_p" not in sent and "top_k" not in sent
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "high"}
    assert sent["model"].startswith("claude-")
    assert sent["system"] == "system"
    assert sent["messages"] == [{"role": "user", "content": "user"}]
    # No thinking budget: removed on the current models, a 400 if sent.
    assert "budget_tokens" not in str(sent)

    # The effort is the one knob, so it has to reach the request.
    _fake_anthropic(monkeypatch)
    AnthropicProvider(effort="low").complete("s", "u")
    assert _FakeAnthropic.calls[0]["output_config"] == {"effort": "low"}


def test_a_client_or_model_that_refuses_a_field_gets_the_request_without_it(monkeypatch):
    """Both ways the far end says no, and the request shrinks to fit.

    An older installed client raises a TypeError naming the keyword; an
    older model returns a 400 naming it. Picking the shape from the model
    name would mean a table of model names that is wrong the week after it
    is written.
    """
    from graphfaker.llm import AnthropicProvider

    fake = _fake_anthropic(
        monkeypatch,
        refuse={
            "output_config": TypeError("Messages.stream() got an unexpected keyword argument 'output_config'"),
            "thinking": Exception("thinking: unsupported parameter for this model"),
        },
    )
    AnthropicProvider().complete("s", "u")

    assert len(fake.calls) == 3, "full shape, then without output_config, then without thinking"
    assert "output_config" in fake.calls[0]
    assert "output_config" not in fake.calls[1] and "thinking" in fake.calls[1]
    assert "output_config" not in fake.calls[2] and "thinking" not in fake.calls[2]


def test_an_error_that_is_not_about_a_parameter_is_raised_as_it_arrived(monkeypatch):
    from graphfaker.llm import AnthropicProvider, ProviderError

    _fake_anthropic(monkeypatch, refuse={"model": Exception("connection reset by peer")})
    with pytest.raises(ProviderError, match="connection reset"):
        AnthropicProvider().complete("s", "u")
