# The semantic layer: what a language model is used for here, and what it is not

GraphFaker uses a language model for exactly one thing: turning a sentence into a declaration the engine runs. It never writes a row.

That is a design decision rather than a limitation, and it is worth saying why, because the obvious thing to build is the other one.

## Why not just ask a model for the data

Ask a model for ten thousand transactions and you get ten thousand plausible transactions. What you do not get is a degree distribution, repeat partners, seasonality, a heavy tail, a population that agrees with itself across tables, ground truth, or the same dataset twice. Those are properties of a process, and a model producing rows one at a time has no process: it has a prior over what a transaction looks like, which is a different thing and a much flatter one.

The engine already knows how to produce all of that. Every number in it came from a measurement against real data, and the benchmarks directory says which. What the engine cannot do is work out what you meant by "a hospital with patients, doctors, wards and admissions over time". That is a language problem, and it is the only part of this where a model is better than the alternative.

So: **the model compiles intent, the engine owns generation.**

## The reproducibility boundary

This is the line everything else rests on, and it is enforced by where the code lives rather than by discipline.

A model never runs inside `generate`. It runs in `graphfaker schema draft`, which writes a YAML file. You read the file, change the counts, fix whatever it misunderstood, and commit it. From that point the dataset is a pure function of the file, the seed and the shard size, exactly as it was before any of this existed.

Two consequences worth stating plainly:

- **Two drafts will not agree.** No sampling temperature makes a model deterministic, and the drafter does not pretend otherwise. The draft is a first guess at what you meant.
- **Two generations always will.** Once the YAML is on disk, nothing asks a model anything, and the same seed gives the same bytes on any machine with the same version of GraphFaker.

If a future feature puts model output into the data itself (generated memo text, product descriptions), it will follow the same rule: the model writes a cache file that is an input to generation, committed and content-addressed, so the dataset stays a pure function of its inputs.

## Installing it

```sh
pip install "graphfaker[llm]"
```

Two small pure-Python clients, neither imported unless you ask for one. Nothing in a generated dataset depends on either, so a collaborator who only wants to generate from your committed schema does not need the extra at all.

Configure it with environment variables so a key never lands in a shell history:

| variable | meaning |
|---|---|
| `GRAPHFAKER_LLM_PROVIDER` | `anthropic` (default) or `openai` |
| `GRAPHFAKER_LLM_MODEL` | the model to ask; defaults to Anthropic's most capable |
| `GRAPHFAKER_LLM_BASE_URL` | an OpenAI-compatible endpoint, for a local model |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | the key, read by the vendor's own client |

`--provider`, `--model` and `--base-url` override them per call.

## Drafting a schema

```sh
graphfaker schema draft "a hospital: patients, doctors, wards, and admissions over time" --out hospital.yaml
```

```
asking anthropic (claude-opus-5-5)...
wrote hospital.yaml
drafted 'hospital' with anthropic (claude-opus-5-5)
  nodes  4,180  Patient=4,000, Doctor=120, Ward=60
  edges  18,000  4 relationship types across 3 families
  latent ward(2 params)
  read it before you generate from it: a drafted schema is a first guess, not an answer
```

The YAML goes to the file (or to stdout with no `--out`, so it can be piped); everything else goes to stderr. Then it is an ordinary schema:

```sh
graphfaker validate hospital.yaml
graphfaker generate --schema hospital.yaml --seed 1 --out ./hospital
```

### What the model is told

The system prompt is built at call time rather than written down, which matters more than it sounds. The list of sampler kinds and their fields is read off the pydantic models, and the worked example is the social pack's own schema. A hand-maintained prompt is wrong the first time a sampler is added, and the failure mode is a draft that uses a word the engine does not know.

### What happens when it gets it wrong

The draft is validated before you see it. If it does not parse as a `GraphSchema`, the pydantic errors go back to the model once, with its own previous answer, and it tries again. One repair round fixes most of them, because the failures are mechanical: a sampler kind that does not exist, an edge pointing at a node type renamed halfway through the answer, a latent parameter referenced but never declared.

After a second failure it stops, exits non-zero, and prints the last attempt so you can see what it was reaching for. It does not write a file. A schema that does not validate is an error, not a dataset.

## What to check in a draft

The model is good at structure and unreliable about magnitude. In rough order of how often it is worth fixing:

- **Counts.** It will pick something round. Whether 4,000 patients to 120 doctors is the ratio you want is a question about your domain, not about English.
- **Distributions.** Money, durations and sizes are lognormal far more often than they are gaussian, and a drafted schema often reaches for gaussian anyway. A gaussian amount has no tail, and no tail means no outliers to find.
- **The latent factor.** A schema with no latent factor is a pile of independent columns: attributes that do not agree with each other and edges that do not agree with either. If the draft has none and your domain has any kind of grouping (a region, a ward, a market segment), adding one is the single change that makes the data behave.
- **Edge shares.** They should sum to about 1.0 and they describe where the edge budget goes, which is usually not where a first guess puts it.
- **Dates that read the clock.** The prompt tells the model to pin them and it mostly does, but `end_date: 'now'` slips through, and a schema that asks for the clock gets one: the same seed then produces different data tomorrow. `graphfaker validate` warns and names the attribute.

## What this is not

It is not a benchmark generator. A drafted schema produces a population with structure and no ground truth: there are no injected patterns, no labels and no answer key, because those come from a domain pack's catalogue and a pack is code. `graphfaker generate fraud` and `graphfaker generate supply_chain` are where the labelled datasets are.

It is also not a replacement for reading the schema reference. The draft is a starting point that saves you the first hour, not a thing to generate from unread.
