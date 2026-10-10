# Guides

How GraphFaker turns a schema into a graph, how the fraud pack builds a bank and hides laundering in it, which generation methods exist, and how to add a domain of your own.

- [How generation works](../how-it-works.md): latent factors, sharded node tables, the topology model, derived attributes, the manifest.
- [How the fraud graph is generated](../fraud-generation.md): entities, the transaction process, the eleven typologies, decoys, hardness, what the truth contains.
- [Ways to generate synthetic graphs](../methods.md): random graph models, attribute-first generators, process simulation, fitting to a seed graph, and where GraphFaker sits.
- [Adding a domain](../adding-a-domain.md): a schema or a process, registered by name, listed by `graphfaker domains`.
- [Training a GNN on the bank](../pyg.md): the PyTorch Geometric export (features, labels, splits) and a baseline that shows what the graph is worth to a detector at each hardness.
- [Entity resolution datasets in Senzing format](../senzing.md): a generated dataset written as Senzing records, with the same company under several spellings at addresses it shares, and the answer key in a second file so any resolver can be scored.
- [Entity resolution and duplication](entity-resolution.md): resolving near-duplicate entities and measuring what an extraction pipeline duplicates.
- [Investigating a synthetic supply chain](../notebooks/supply_chain_investigation.ipynb): four detectors a procurement team would write, each scored against the truth and against the innocent suppliers it accuses.
- [Measuring duplication in an LLM-built knowledge graph](../notebooks/duplication_experiment.ipynb): the experiment, end to end.
- [A real register, and a generated one you can score](../notebooks/register_resolution.ipynb): resolve companies on a real register where there is no answer key, then on a generated one with the same measured shape where there is. The simplest approach wins, both obvious improvements cost precision, and matching on address takes it to 0.03.
- [Scaling: what was measured](../scaling.md): generation time, memory and what `--workers` is worth, on two machines, with the reproduce commands.
- [Speed, realism and hard negatives](../scaling-and-realism.md): what the measurements mean for synthetic fraud data, why speed is what makes a rare-event benchmark usable, and how the fraud pack compares to Santander's gen-fraud-graph.

```{toctree}
:hidden:

../how-it-works
../fraud-generation
../methods
../adding-a-domain
../pyg
../senzing
entity-resolution
../scaling
../scaling-and-realism
Investigating a supply chain <../notebooks/supply_chain_investigation>
Measuring duplication <../notebooks/duplication_experiment>
A real register, and one you can score <../notebooks/register_resolution>
```
