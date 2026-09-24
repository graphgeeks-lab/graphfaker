# How the fraud graph is generated

This page describes the fraud / AML domain pack step by step: what entities exist, how legitimate activity is produced, how each laundering typology is injected, what the hardness levels change, and what the truth tables contain. The code is in `graphfaker/domains/fraud/`, one module per step.

## How the fraud hides, in one paragraph

The fraud is drawn from the same distributions as the legitimate traffic: amounts from the channel's own amount model scaled by the account's income, timing from the same hour-of-day and weekday profile, partners recruited uniformly from ordinary accounts rather than from a marked pool. Those accounts keep behaving normally while the pattern runs, so none of them is a single-purpose account. And the dataset contains innocent accounts shaped exactly like laundering, labelled as innocent, so a rule that keys on shape alone is punished. What is left to find is structure over time, which is the point: nothing separates the fraud from the background except the thing a detector is supposed to be good at.

## Scale

`scale` follows the convention of Santander's gen-fraud-graph so datasets are comparable: `scale=1.0` means about 10 million accounts and 90 million transactions. Everything else derives from it.

| quantity | rule | at scale 0.01 |
|---|---|---|
| accounts | 10,000,000 × scale, at least 200 | 100,000 |
| customers | accounts / 1.4 | 71,428 |
| merchants | accounts × 0.02, at least 200 | 2,000 |
| devices | customers × 1.15 | 82,142 |
| counterparties | accounts × 0.002, at least 20 | 200 |
| transactions | 90,000,000 × scale | 900,000 |
| patterns | 1,000 × scale, at least 2 per typology | 22 |

The convention implies a density of about nine transactions per account over a 90-day period. That is low compared with a real retail account and it matters for detectability; see "What hardness cannot hide" below.

## Step 1: entities (`entities.py`)

Entities are declared as a `GraphSchema` and drawn by the generic engine, so the same samplers, sharding and seeding apply. One latent factor, `region`, gives each region a mean log income and a mean log balance; customers, accounts and merchants all carry a region.

