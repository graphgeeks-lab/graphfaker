"""Score Cypher fraud detectors against the ground truth in Neo4j.

Load a fraud dataset first, with its truth::

    graphfaker fraud --scale 0.01 --seed 42 --out ./bank
    graphfaker load neo4j ./bank --database fraud --create

    NEO4J_PASSWORD=... python examples/neo4j_detectors.py --database fraud

Each rule returns a column called ``flagged``; the same scoring tail is
appended to all of them, so the numbers are comparable. This regenerates the
table in ``docs/neo4j.md``. Run it after changing a rule rather than
trusting the numbers already written down.

The point is not that any of these rules is good. At ``--hardness medium``
none of them gets far past 7% recall, and the cycle rule finds every decoy
and almost no fraud. Being able to measure that is the point.
"""

from __future__ import annotations

import argparse
import time

from graphfaker.sinks import Target

#: Appended to every rule. Takes a `flagged` column of account ids and
#: scores it against the accounts that belong to a fraud pattern.
SCORE = """
WITH collect(DISTINCT flagged) AS flagged
CALL () {
  MATCH (a:Account)-[:IN_PATTERN]->(p:Pattern) WHERE p.is_fraud
  RETURN collect(DISTINCT a.id) AS actual
}
WITH flagged, actual, [x IN flagged WHERE x IN actual] AS tp
RETURN size(flagged) AS flagged, size(actual) AS fraud_accounts, size(tp) AS tp,
       CASE size(flagged) WHEN 0 THEN 0.0
            ELSE round(1000.0 * size(tp) / size(flagged)) / 10 END AS precision_pct,
       round(1000.0 * size(tp) / size(actual)) / 10 AS recall_pct
"""

RULES: dict[str, str] = {}

RULES["fan-in >= 8 senders, whole period"] = """
MATCH (dst:Account)<-[:TRANSFERS]-(src:Account)
WITH dst, count(DISTINCT src) AS senders
WHERE senders >= 8
WITH dst.id AS flagged
"""

RULES["fan-in >= 5 senders in one week"] = """
MATCH (dst:Account)<-[t:TRANSFERS]-(src:Account)
WITH dst, t.timestamp.week AS week, count(DISTINCT src) AS senders
WHERE senders >= 5
WITH dst.id AS flagged
"""

RULES["...and the burst is >80% of lifetime inflow"] = """
MATCH (dst:Account)<-[t:TRANSFERS]-(:Account)
WITH dst, sum(t.amount) AS lifetime
MATCH (dst)<-[t:TRANSFERS]-(src:Account)
WITH dst, lifetime, t.timestamp.week AS week,
     count(DISTINCT src) AS senders, sum(t.amount) AS burst
WHERE senders >= 5 AND burst > 0.8 * lifetime
WITH dst.id AS flagged
"""

RULES["three-hop cycle"] = """
MATCH (a:Account)-[t1:TRANSFERS]->(b:Account)-[t2:TRANSFERS]->(c:Account)-[t3:TRANSFERS]->(a)
WHERE t1.timestamp < t2.timestamp AND t2.timestamp < t3.timestamp
  AND duration.inDays(t1.timestamp, t3.timestamp).days <= 30
UNWIND [a.id, b.id, c.id] AS flagged
WITH flagged
"""

# Unseeded, a *3..5 cycle search over 100k accounts is not worth waiting for.
# Seeding on accounts that both took in and pushed out a large sum in the same
# week cuts it to a few hundred starting points.
RULES["seeded 3-5 hop cycle, time-ordered"] = """
MATCH (a:Account)-[out:TRANSFERS]->(:Account)
WITH a, out.timestamp.week AS week, sum(out.amount) AS sent
WHERE sent > 5000
MATCH (a)<-[in_t:TRANSFERS]-(:Account)
WHERE in_t.timestamp.week = week
WITH DISTINCT a
MATCH path = (a)-[:TRANSFERS*3..5]->(a)
WITH nodes(path) AS ns, relationships(path) AS rs
WHERE all(i IN range(0, size(rs) - 2) WHERE rs[i].timestamp < rs[i + 1].timestamp)
  AND duration.inDays(rs[0].timestamp, rs[size(rs) - 1].timestamp).days <= 30
UNWIND ns AS n
WITH n.id AS flagged
"""

RULES["amounts just under the threshold"] = """
MATCH (a:Account)-[t:TRANSFERS|WIRES]->()
WHERE t.amount > 8500 AND t.amount < 10000
WITH a, count(t) AS near_threshold
WHERE near_threshold >= 3
WITH a.id AS flagged
"""

RULES["24-hour pass-through"] = """
MATCH (:Account)-[i:TRANSFERS]->(mid:Account)-[o:TRANSFERS]->(:Account)
WHERE o.timestamp > i.timestamp
  AND duration.inSeconds(i.timestamp, o.timestamp).seconds < 86400
  AND abs(o.amount - i.amount) / i.amount < 0.1
WITH mid.id AS flagged
"""

#: Where a rule's hits come from, split by typology and by whether the
#: pattern is fraud or a decoy. Decoys are legitimate activity shaped like
#: laundering; a rule that catches all of them is measuring shape, not crime.
BREAKDOWN = """
WITH collect(DISTINCT flagged) AS flagged
MATCH (a:Account)-[:IN_PATTERN]->(p:Pattern)
WITH p.typology AS typology, p.is_fraud AS is_fraud, flagged, collect(DISTINCT a.id) AS accounts
WITH typology, is_fraud, size(accounts) AS accounts,
     size([x IN accounts WHERE x IN flagged]) AS caught
WHERE caught > 0
RETURN typology, is_fraud, accounts, caught ORDER BY is_fraud DESC, typology
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="fraud")
    parser.add_argument("--uri", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--breakdown", action="store_true", help="also print per-typology hits")
    args = parser.parse_args()

    given = {k: v for k, v in vars(args).items() if k in ("uri", "password", "database") and v}
    target = Target(**given)
    driver = target.connect()

    header = f"{'rule':44} {'flagged':>8} {'tp':>4} {'prec%':>7} {'recall%':>8} {'time':>7}"
    print(header)
    print("-" * len(header))
    try:
        for name, rule in RULES.items():
            started = time.perf_counter()
            records, _, _ = driver.execute_query(rule + SCORE, database_=target.database)
            row = records[0]
            print(
                f"{name:44} {row['flagged']:>8} {row['tp']:>4} "
                f"{row['precision_pct']:>7} {row['recall_pct']:>8} "
                f"{time.perf_counter() - started:>6.1f}s"
            )
            if args.breakdown:
                hits, _, _ = driver.execute_query(rule + BREAKDOWN, database_=target.database)
                for hit in hits:
                    kind = "fraud" if hit["is_fraud"] else "DECOY"
                    print(
                        f"    {kind:>5}  {hit['typology']:<20} "
                        f"caught {hit['caught']}/{hit['accounts']}"
                    )
        fraud_accounts = records[0]["fraud_accounts"]
        print("-" * len(header))
        print(f"{fraud_accounts} accounts belong to a fraud pattern in this dataset.")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
