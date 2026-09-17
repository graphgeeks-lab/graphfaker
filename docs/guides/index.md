# Guides

How GraphFaker turns a schema into a graph, how the fraud pack builds a bank and hides laundering in it, which generation methods exist, and how to add a domain of your own.

- [How generation works](../how-it-works.md): latent factors, sharded node tables, the topology model, derived attributes, the manifest.
- [How the fraud graph is generated](../fraud-generation.md): entities, the transaction process, the eleven typologies, decoys, hardness, what the truth contains.
- [Ways to generate synthetic graphs](../methods.md): random graph models, attribute-first generators, process simulation, fitting to a seed graph, and where GraphFaker sits.
- [Adding a domain](../adding-a-domain.md): a schema or a process, registered by name, listed by `graphfaker domains`.
- [Training a GNN on the bank](../pyg.md): the PyTorch Geometric export (features, labels, splits) and a baseline that shows what the graph is worth to a detector at each hardness.
- [Entity resolution and duplication](entity-resolution.md): resolving near-duplicate entities and measuring what an extraction pipeline duplicates.
- [Measuring duplication in an LLM-built knowledge graph](../notebooks/duplication_experiment.ipynb): the experiment, end to end.
- [Scaling: what was measured](../scaling.md): generation time, memory and what `--workers` is worth, on two machines, with the reproduce commands.
- [Speed, realism and hard negatives](../scaling-and-realism.md): what the measurements mean for synthetic fraud data, why speed is what makes a rare-event benchmark usable, and how the fraud pack compares to Santander's gen-fraud-graph.

```{toctree}
:hidden:

../how-it-works
../fraud-generation
../methods
../adding-a-domain
../pyg
entity-resolution
../scaling
../scaling-and-realism
Measuring duplication <../notebooks/duplication_experiment>
```