| node | attributes |
|---|---|
| Customer | name, date of birth, email, phone, street, city, segment (retail / affluent / business), KYC tier, log income (around the region's mean), activity (a log-normal propensity to transact) |
| Account | customer (a foreign key into the same region), account type (checking / savings / business / credit), status (active / dormant / closed), currency, balance, opened_at |
| Merchant | name, category (eleven categories, each with its own amount profile), city, prominence (log-normal popularity) |
| Device | fingerprint, device type, OS, first_seen |
| Counterparty | name, country, IBAN (external banks that receive wires) |

Two structural edge sets are built after the tables. `OWNS` links each account to its customer. `USES` links customers to devices: every customer gets a primary device, surplus devices become second devices, and 6% of devices are also used by another customer of the same region. Those are households. They matter because guilty device sharing (below) has to be distinguishable from innocent sharing rather than being the only sharing in the graph.

Account opening dates are exponential in age (most accounts are a few years old) and always precede the transaction period, so no account transacts before it exists.

## Step 2: legitimate transactions (`process.py`)

The process is vectorised with numpy; 900,000 transactions take about two seconds. It has two layers.

**Recurring flows** come first.

- Salary: each employed customer's first checking account receives a monthly transfer from an employer, a business account in the same region, on a payday (the 25th to 28th, or the 1st). The amount is the customer's income divided by twelve with 1% noise.
- Rent: 55% of checking accounts pay one rent merchant on a fixed day between the 1st and the 5th, the same amount each month.
- Utilities: 70% of checking accounts pay one or two utility merchants between the 8th and the 20th with 8% monthly variation.
- Subscriptions: checking and credit accounts hold on average 1.5 subscriptions, paid on a fixed day.

Participation in these flows is thinned so that recurring events make up about 30% of the transaction budget at any scale. Without thinning, the fixed budget per account would be mostly rent and subscriptions.

**Ad-hoc activity** fills the rest of the budget. Each account's expected count is proportional to its customer's activity, its account type (business 2.5×, savings 0.15×, credit 0.8×) and its status (dormant 0.05×, closed 0). Counts are Poisson. Each account's events are split across channels by account type: a checking account is 70% card payments, 27% transfers, 3% wires; a credit account is almost all card payments; a business account does more transfers and wires.

- Card payments (`PAYS`) go to a merchant chosen by prominence, 80% of the time within the customer's region. The amount is log-normal with the merchant category's parameters (a grocery visit is around $55, rent around $1,300) multiplied by an income factor.
- Transfers (`TRANSFERS`) go to one of the account's contacts 75% of the time. Contacts are two to six accounts per account, drawn 70% from the same region and weighted by activity. The remaining transfers go to a random account. Amounts come from a mixture: everyday amounts (median around $100) plus 8% large transfers (median around $2,400) so the tail reaches the thousands the way real P2P rails do. A short memo is attached.
- Wires (`WIRES`) go to a random external counterparty with log-normal amounts around $1,100.

Timestamps follow a day-of-week profile per channel (weekends quieter, wires almost only on weekdays) and an hour-of-day profile with a lunchtime and an evening peak. Amounts scale with the customer's income (`amount ∝ exp(0.5 × (log_income − mean))`).

What the process does not do: it does not keep a running balance, so an account can spend more than it holds. That is a known simplification.

## Step 3: injected patterns (`typologies.py`)

After the legitimate process, patterns are injected. Each pattern picks its accounts from eligible ones (checking, business or savings, not closed), chooses a time window inside the period, creates its transactions, and records its accounts with roles.

Each typology has a signature: the thing a simple rule was written to catch. Hardness (next section) controls how much of the signature survives.

| typology | shape | signature |
|---|---|---|
| `fan_in` | 5 to 15 sources send to one collector; the collector often wires the pool out | round amounts between $1,000 and $9,000, all within a short window |
| `fan_out` | one distributor sends to 5 to 15 receivers | near-identical splits of one lump sum |
| `gather_scatter` | 4 to 10 sources send to a hub, which forwards to 4 to 10 receivers | round amounts in, equal splits of 92 to 98% out |
| `scatter_gather` | a source sends to 4 to 10 intermediaries, each forwards 90 to 98% to a collector | equal splits and quick pass-through |
| `cycle` | 4 to 7 accounts in a ring, each hop after the last | amounts of $5,000 to $15,000 decaying 0.5 to 3% per hop |
| `stack` | a chain of 3 to 6 accounts | same as cycle without closing the ring |
| `bipartite` | 3 to 6 sources each pay most of 3 to 6 targets | round amounts between $500 and $4,000 |
| `structuring` | 6 to 20 deposits from one account to 1 to 3 receivers | every amount between 90% and 99.5% of the reporting threshold |
| `mule_network` | 3 to 10 mules receive from victims and forward 90 to 97% to a hub within hours | mules share one device; at low hardness their accounts were opened days before |
| `bust_out` | one credit account builds rising, ordinary spending, then a burst at electronics, travel and retail merchants | the burst is 4 to 8 times the account's typical purchase |
| `synthetic_identity` | 3 to 7 customers with identical phone, street and city and one shared device funnel funds to a collector | attribute collisions plus pass-through |

**Decoys** are legitimate structures with the shape of a typology, labelled `is_fraud = False`: a business paying salaries to many people on one day (a payroll fan-out), a marketplace collecting many small orders (a fan-in), suppliers invoicing each other in a loop (a cycle). Decoys never share accounts with fraud patterns so that labels stay unambiguous.

**Recruitment** of pattern members is uniform over eligible accounts. An earlier version weighted recruitment towards active accounts on the theory that extra transactions would stand out less on a busy account. Measurement showed the opposite: busy accounts are outliers already, and rings built from them were found by degree alone. Ordinary accounts plus a few extra edges hide better.

## Step 4: what hardness changes (`config.py`)

`hardness` is a preset of six numbers. They are inputs, not claims; the hardness report (below) measures what they achieve.

| parameter | low | medium | high | effect |
|---|---|---|---|---|
| `amount_blend` | 0 | 0.5 | 0.9 | probability that a signature amount is replaced with a draw from the legitimate distribution of the same channel |
| `timing_spread_days` | 0.1 | 3 | 14 | multiplier on each typology's natural span; low packs a pattern into hours, high spreads it over weeks |
| `ring_overlap` | 0 | 0.2 | 0.4 | probability that a new pattern reuses an account already in one |
| `decoy_ratio` | 0 | 0.5 | 1.0 | decoys as a fraction of the fraud pattern count |
| `activity_camouflage` | 0 | 0.6 | 1.0 | share of pattern accounts that keep their legitimate activity; the rest have it removed, so they look like single-purpose accounts |
| `size_scale` | 1.0 | 0.75 | 0.5 | multiplier on pattern sizes (sources in a fan-in, hops in a chain), never below three |

Structuring is treated specially. Blending its amounts into the legitimate distribution would remove what makes it structuring, so `amount_blend` instead widens the band below the threshold (from 90 to 99.5% at low to 50 to 99.5% at high) and occasionally lets an amount slip above it.

Mule accounts are made "fresh" (opened days before the pattern) with probability `1 − activity_camouflage`, and any legitimate transaction they had before that date is removed.

## Step 5: assembly (`generate.py`)

Legitimate and injected transactions are numbered in time order across all three channels, `tx_0, tx_1, ...`, so ids increase with time like a real ledger and do not reveal which rows were injected. The rows themselves stay in generation order (a block of accounts at a time, then the injected rows), so sort by `timestamp` when you want time order. The `PAYS`, `TRANSFERS` and `WIRES` tables carry no labels.

Pattern side effects are applied to the entity tables: fresh opening dates, shared devices added to `USES`, and synthetic identities copying phone, street and city from their template customer.

## Truth tables

| table | columns |
|---|---|
| `truth/patterns` | pattern_id, typology, is_fraud, n_accounts, n_transactions, start, end, accounts (list), roles (list) |
| `truth/accounts` | account_id, pattern_id, typology, role, is_fraud (one row per membership) |
| `truth/transactions` | tx_id, pattern_id, typology, is_fraud |
| `truth/region` | the latent groups and their parameters |

`manifest.json` carries the full `FraudConfig` under `extra.fraud`.

## Measuring the result (`hardness.py`, `evaluate.py`)

`hardness_report(run)` computes, for every typology and for all fraud together, the AUC that each single feature achieves separating fraud accounts (or transactions) from legitimate ones. Account features: transaction counts in and out, amounts in and out, maximum and mean amount, distinct partners in and out, pass-through ratio, share of round amounts, share of near-threshold amounts, burstiness, account age. Transaction features: amount, roundness, near-threshold, hour, weekend. The AUC is symmetric (a feature that works in either direction scores above 0.5), so it answers "could a threshold on this one number find the fraud?".

`realism_report(run)` reports properties of the legitimate process a reader would check first: merchant in-degree Gini, repeat-partner share and same-region share of transfers, recurring share, weekend and night shares, amount median and 99th percentile.

`evaluate(truth, flagged_accounts, flagged_transactions, ring_threshold)` scores a detector at three levels: accounts, transactions, and patterns (a pattern counts as found when at least `ring_threshold` of its accounts are flagged). It follows gen-fraud-graph's evaluator so results are comparable. Flagging a decoy's accounts counts as a false positive.

## What hardness cannot hide

Measured on 20,000 accounts:

| hardness | transaction `amount` AUC | best account-level feature |
|---|---|---|
| low | 0.94 | max_amount 0.85 |
| medium | 0.79 | max_amount 0.75 |
| high | 0.62 | in_partners 0.73 |

Amount and round-number signals fade as intended. Degree does not fade completely: with nine transactions per account per quarter, even a five-member ring adds partners an ordinary account does not have. Two typologies are defined by a single feature (structuring by near-threshold amounts, bust-out by spend escalation) and score high per typology at every level; the "all" row is the number to quote.
