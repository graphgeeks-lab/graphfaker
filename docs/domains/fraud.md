# Fraud and AML

A bank's transaction graph is the most useful dataset a fraud team cannot share. GraphFaker generates one that behaves like it without containing anyone: customers, accounts, merchants and devices with correlated attributes; a transaction process with salaries, rent, repeat partners and seasonality; and money-laundering typologies injected on top with every account, role and transaction recorded. Realism is measured, not asserted (degree distribution, clustering, communities, assortativity against a random baseline), and so is how hard the fraud is to find (the AUC of every single-feature rule a detector would try first). The result loads into Neo4j, LadybugDB or DuckDB and verifies against the files it came from, with a blind copy for honest benchmarks. No real customer data goes in, so none can come out.

The fraud domain generates a bank: customers, accounts, merchants, devices and external counterparties; a period of realistic transaction activity; and laundering typologies injected into it and recorded. It is the dataset for demonstrating a graph database, benchmarking a fraud detector, or training a GNN against a known answer.

```bash
graphfaker generate fraud --scale 0.01 --hardness medium --seed 42 --out ./bank
graphfaker fraud --scale 0.01 --hardness medium --seed 42 --out ./bank      # the same, plus the hardness and realism reports
```

```python
from graphfaker.domains import fraud
from graphfaker.domains.fraud.hardness import hardness_report, realism_report
from graphfaker.domains.fraud.evaluate import evaluate

run = fraud.generate(scale=0.01, hardness="medium", seed=42, workers=8)
run.tables.edges["TRANSFERS"]
run.truth["patterns"]
print(hardness_report(run).summary())
```

## What it contains

| node type | what it is |
|---|---|
| Customer | a person with name, contact details, segment, KYC tier, income, and a propensity to transact |
| Account | checking, savings, business or credit; opened before the period; owned by one customer |
| Merchant | a place to pay, in one of eleven categories, with a heavy-tailed popularity |
| Device | shared by households innocently and by mule rings guiltily |
| Counterparty | an external bank that receives wires |

Relationships: `OWNS` (customer to account), `USES` (customer to device), and three transaction channels carrying `tx_id`, `timestamp`, `amount`, `memo` and `recurring`: `PAYS` (account to merchant), `TRANSFERS` (account to account), `WIRES` (account to counterparty).

## Options

| option | default | meaning |
|---|---|---|
| `scale` | 0.001 | size, on gen-fraud-graph's convention: 1.0 is about 10M accounts and 90M transactions |
| `hardness` | `medium` | `low`, `medium` or `high`: how well the fraud hides in normal activity |
| `period_start`, `period_days` | 2024-01-01, 90 | the span of transaction history |
| `reporting_threshold` | 10000 | the cash reporting threshold structuring stays under |
| `patterns` | derived from scale | how many of each typology to inject, e.g. `{"cycle": 10, "fan_in": 5}` |
| `regions` | 8 | latent regions; income, balances, merchant choice and transfer partners correlate within one |

## The typologies

`fan_in`, `fan_out`, `gather_scatter`, `scatter_gather`, `cycle`, `stack` and `bipartite` (the AMLworld set), plus `structuring` (deposits under the reporting threshold), `mule_network` (pass-through within hours, a shared device, freshly opened accounts), `bust_out` (credit built up then maxed out) and `synthetic_identity` (customers sharing phone, address and device). At `medium` and `high` hardness, decoys are added: payroll fan-outs, marketplace fan-ins and supplier cycles that look like typologies and are labelled as not fraud.

Every pattern is recorded in `truth/patterns` (typology, accounts, roles, transactions, start and end), `truth/accounts` (one row per membership, with role) and `truth/transactions` (which transactions were planted). The edge tables carry no labels, and transaction ids are assigned in time order so the id does not reveal what was injected.

## Hardness, measured

`hardness_report` scores every single feature a simple rule might threshold on (amount, round amounts, proximity to the threshold, degree, pass-through, burstiness, account age) by the AUC it achieves against the truth. On a 20K-account run the transaction `amount` AUC falls from 0.94 at `low` to 0.62 at `high`; degree remains the signal that survives. `evaluate` scores flagged accounts and transactions at account, transaction and pattern level, the way gen-fraud-graph's evaluator does.

The scale convention, the evaluator's three levels and the compatible output layout come from Santander's [gen-fraud-graph](https://github.com/SantanderAI/gen-fraud-graph), so the two generators' datasets are comparable; the first seven typologies follow AMLworld. The full method, step by step, is in [How the fraud graph is generated](../fraud-generation.md). To put the result in a database with the truth alongside, see [Neo4j](../neo4j.md), [LadybugDB](../ladybug.md) and [DuckDB](../duckdb.md).
