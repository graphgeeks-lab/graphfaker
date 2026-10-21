=======
History
=======

1.4.0 (2026-10-21)
------------------

Two guards and a way to read a schema back. Nothing in a dataset changes
for a given seed.

The guards are both about not destroying something quietly. A dataset
directory could become the union of two runs, and ``schema draft`` could
write over a schema you had edited. Neither announced itself: the first was
reported as "the draft generates badly but validate is happy", which is
exactly what it looks like from outside.

* ``graphfaker validate --verbose`` reads a schema back to you.
  ``GraphSchema.describe()`` renders the thing somebody holding a few
  hundred lines of drafted YAML actually needs, and it is not a
  pretty-printer for the file: each attribute carries its sampler and the
  sampler's parameters, because a gaussian where the column means money is
  the commonest thing wrong with a written schema and a gaussian amount has
  no tail; each edge family carries its budget **divided by its source
  rows**, because a schema states a share of a total and nothing in the file
  says whether that works out to one payment per purchase; and the latent
  factor gets a line even when there is none, because a schema without one
  is a pile of independent columns.

  That middle number is the one that earns the flag. ``0.80 per Admission``
  means a fifth of admissions land in no ward, and ``0.60 per Patient`` on a
  functional relationship means 40% of patients have no primary physician.
  Neither is visible in the YAML, and on a drafted customs schema the same
  arithmetic was the entire finding. The summary lives on the schema rather
  than in the CLI so a notebook can use it, which is where a draft actually
  gets picked apart, and it judges nothing: the same contract the hardness
  and coverage reports have.

* ``graphfaker schema draft`` will not write over an existing file.
  A drafted schema is meant to be read and edited, and a second draft of
  the same sentence is a different schema, so replacing one silently throws
  away the editing that is the point of the file. Checked before the model
  is asked, so a mistaken rerun costs nothing; ``--force`` overwrites. If a
  dataset sits in the directory named after the schema, which is the
  pairing people make, it says so rather than doing anything about it.

* A dataset directory can no longer become the union of two runs. The
  write is per table and removed nothing, so generating a second dataset
  into the same ``--out`` left both: the manifest said two node types and
  the folder held four, and every loader reads the directory rather than
  the manifest, so it read one graph made of two. ``GraphRun.write``
  refuses when the directory holds tables the run does not replace, names
  them, and writes nothing; ``--force`` on ``generate`` and ``fraud``
  (``force=True`` from Python) replaces the dataset instead.

  It only comes up when the set of tables changes, which means a renamed
  or removed node type or a different schema entirely, so regenerating the
  same schema is unaffected and needs no flag. Only ``nodes/``, ``edges/``
  and ``truth/`` are considered and only ``.parquet`` in them, so a
  directory holding your own files is neither reported on nor touched.

  Reported by a user who drafted twice into the same name and found the
  result generated badly while ``validate`` was happy, which is exactly
  what this looks like from outside: the schema file was fine and the
  directory was not.

1.3.0 (2026-10-17)
------------------
The schema debt paid: what a domain pack does over time is a declaration the
engine runs, rather than three Python files answering the same three questions
with different constants. A coverage report that says what a dataset actually
contains. Company identifiers that are mostly missing, the way a register's
are. And the semantic layer's first half, which turns a sentence into a schema
file you read and commit.

Six correctness fixes came out of pointing that last one at a live model, and
one of them matters more than the feature it came from: a seeded dataset is the
same tomorrow. It was not before.

Datasets change for a given seed in the ``supply_chain`` pack, which gained
the identifier coverage below, and in the ``fraud`` and ``social`` packs,
whose date providers no longer read the clock. ``coordination`` is
byte-identical to 1.2.0.

* The schema debt, paid. ``graphfaker.schema.process`` holds what a
  process *is*, as declarations: a ``TimingModel`` (weekday weights, an hour
  profile or an office-hours window, an optional quarter-end boost), a
  ``MonthlySchedule``, an amount model that is a ``Lognormal`` or a
  ``Mixture`` of two, ``Rounding`` for the round-number rate procurement
  invoices really have, and ``ChannelSpec`` and ``ProcessSpec`` over them.
  ``graphfaker.engine.process`` runs them: day weights, timestamps, monthly
  dates, amounts, blocks.

  Three packs answered the same three questions (when, how often, how big)
  in three ``process.py`` files with different constants, and ``day_weights``,
  the timestamp sampler and the block splitter existed twice over. The fraud
  and supply chain packs now declare their timing and amounts and call the
  engine. **No dataset moved**: the fingerprints of every pack at several
  scales and hardness levels are the digests they were before the refactor,
  which ``tests/test_process.py`` asserts, because a refactor is not a
  release.

  What stayed in the packs is partner selection, which is the part that is
  genuinely different each time: a merchant chosen by region and category, a
  contact from a repeat list, a supplier with a contract. A timing model is
  not. The coordination pack keeps its own timestamps too: it samples days by
  rejection against the profile rather than by a weighted choice, and folding
  a third method into the model would have been a branch per caller rather
  than a lift.

  The packs also declare how their events are divided, which is the half
  that turns a set of helpers into a process the engine drives. Two shapes
  were in the code and both are declarations now: a flat ``share`` of the
  event budget per channel, which is what the supply chain does, and an
  ``ActorMix``, which spreads the budget over the actors in proportion to how
  active they are and then divides each actor's events by what kind of actor
  it is, which is what the fraud pack does because a credit card makes no
  wires and a flat share cannot say that. A channel can also derive its
  volume from another, one event for one, which is what a shipment and an
  invoice are to an order. ``channel_counts``, ``actor_counts`` and
  ``split_by_mix`` do the arithmetic the packs used to do inline.

  A ``Domain`` carries its ``ProcessSpec``, and ``graphfaker schema <domain>``
  prints it above the entity schema: where each channel's volume comes from,
  when it happens, what the amounts look like and how often they are round.
  The schema says what the entities are; this says what they do.

  This is what the designer work needs. A compiler has nothing to emit into
  until a process can be written down, and now one can be. The coordination
  pack is the remaining holdout: it samples days by rejection against the
  profile rather than by a weighted choice, and folding a third method into
  the model would have been a branch per caller rather than a lift.

* A coverage report, the third metric next to hardness and realism.
  ``graphfaker.engine.coverage_report.coverage_report(run)`` says what a
  dataset actually contains, which is the question people ask first and the
  one nothing answered: every shape in the pack's catalogue and how many of
  each, the roles the members filled, where the pattern events sit in the
  ordinary process (which channels, which part of the day and week, which
  amount deciles, each against the background of the whole run), which parts
  of the population were recruited, and each shape against the decoy that
  imitates it. Printed by ``graphfaker fraud`` with the other two reports and
  by ``graphfaker generate <domain> --coverage`` for any pack, and carried in
  ``--json``.

  It is domain-agnostic because it has to be: it reads the truth tables
  through ``graphfaker.sinks.truth``, which finds them by shape, and the
  pack's ``PatternCatalog``, which a ``Domain`` now carries alongside its
  process. A pack gets the report by existing. The catalogue is what makes it
  worth having, because the truth tables hold what happened and only the
  catalogue knows what was supposed to be possible, so without it the report
  can say what a run contains and not what it is missing.

  Reading the decoys took both conventions in the packs. Fraud and supply
  chain record a decoy under the name of the shape it imitates, on purpose,
  so that the shape column says what a structure looks like and the label
  says whether it is the thing being looked for; coordination records a decoy
  under its own name. Counting by the shape column alone gets one of the two
  wrong, and which one depends on the pack.

  Nothing in it is a verdict. A small run cannot carry fourteen shapes at a
  floor of two, and some typologies genuinely only happen on weekdays. It
  says what is there.

* Identifiers are mostly missing now, the way a register's are. A generated
  company carried every column; a real one carries a latitude 67.3% of the
  time, a postcode 61.9%, a LinkedIn page 33.1%, a Placekey 29.2%, a website
  14.7%, a second address line 2.8%, a state filing number 1.9%, an NPI 0.4%
  and an LEI 0.06%, measured over the 1,033,773 companies registered in
  Nevada in the 2026-03-05 open data export and agreeing within a point with
  the Las Vegas register. That was the last of the four measured realism gaps
  for companies, and the one that matters most for the thing people build
  these datasets to try: entity resolution is hard because the attribute that
  would settle a match is absent, and a dataset where every column is filled
  turns the problem into a lookup.

  ``graphfaker.engine.coverage`` holds the profile and the generators, and
  the supply chain pack applies it to suppliers, plants, warehouses and
  customers. Two things beyond the rates. An identifier that describes a
  place belongs to the place, so two companies at one door have the same
  coordinates and the same Placekey ``where``; drawing them per company would
  hand a resolver a key that address reuse cannot confuse, which is the
  opposite of the trap the shared addresses exist to set. And which addresses
  are covered is drawn in proportion to how many companies sit at them, both
  because the register's rate is a share of records and a coin flip per
  address misses it by several points when a handful of agent addresses hold
  half the rows, and because the bias is the right way round: an address with
  ninety thousand companies registered at it is a building someone has
  certainly geocoded.

  The rates are measured; the formats are not. An LEI has the right shape and
  correct ISO 7064 check digits but is registered to nobody, an NPI passes
  the Luhn check real ones do and sits in the organisation range, and a
  Placekey's ``where`` is not a real H3 cell. The Senzing export carries all
  of them as the features a register writes. It no longer writes a supplier's
  tier as ``OTHER_ID_NUMBER``: that put a modelling attribute on 11% of
  records where a register has an identifier on 1.9%, and handed a resolver a
  feature that correlates with the answer and exists nowhere in the world.

* The semantic layer's first half: ``graphfaker schema draft "<description>"``
  turns a sentence into a schema YAML. ``pip install graphfaker[llm]``.

  The architecture is one sentence and the rest follows from it: **the model
  compiles intent and the engine owns generation**. Ask a model for ten
  thousand transactions and you get ten thousand plausible rows with no
  process behind them, no degree distribution, no repeat partners, no tail
  and no way to make the same dataset twice. What the engine cannot do is
  work out what you meant by "a hospital with patients, doctors and
  admissions", and that is the only part of this where a model beats the
  alternative.

  So a model never runs inside ``generate``. It runs in ``schema draft``,
  which writes a file you read, edit and commit, and the dataset is then a
  pure function of that file, the seed and the shard size exactly as before.
  Two drafts of the same sentence will not agree; two generations from the
  drafted file always will.

  What comes back is validated before anybody sees it, and the pydantic
  errors go back to the model once, because the failures are mechanical
  almost every time: a sampler kind that does not exist, an edge pointing at
  a node type renamed halfway through the answer. After a second failure it
  prints what it was reaching for and writes nothing. The prompt's list of
  sampler kinds is read off the pydantic models and its worked example is
  the social pack's own schema, so neither can drift from what the engine
  accepts. A provider is anything with a ``complete`` method; the two that
  ship are Anthropic and an OpenAI-compatible client, which covers the
  hosted APIs and anything speaking the same protocol locally.
  ``docs/semantic-layer.md`` has the whole argument.

* A project ``.env`` is read on the way in, so an API key already sitting
  next to the code does not have to be exported again to run one command.
  The nearest file from the working directory up to the repository root, so
  it is found from a subdirectory and a stray file outside the repo is not;
  ``--env-file`` points somewhere else. Anything already in the environment
  wins, because a key exported on purpose and a CI job's secrets should not
  lose to a file somebody forgot about. ``graphfaker info`` says which file
  was used and which keys are present, and nothing ever prints a value.
  Thirty lines rather than a dependency: the parts of the format that are
  genuinely hard are the parts nobody puts an API key in.

* The Anthropic provider no longer asks for a temperature, which the
  current models will not take. They removed the sampling controls outright
  and replaced them with ``output_config.effort``, so the client raised
  ``Messages.create() got an unexpected keyword argument 'temperature'``
  before anything reached the network: a near-zero temperature was not a
  conservative default, it was a request that could not be made. It now
  sends adaptive thinking and ``effort: high``, and streams, because
  thinking makes a request take long enough that a non-streaming call can
  hit the client's own timeout.

  ``temperature`` is gone from the provider protocol with it. One provider
  still takes it and sets its own; it is not a parameter every provider
  has. Both providers now send the current request shape and drop whatever
  the far end names in its rejection, which covers an older model that has
  no effort level and an older installed client that has never heard of
  one. Picking the shape from the model's name would mean a table of model
  names that is wrong the week after it is written.

* A seeded dataset is the same tomorrow. ``date_of_birth`` and ``date``
  count backwards from today and were reading the real clock, so
  ``graphfaker generate fraud --seed 42`` produced a different birth-date
  column every day: reproducible across machines and not across midnight,
  which is the half nobody checks. They now count from
  ``graphfaker.engine.fastfaker.REFERENCE_DATE``, a constant. The supply
  chain and coordination packs never had the problem, because they take an
  explicit ``period_start``, which is the same idea written down.

  Found while checking that a drafted schema generates the same bytes twice,
  which is the sort of thing the semantic layer makes much more likely to be
  noticed: nobody re-runs a pack on two different days and diffs it, and the
  one test that would have caught it was written the same afternoon it would
  have started failing. ``tests/test_seed.py`` now moves the clock eleven
  years and asserts every pack is unmoved, so a drift of one day in one
  column fails it.

  Ages are relative to that date, which is the cost of having an answer at
  all, and moving it is a release decision rather than a maintenance one.

* ``graphfaker validate`` warns when a schema reads the clock.
  ``end_date: now``, ``start_date: -2y`` and providers like ``past_date``
  are relative to the day the generator runs, so the same seed gives
  different data tomorrow. The engine's own date providers are frozen (see
  above) but it cannot honestly freeze somebody else's module, so it names
  the attribute instead. A warning rather than an error: a schema is allowed
  to want the clock, it is just rarely what anyone wants from a seeded
  generator, and finding out by diffing two runs a day apart is a bad way to
  find out.

* An expression sampler is checked for names it will actually have.
  ``expr`` was validated by compiling it, so ``datetime.timedelta(days=1)``
  passed ``graphfaker validate`` and raised a ``NameError`` a minute into
  generation. Every free name is now checked against what the scope will
  hold, and the error says what is available. ``timedelta`` was added to
  that scope, so "discharged some days after admission" can be said at all;
  deliberately not the whole ``datetime`` module, because ``now()`` in a
  schema is a dataset that changes every run.

* An expression that does not parse is a validation error rather than a
  crash. A ``SyntaxError`` walks straight out of a pydantic field
  validator, so a schema with a typo in an expression killed the command
  that was supposed to report it, and anything catching schema errors never
  saw it.

* Quoting a date in a schema file no longer changes what it means. YAML
  parses an unquoted ``2024-01-01`` into a date and a quoted one into a
  string, and Faker's date range arguments take a date or one of its own
  relative strings and raise on anything else, so the same schema worked or
  did not depending on a pair of quotes, and the failure arrived halfway
  through a run rather than at validation. A written ISO date is now read as
  a date wherever it reaches a Faker provider.

* The vectorised Faker path falls back instead of failing. ``supports``
  looks at the provider, but a provider's format expands to tokens, so
  ``address`` passed the check and then died rendering ``{{state_abbr}}``,
  which the fast path has no table for. A schema Faker itself handles fine
  killed the run. The fast path is an optimisation and Faker is the
  definition, so an unsupported corner is slow rather than fatal now. No
  shipped dataset moves: the only schemas affected produced nothing at all.

* A schema with an explicit ``null`` now round-trips.
  ``GraphSchema.to_yaml`` dropped every ``None`` to keep a written schema
  readable, which is right for a field whose default is already ``None`` and
  wrong for one whose default is not: writing
  ``topology: {prominence: null}`` and reading it back brought the default
  attribute name ``prominence`` straight back, so the file said one thing
  and loading it meant another. No shipped schema set one, so no digest
  moves, but a schema that does not round-trip is a dataset that cannot be
  regenerated from its own file, which is the one promise the manifest
  makes. Found by a drafting test, which is the sort of thing the semantic
  layer makes much more likely to happen.

1.2.0 (2026-10-11)
------------------

A supply chain domain pack, the pattern machinery lifted out of the packs
into the schema and the engine, and a register read in and written back out:
real company data measured into a reference, and generated data exported as
an entity resolution dataset with its answer key.

Datasets change for a given seed in the ``supply_chain`` pack, which gained
addresses, register-shaped names and people. The ``fraud``, ``social`` and
``coordination`` packs are byte-identical to 1.1.0: the refactor moved code,
not draws.

The supply chain pack: a multi-tier supplier network, the orders, shipments,
invoices and deliveries on it, and labelled procurement patterns with
legitimate structures that leave the same trace.

* ``graphfaker generate supply_chain`` produces suppliers in three tiers,
  plants, warehouses, customers, products and carriers; the contracts, tier
  structure and lanes between them; and an event stream of scheduled
  replenishment, seasonal ad hoc demand, shipments against promised lead
  times, invoices behind the goods, inter-company movements and outbound
  deliveries.
* Four labelled plays, each paired with a legitimate structure that leaves
  the same trace: ``phantom_supplier`` with ``disruption_cascade``,
  ``invoice_kiting`` with ``consignment_loop``, ``split_orders`` with
  ``blanket_calloffs``, ``counterfeit_injection`` with ``quality_incident``.
  A port closure and a shell company produce the same ledger; a framework
  agreement and a split-order scheme produce the same histogram.
* ``hardness_report`` scores sixteen features in six families against the
  suppliers that trade, not against the dormant tail, because a procurement
  team ranks the suppliers it is buying from. ``evaluate`` scores at
  supplier, event and pattern level with ``legitimate_false_positive_rate``
  reported separately.
* The measurement found four label leaks while the pack was being written,
  each of which would have made a play findable by one column: legitimate
  invoices were never round numbers, every legitimate invoice had a shipment
  behind it, inter-company movements only ever ran down the chain, and plays
  recruited suppliers that had no contract and therefore no ordinary
  traffic. All four are fixed in the process rather than in the features.
  A fifth was in the dial itself: ``activity_camouflage`` was stripping a
  member's ordinary trading, borrowed from the fraud pack's mule accounts,
  and a supplier with no business is not hiding. It now controls
  displacement instead, thinning a member's ordinary traffic while its
  pattern runs, so a scheme is done instead of part of the work rather than
  on top of all of it. Mean best single-feature AUC across the catalogue
  now falls 0.853 / 0.823 / 0.814 across the three levels, where before the
  fix ``high`` was the easiest setting.
* A threshold rule, the first thing anyone writes, finds every split-order
  scheme, misses the other three plays completely, and flags 42% of the
  suppliers running ordinary blanket agreements.
* ``docs/domains/supply-chain.md``, and a notebook,
  ``docs/notebooks/supply_chain_investigation.ipynb``, that writes the four
  detectors a procurement team would write and scores each one against the
  truth and against the innocent suppliers it accuses. The threshold rule
  finds every split-order scheme and flags 36% of the legitimate blanket
  agreements; the unmatched-invoice rule finds every phantom supplier and
  flags 86% of the suppliers caught in a port closure; "on any cycle" flags
  every supplier that trades, because goods legitimately move in loops; and
  a tightened cycle query that wants the same value passed round inside
  three weeks finds no rings at all. All four together reconstruct 8 of the
  10 schemes and accuse 4 legitimate structures.

A real register, and what measuring one changed.

* Organisations share addresses, in the proportions a register does.
  ``graphfaker.engine.addresses`` allocates a pool across rows on a Zipf
  curve calibrated against a real company register of 2,026,444 records:
  the median address holds one organisation, the 90th percentile four, the
  top 1% of addresses over half of everything. Giving every company its own
  address makes deduplicating on address work perfectly, which is the
  opposite of what a corporate dataset is for. The supply chain pack's
  suppliers, plants and warehouses now draw from one shared pool and its
  customers from a nearly unique one. ``REGISTER``, ``RETAIL`` and
  ``UNIQUE`` are the three profiles, and ``alpha`` rather than the mean is
  the dial that decides how shared an address is. Addresses draw from a
  stream of their own, so every other draw in a run stayed where it was:
  a supply chain dataset for a given seed is the one 1.2.0 would have
  written with the address columns added, not a different dataset.
* ``benchmarks/realism/corporate.py`` measures what a generator cannot
  invent about a population of companies, streaming a register in Senzing's
  format into aggregate statistics and scoring a GraphFaker run on the same
  metrics. The reference files beside it are counts only. It found four
  gaps, of which addresses are the first fixed; names that collide,
  attribute coverage and officer density are in the plan.
* A second reference, from a second register, because a shape measured once
  is a property of a file and a shape measured twice is a property of the
  world. ``corporate-nevada.json`` comes from the national open data export
  of 2026-03-05: 330,553,048 records in 2,493 shards, one pass in 150
  minutes at 37,000 records a second. It agrees with the Las Vegas file it
  has no records in common with. Companies per address: median 1 against 1,
  90th percentile 4 against 4, busiest address 99,794 against 99,522, top 1%
  of addresses holding 51.0% of everything against 53.7%. Names already
  taken with the legal form stripped: 90.3% against 91.8%. LLC: 52.1%
  against 54.4%. The measurement takes ``--state`` for this, because the
  distributions that need one counter per entity cannot be held over 330
  million records while the category mixes can, and every output file now
  carries a ``scope`` saying which numbers had which treatment. Two things
  the national pass adds: identifier coverage over everything (geo 67.3%,
  postcode 61.9%, LinkedIn 33.1%, Placekey 29.2%, website 14.7%, LEI 0.06%),
  and the fact that only 46,201 of Nevada's 1,033,773 companies have a
  single person attached, with a mean of 14.5 and a maximum of 68,945 among
  those that do. Board density has a floor of zero, which a generator that
  gives every company an officer gets wrong twice.
* The address model is confirmed rather than retuned by that. It reproduces
  the mean, the median and the 90th percentile of both registers and still
  runs hot in the tail (p99 about 40 against 31 and 27), and a sweep across
  the exponent shows why: one Zipf curve sets the mean and the tail with the
  same number, and the exponent that fixes p99 puts the mean at 3.7. Two
  components, a head of registered agents over a nearly unique body, is the
  fix, and it is in the plan rather than quietly tuned.
* ``graphfaker gen --fetcher senzing --path register.jsonl`` loads a
  register: organisations, locations and people, with pointer roles becoming
  ``BRANCH_OF``, ``HEADQUARTERS_OF``, ``SITE_OF``, ``EXECUTIVE_AT`` and
  ``CONTACT_AT`` edges. Streamed, with ``--limit`` for part of a file.
  GraphFaker ships no register; a loaded one is personal data.
* ``--path`` takes a directory or a zip as well as a file, because a
  register of any size arrives sharded: the national open data export is
  2,493 JSONL files in one archive, 330,553,048 records and 158 GB
  unpacked.
  The company shards are read first so a pointer resolves as it arrives,
  decided by reading one line of each shard rather than by its name: every
  location shard in that archive is called
  ``bq_organization_locations_...``, so a name heuristic put 1,988 pointer
  shards ahead of the 500 company shards and resolved nothing. ``--state``
  and ``--city`` keep the companies registered in one place together with
  their locations and officers wherever those live, which is how an archive
  that cannot be one graph becomes one.
* ``graphfaker register PATH`` says what a register contains before a load
  spends an hour on it: record types, pointer roles, the attributes on each
  kind of record with their coverage, and a list of what a load would not
  keep. It streams and holds nothing, and a ``--limit`` is spread across the
  shards rather than spent on the first one, so half a million records out of
  330 million answers the format question in half a minute. Running it
  against the national export found three things worth fixing in the loader:
  an attribute can appear twice on a record (552 companies in every half
  million carry a second ``NAME_ORG``, an alias, now kept as
  ``NAME_ORG_ALSO``), ``GEO_LATITUDE`` is a float in the location shards and
  a string in the company shards (coerced), and a person with no name
  carries ``NAME_FULL: null`` (dropped, so coverage counts mean what they
  say). ``NAME_MIDDLE``, ``BQ_ID`` and the ``GROUP_ASSN_ID`` pair were
  attributes the loader had never seen and silently dropped.
* A register loads into a database, not only a file. ``graphfaker register
  PATH --out DIR`` writes it as node and edge tables, one frame per record
  type and one per relationship, with a manifest; ``--sink duckdb``,
  ``ladybug`` or ``neo4j-admin`` does the database in the same step, and
  every existing ``graphfaker load`` and ``graphfaker verify`` command works
  on the directory afterwards. No ``truth/``, because nobody labelled a
  register, and no ``schema.yaml``, because a schema says how to generate
  something and this was read. The whole Las Vegas register becomes tables in
  75 seconds and a DuckDB with a property graph in another 85: 1,668,368
  organisations, 358,076 people, 1,394,598 relationships, and the top of
  ``GROUP BY address`` is one PO box with 99,524 companies at it, with
  ``3225 Mcleod Dr`` and ``3225 McLeod Dr Ste 100`` as separate rows a little
  below.
* A real register loads into LadybugDB. A relationship in one does not
  always run between the same two kinds of node: in the Las Vegas open data
  ``SITE_OF`` runs organisation to organisation 814,029 times and person to
  organisation 5,079 times, because a few thousand people are filed as a
  company's location, and ``HEADQUARTERS_OF`` does the same 2,089 times. The
  sink declared one pair per relationship, so the whole register wrote itself
  to Parquet and then died on the database with "unable to find primary key
  value". It now declares every pair a relationship actually connects and
  copies each pair separately, which is what LadybugDB wants. The 2,026,444
  records of that file become 1,668,368 organisations and 358,076 people in
  36 seconds. ``infer_endpoint_pairs`` is the new shared helper, ordered
  commonest first so a sink that can only express one pair loses the least.
  GraphFaker's own datasets never produce this shape, which is why nothing
  caught it until a real register went in.
* A dataset with no edges no longer breaks the DuckDB property graph.
  ``EDGE TABLES ()`` is a syntax error, so a cut of a register that happens
  to contain only companies loaded every table and then failed on the last
  statement. It gets a vertex-only property graph now.
* The realism measurement takes ``--workers``. The shards are the unit of
  work and every counter adds, so the pass over the national export went from
  150 minutes in one process to 26 in twelve, and the result is identical on
  every measured key. Three things made the single-process pass faster as
  well: ``orjson`` when it is installed, which halves the 59% of a pass that
  is spent parsing JSON; one zip handle for the whole archive instead of one
  per shard, which was 64 seconds of re-reading a central directory before
  the first record; and building an address key only for the records a filter
  kept rather than for all 227 million organisation records. ``orjson`` is
  optional and nothing changes without it.
* Company names come from a register's distribution, not Faker's.
  ``graphfaker.engine.names`` generates the measured legal forms (LLC 53.9%,
  INC 20.5%, none 11.9%, ``L.L.C`` 2.6%, ``CORPORATION`` 2.4%) with the
  measured comma convention, which is a coin flip, and holds name reuse at
  the register's rate instead of letting it run away with scale. The supply
  chain pack's suppliers, plants, customers and carriers use it; a warehouse
  is a site rather than a legal entity and keeps its place name.
  ``names.variants`` writes one company's name the several ways a register
  writes it, which is what an entity resolver actually faces.
* Officers and contacts, on the companies that have any.
  ``graphfaker.engine.people`` attaches people to companies the way a
  register does, and the first measured fact is that most companies have
  nobody: 4.5% of a register's companies have a single person attached,
  46,201 of Nevada's 1,033,773 and 27,727 of Las Vegas's 631,846. Among
  those that do the median is one and the 99th percentile is 151, with the
  busiest holding a tenth of every person-company link in the register on
  its own, because the busy ones are filing agents rather than boards. Board
  density is a distribution with a floor of zero, and a generator that gives
  every company a board gets the common case and the tail wrong at the same
  time.

  The supply chain pack grows a ``Person`` node type with ``CONTACT_AT`` and
  ``EXECUTIVE_AT`` relationships, the same vocabulary the loader produces for
  a real register, so a generated graph and a loaded one answer the same
  query. Roughly one person in six is attached to more than one company,
  which is where "shares a director" comes from. The role mix is the
  register's (``Contact`` 79%, ``Executive`` 21%), the name parts are
  recorded separately on 23% of people as they are on 23% of a register's
  person rows, and people carry a home address.

  People per company uses a rank curve rather than interpolated quantiles,
  for the same reason addresses do: it hits the measured mean exactly at
  every scale and keeps the concentration, where interpolating between a 99th
  percentile of 151 and a maximum of 68,945 puts so much weight on the head
  that the mean goes with it. At the register's own 46,201 attached companies
  the busiest holds 10.2% of every link against a measured 10.3%, and the top
  1% hold 63% against 61%. The two remaining realism rows in
  ``benchmarks/README.md`` are no longer "not modelled".
* ``graphfaker generate ... --sink senzing`` writes an entity resolution
  dataset: records in the format Senzing takes, and the answer in a second
  file. Entity resolution has no public benchmark worth the name because the
  data cannot provide one, a register having no answer key and a labelled
  dataset usually being too clean to be hard. This writes
  ``senzing/records.jsonl``, where a company appears several times under
  several spellings and at addresses it shares with other companies, and
  ``senzing/entities.parquet``, which says which records are one company or
  one person.
  Nothing in the records names the entity. ``read_gold`` turns the answer
  into the clustering shape ``evaluate_clusters`` takes, so a resolver can be
  scored in two lines.

  Every hard part is a measured one. Names vary over a company's records at
  the rates a register writes them; addresses are shared between companies on
  the measured curve; a company's own records are mostly at *different*
  addresses, because a branch being elsewhere is why a register holds a
  record for it; and 1% of name stems really do belong to two different
  companies. Scored with ``graphfaker.resolve`` on 4,409 records for 2,207
  companies: the name alone gives 0.93 F1, name and address together give
  0.955 precision at 0.119 recall, and the address alone fails in both
  directions at 0.058 precision and 0.118 recall. A dataset of unique names
  and unique addresses would have called all three a success.

  What is written round-trips: the pointers in the records reconstruct the
  answer key exactly through ``SenzingFetcher``, which is the test that it is
  a Senzing dataset rather than a JSON file with the right field names. See
  ``docs/senzing.md``. People, missing attributes and typos are not in it;
  the first two are measured gaps with numbers in the references and the
  third is deliberate.
* ``docs/notebooks/register_resolution.ipynb``: the paired entity resolution
  experiment, which is what the measuring and the generating were both for.
  It resolves companies on a real register, where there is no answer key and
  so no score, and on a generated dataset carrying the register's measured
  names and addresses, where ``evaluate_clusters`` can say what the resolver
  got. The simplest configuration wins: the name alone, blocked on its stem,
  at 0.967 precision and 0.932 F1. Blocking on the name instead of the stem
  does six times the comparisons for the same answer, because 54% of
  companies end in ``LLC`` and the legal form is a token like any other.
  Adding the address takes precision to 0.763, because companies share
  addresses here at the rate a register says they do, and matching on address
  alone takes it to 0.03 with one cluster of 231 records covering 86
  companies at one door. That is the registered agent, and the reason the
  address work exists. On 5,000 real register records the same resolver
  returns 57 clusters and no score at all. The real-register section is
  optional (``GRAPHFAKER_REGISTER``) and prints counts only.
* **A correction.** The 1.1.0 notes and the benchmark README said different
  companies share a name 86.7% of the time against our 1.4%, and concluded
  our names were two orders of magnitude too unique. The counting was wrong:
  a location record carries its parent's name, so a company with five sites
  writes its name five times, and that is what the 86.7% was. Counting
  companies only, 0.14% of names and 1.02% of stems are shared, and the
  generator's old names were wrong in the *other* direction: 10.3% repeated
  at 200,000 rows with one stem on 1,451 companies. The measurement now
  reports the two separately, the comparison puts companies against
  companies, and the generated rate holds from 500 rows to 200,000.
* The benchmark's name normaliser never stripped ``L.L.C``. It removed
  punctuation first, which turns the spelling into the three tokens ``L L C``,
  so the one variant the whole exercise is about was the one that got through
  and ``ACME, L.L.C`` counted as a different company from ``ACME LLC``. The
  trailing form is matched before the punctuation goes now, which moved the
  measured stem reuse from 0.97% to 1.02%, and ``tests/test_names.py`` fails
  if the package's definition and the benchmark's stop agreeing.
* Legal forms and name tokens are counted per company rather than per record,
  for the same reason as the collisions above.
* The reference files say where they came from and under what terms.
   ``benchmarks/realism/*.json`` carry a ``provenance`` block and a
   ``licence``, and ``corporate.py`` takes ``--licence`` to record it. Both
   references are measured from OpenData.org's U.S. entity dataset, whose
   terms put it under CDLA-Permissive-2.0: computed output carries no
   obligations under section 3.1, which is what those files are, while
   sharing the data itself carries the section 2.1 condition to pass on the
   agreement text, which is one reason GraphFaker ships no register. A
   licence is not a privacy basis, so the rule is unchanged: aggregate
   statistics travel, records do not.
* ``graphfaker gen --export`` no longer fails on a Windows console. It
  printed a check mark when it was done, which is not in cp1252, so the
  command wrote the file and then died with a ``UnicodeEncodeError``. The
  Wikipedia export did the same.

1.1.0 (2026-10-04)
------------------
The coordination domain pack: a social platform, and the coordinated behaviour
inside it.

.. code-block:: sh

   graphfaker generate coordination --scale 0.001 --tradecraft medium --seed 42 --out ./platform

5,000 accounts, 50 topics, 93,000 follows, 147,000 posts, reshares and
replies, and 22 campaigns of which 6 are organic. ``--tradecraft`` decides how
well the inauthentic ones hide, and the hardness report says what that bought:

.. code-block:: text

   tradecraft: medium
   playbook                kind               n   max AUC  best feature (family)
   follow_farm             coordinated       29     0.898  clustering_proxy (structure)
   amplification_ring      coordinated       23     0.898  topic_concentration (content)
   sockpuppet_cluster      coordinated       12     0.895  device_shared_with (identity)
   mutual_follow_community organic           30     0.892  reciprocity (structure)

The fourth row is the point of the pack. ``mutual_follow_community`` is a
group of people who genuinely know each other, labelled
``is_coordinated=False``, and it is as hard to tell apart from a follow farm
as the follow farm is to find. A detector that fires on tight reciprocal
clusters scores well on this dataset and suspends a fan club on a real one.

* Every database sink reads the ground truth in the domain's own words. The
  truth layout is the same in every pack (patterns, memberships, labelled
  events, latent factors) and each pack names it after its own subject: the
  fraud pack writes ``patterns`` with ``pattern_id``, ``typology`` and
  ``is_fraud``, the coordination pack writes ``campaigns`` with
  ``campaign_id``, ``playbook`` and ``is_coordinated``. The sinks read those
  names literally, so a coordination dataset could not be loaded into DuckDB or
  LadybugDB at all and loaded into Neo4j with its truth silently missing.
  ``graphfaker.sinks.truth`` now resolves the layout by shape, the way the
  PyTorch Geometric sink already did, and the loaders, the property graph and
  the verifier all read from it. A bank's edges still gain ``typology`` and
  ``is_fraud``; a platform's gain ``playbook`` and ``is_coordinated``; the node
  label and the relationship type are ``Pattern`` and ``IN_PATTERN`` in both,
  because that is what every query in the documentation is written against. A
  coordination dataset now passes 105 checks in DuckDB and 107 in LadybugDB,
  and tests cover both so this cannot come back.
* ``Pattern`` and the injection driver moved into the engine, and a domain's
  pattern catalogue into the schema. ``graphfaker.schema.PatternCatalog`` and
  ``PatternSpec`` declare what a pack injects: every shape with its share of
  the budget, its natural span, and, for a decoy, the shape it imitates.
  ``Camouflage`` is the set of dials every pack has (signature blend, timing
  spread, overlap, decoy ratio, activity camouflage, size scale), which
  ``HardnessProfile`` and ``TradecraftProfile`` now subclass and rename to
  their own vocabulary. ``graphfaker.engine.injection`` holds the shared
  bookkeeping: the pattern record, the recruitment ledger, the budget
  allocator, the decoy orders and the driver loop that runs a catalogue. The
  fraud and coordination packs keep their own drawing and lose about 200 lines
  of duplicated machinery between them, including two byte-identical copies of
  the budget allocator. Datasets are unchanged: the same seed gives the same
  fingerprint in both packs at every hardness and tradecraft level.

The social domain has been a ``GraphSchema`` preset since 0.5 and nothing more:
entities and a topology, one latent factor as its only ground truth, and no
tests. The fraud pack, by contrast, has a temporal process, a labelled pattern
catalogue, a measured hardness dial and a scoring harness. ``coordination``
gives the social side the same treatment, as a domain of its own rather than by
changing ``social``, because the graph is a different one: accounts, topics and
devices rather than people, places and products.

* ``graphfaker generate coordination`` produces accounts, topics and devices;
  a follow graph with heavy-tailed in- and out-degree, ~50% reciprocity and
  recoverable communities; and an organic event stream of posts, reshares and
  replies with a diurnal rhythm and a lurker majority.
* Eight labelled inauthentic playbooks: ``copypasta``,
  ``amplification_ring``, ``reply_brigade``, ``follow_farm``,
  ``hashtag_flood``, ``sockpuppet_cluster``, ``astroturf_campaign``,
  ``account_handover``. ``follow_farm`` is there because an event-only detector
  cannot see it at all, and ``recall_by_playbook`` makes a one-signal detector
  visible.
* **Organic decoys, which are the point.** Coordinated behaviour has a strong
  legitimate twin: a fandom reacting to a release, a city reacting to an
  earthquake and a paid amplification ring are structurally the same thing. A
  dataset whose only labelled structures are inauthentic rewards any detector
  that fires on synchrony, which is the detector that suspends fan clubs.
  ``fandom_burst``, ``breaking_news`` and ``mutual_follow_community`` are
  generated by the same machinery, labelled ``is_coordinated=False`` and
  counted as false positives when flagged. Each is matched to its twin on the
  properties that are not the point, chiefly posting volume.
* ``tradecraft`` (``low`` / ``medium`` / ``high``) is an input to a
  measurement, not an assertion. Mean per-playbook best-single-feature AUC
  falls 0.984 / 0.852 / 0.789, and the evidence that still works broadens from
  identity and timing to structure and content.
* ``hardness_report`` scores sixteen features in five families and reports
  which family found each playbook. ``decoy_separability`` scores each organic
  structure against the playbook it imitates, and names the feature
  responsible, because a decoy separable by one feature is not a decoy.
* ``evaluate`` scores a detector at account, event and campaign granularity,
  with ``organic_false_positive_rate`` reported separately: on a real platform
  the cost of suspending a fan club is not symmetric with the cost of missing
  one ring.
* ``examples/coordination_detectors.py`` scores five rules a platform would try
  first, at all three tradecraft levels. The conclusion matches the fraud
  pack's Cypher cookbook: single-signal detectors do badly. The best F1
  anywhere is 0.499, at the easiest setting, and the co-occurrence rule goes
  from 0.843 precision with no organic false positives at ``low`` to 0.118
  precision and 15% of organic accounts flagged at ``medium``.
* No node attribute names the answer, and a test asserts that no single feature
  separates any playbook perfectly — a perfectly separating feature means a
  label has leaked into the graph. It caught two such leaks during development:
  account handover left ``reshare_count`` at exactly zero, and every sockpuppet
  cluster was separable at AUC 1.000 by device sharing until organic device
  sharing was given a tail of its own.
* 43 tests, including the pack's own thesis as an assertion: a co-occurrence
  detector must flag organic bursts, or the decoys are not doing their job.
* A PyTorch Geometric export for the pack, via the existing ``pyg`` sink:
  ``--sink pyg``, ``to_hetero_data`` and ``from_directory`` all work on a
  coordination dataset. Account carries ``y`` (coordinated), ``decoy``
  (organic) and stratified splits; ``POSTED``, ``RESHARED`` and ``REPLIED``
  carry ``y`` and ``edge_time``; ``community`` is a latent tensor rather than a
  feature.
* The sink's label rules are now a convention rather than the fraud pack's
  column names: a truth frame keyed by ``<entity>_id`` with a single boolean
  column labels those entities. ``accounts.is_fraud`` and
  ``accounts.is_coordinated`` both work, and a third domain following the same
  shape gets labels without touching the module. Identifiers and foreign keys
  are kept out of edge attributes — the coordination pack's interaction edges
  carry the topic they are about, which one-hot encoded to 48 columns and would
  reach thousands at scale. Matching a label frame on values alone picked
  ``campaigns.topic``, which holds real Topic ids next to a boolean, and
  labelled every topic; the id column now has to end in ``_id`` as well.
* ``examples/coordination_pyg_baseline.py``: a heterogeneous GraphSAGE against
  a logistic regression on account features alone, at all three tradecraft
  levels. The graph helps everywhere — average precision more than doubles at
  ``medium``, 0.101 to 0.243 — and it **flags three times as many organic
  accounts**, 4.0% against 12.0%, because it buys its power by learning
  "tightly connected group acting together" and a fan club is exactly that.
  Measuring AUC alone says "graphs win", which is true and incomplete; that gap
  is what the organic decoys exist to make visible. The baseline reports the
  organic share at a fixed operating point, so two models with different score
  distributions compare fairly.
* ``docs/domains/coordination.md``; ``docs/pyg.md`` covers both packs.

Also in this release, and recorded here rather than under a 1.0.1 that was
never tagged:

* Dependency floors raised for three advisories the audit flagged: ``urllib3>=2.8.0`` (PYSEC-2026-4175, 4176, 4177), and in the ``examples`` extra ``notebook>=7.6.3`` (PYSEC-2026-4112) and ``jupyterlab>=4.6.4`` (PYSEC-2026-4055, 4056, 4057). Nothing in GraphFaker's own code changed.
* The social topology model is 30 times faster and no longer quadratic in the graph. Edge building asked networkx for ``number_of_edges()`` twice per attempt to count its budget, and that call sums the degree of every node, so the cost grew with nodes times edges: at 2,000 nodes and 12,000 edges it was 96% of the run. The count now comes from the add itself, which is a dict lookup. ``social`` at 2,000 nodes and 12,000 edges went from 22.4 s to 0.65 s, 20,000 nodes and 122,000 edges takes 6.8 s, and a million edges is about 105 s where it was not practical before. Datasets are unchanged: same seed, same fingerprint.

1.0.0 (2026-09-23)
------------------

The first stable release. The changes below landed after 0.6.1; what makes this 1.0 is the promise attached to them.

**What 1.0 means.**

* The public API is stable for 1.x: ``GraphSchema`` and its samplers, ``generate``, ``GraphRun``, ``GraphTables``, ``Manifest``, ``fingerprint``, the domain registry, the sinks, the metrics and ``GraphFaker`` itself. Names are added, not removed or changed in meaning; anything to be removed is deprecated first and kept for the rest of 1.x. The documentation is the contract: if the docs and the code disagree, the code is wrong.
* Reproducibility is scoped to a version. Within one release of GraphFaker, a dataset is a pure function of the schema (or the domain and its options), the seed and the shard size: the same inputs give the same bytes on any machine, at any number of workers, under any hash seed. Across releases the bytes may change, because a faster or more realistic generator draws differently, and ``manifest.json`` records the version that wrote a dataset. Every release that changes the bytes says so in this file. Pin the version alongside the seed when a dataset has to be regenerated exactly, in a paper or a benchmark.
* There are two entry points and both are supported. ``graphfaker generate`` / ``graphfaker fraud`` and ``graphfaker.engine.generate`` are the schema engine: they write tables, ground truth and a manifest, and they are what the rest of the documentation is about. ``graphfaker gen`` and the ``GraphFaker`` class are the NetworkX-facing path: they load the real-world sources (OpenStreetMap, flights, Wikipedia), produce a quick in-memory social graph, and export a single file in GraphML, CSV, Cypher, openCypher or GQL. Use the engine for datasets you will load or train on; use ``gen`` for a real-world network or a one-file export.

**In this release.**

* ``graphfaker generate --schema my_graph.yaml`` generates from a schema file, no domain needed; ``--shard-size`` sets the rows per shard for such runs. ``graphfaker schema <domain> [--key value] [--out FILE]`` writes a domain's schema as YAML with its options applied, to edit and generate from. The fraud domain prints its entity schema with a note that its transactions and patterns come from code. A domain may register a ``schema_note`` for that purpose. File-not-found, invalid YAML and schema validation problems are reported as such.
* A full-size bank fits a 16 GB machine. The transaction process builds each channel a block of accounts at a time (about four million events per block), the accounts a pattern stripped or opened late are filtered out of each block as it is produced, the channels are concatenated from those blocks without a copy, the global time order is one ``argsort`` over an int64 array, and the population keeps ids in the node tables' memory instead of as numpy objects. Peak memory at ``scale=1.0`` went from 32 GB to 15 GB, at ``scale=0.3`` from 10.1 GB to 5.2 GB, and generation got faster too (``scale=1.0`` in 7 minutes single-process, ``scale=0.3`` 122 s to 95 s). Edge files are no longer sorted by timestamp: rows are in generation order and ``tx_id`` carries the rank in time across channels. Datasets change for the same seed once more (the ad hoc process draws per block).
* ``osmnx`` moved out of the base install into an ``osm`` extra. It brings geopandas, shapely, pyproj and pyogrio, about 150 MB that only the OpenStreetMap fetcher uses. ``pip install "graphfaker[osm]"`` restores it; without it ``graphfaker gen --fetcher osm`` and ``GraphFaker.generate_graph(source="osm")`` say so and name the extra. The ``examples`` extra includes it.
* ``graphfaker inspect DIR`` prints what a dataset directory contains from its manifest and file layout: schema, seed, shard size, versions, node and edge counts, the truth tables present, the Parquet size and the domain options; ``--json`` for scripts. ``graphfaker validate FILE...`` checks schema YAML files with the rules ``generate --schema`` applies, reports every file and exits non-zero if any fails. ``generate`` and ``fraud`` take ``--quiet`` (``-q``, warnings and errors only) and ``--json`` (the manifest, and for ``fraud`` the hardness and realism reports, as one JSON document on stdout; logging is on stderr); ``evaluate --json`` prints the scores as JSON. ``HardnessReport.as_dict()`` is the data behind it.
* ``graphfaker --version`` (``-V``) prints the version. ``graphfaker info`` prints it with the engine version, Python and platform, the versions of polars, numpy, pyarrow, faker and networkx, and which extras (``neo4j``, ``duckdb``, ``ladybug``, ``pyg``, ``osm``) are installed and what each enables. ``ladybug`` is an extra of its own now, next to ``neo4j`` and ``duckdb``. The ``pyg`` extra includes scikit-learn, which ``examples/pyg_baseline.py`` always needed.
* ``fingerprint()`` visits tables by name and sorts their rows, so a run read back from Parquet has the same digest as the run that wrote it. Digests from earlier versions do not carry over.
* The documentation site takes the GraphGeeks palette (navy ``#141846``, pink ``#D81B5A``, pale cyan ``#E2F3F7``, white ground), the landing page shows the same command against DuckDB, LadybugDB and Neo4j in turn, and every mention of unreleased work is gone: the pages describe what this version does.
* A Docker image, ``ghcr.io/graphgeeks-lab/graphfaker``, for ``linux/amd64`` and ``linux/arm64``: the CLI with the DuckDB and Neo4j clients, ``graphfaker`` as the entrypoint, ``/data`` as the working directory to mount a volume on, an unprivileged user. The release workflow builds it on every push, runs a generate, a load and a verify against the built image, and publishes it: ``main`` and a short sha from every push to ``main``, the version tags and ``latest`` from a ``v*`` tag. ``docs/get-started/docker.md`` has the commands, the memory note and a compose file with Neo4j.

0.6.1 (2026-09-16)
------------------

``--workers`` that pay for themselves.

* ``--workers`` now pays for itself from about ``scale=0.1`` instead of ``scale=1.0``. Measuring 0.6.0 by phase and by worker count (the Karp-Flatt analysis in ``docs/scaling.md``) showed the run behaving 80 to 90% serial against a 43 to 51% phase split; the difference was start-up, not work.
* One process pool serves the whole run. A node type with foreign keys used to get a pool of its own so the index could be installed once per worker; now the index goes to a temp file whose path travels with every shard job, and a worker loads it the first time it sees the path. ``Account`` at ``scale=0.1`` no longer pays 5 s of start-up for 2 s of work.
* A node type below ``PARALLEL_MIN_ROWS`` (250,000 rows) stays in process whatever ``--workers`` says; below that the pool costs more than it saves.
* ``import graphfaker`` and ``import graphfaker.engine`` are lazy (PEP 562): the public names load on first use instead of pulling in networkx, pandas, the fetchers and every domain at import. A worker process now imports in 1.5 s warm instead of 2.9 s, and every CLI invocation starts faster. ``from graphfaker import GraphFaker`` and the rest work as before.
* Measured on the i7 laptop, four workers: ``scale=0.1`` 1.05x before, 1.45x after; ``scale=0.3`` 1.13x before, 1.52x after, against an Amdahl bound of about 1.6x. The output is byte-identical with or without workers, and a test now exercises the pool path with the threshold lowered.

0.6.0 (2026-09-16)
------------------

Every database we said the data would land in now takes it, verified; the same dataset trains a GNN; and a full-size bank (10M accounts, 90M transactions) generates in minutes rather than hours.

Databases:

* ``graphfaker load neo4j <dir>``: loads a dataset into a **running** Neo4j over Bolt with batched ``UNWIND`` writes: no stopped database, no staging files in the server's import directory, no ``neo4j-admin`` on the PATH, and it works against Aura. Roughly 16k rows/s, so about 80 seconds for the 1.34M rows of ``--scale 0.01``. The offline ``--sink neo4j-admin`` path is still there and is still the right answer above a few tens of millions of rows; ``docs/neo4j.md`` has the comparison.
* Ground truth is loaded as a subgraph rather than flattened onto nodes: ``(:Pattern)`` nodes, ``(:Account)-[:IN_PATTERN {role}]->(:Pattern)`` memberships, ``is_fraud``/``pattern_id``/``typology`` on the money relationship itself, and ``(:Region)`` nodes carrying each latent factor's generation parameters. Membership has to be a relationship because an account can belong to several patterns: 205 memberships across 181 accounts at ``--scale 0.01``.
* ``--blind`` loads the graph with none of that, so the same dataset can still be used as an unbiased benchmark. The usual arrangement is a blind database for whoever builds the detector and a truth-loaded one for whoever scores it.
* ``graphfaker verify neo4j <dir>``: treats the Parquet as the oracle and Neo4j as the thing under test, and exits non-zero when they disagree. Five families of check: per-label and per-type counts, constraints and endpoint labels and key uniqueness, a per-property aggregate scan (presence, sum, min, max) that catches coercion and truncation where counts cannot, sampled row-by-row round trips, and truth coverage. 154 checks on the ``--scale 0.01`` bank. ``load`` runs it automatically; ``--no-verify`` opts out.
* The verifier is tested against real breakage: the suite loads a dataset, injects one silent corruption at a time (a deleted relationship, a removed property, an altered amount, a relabelled node, unflagged truth) and asserts the specific check that must catch it. Integration tests are marked ``neo4j``, run in their own ``graphfaker-pytest`` database, and skip when no server is reachable.
* ``--sink neo4j`` on ``graphfaker fraud`` and ``graphfaker generate`` generates and loads in one step, configured from ``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` / ``NEO4J_DATABASE``.
* Database names are validated before the round trip: Neo4j allows no underscores, which is the first thing anyone types after naming an output directory ``fraud_data``.
* ``docs/neo4j.md``: the walkthrough, what the checks catch, and a Cypher cookbook for the laundering typologies in which every rule is **scored against the ground truth**. The measured result is that single-signal structural rules do badly. A fan-in rule with no time window runs at 0.3% precision, and a cycle detector catches every decoy ring and fewer than half the fraud rings. Adding one behavioural signal takes precision to 66.7%. ``examples/neo4j_detectors.py`` regenerates that table.
* New optional dependency group: ``pip install 'graphfaker[neo4j]'``.
* Fixed: relationship types with no attributes (most of the social graph) could not be loaded, because an empty polars struct is not constructible.
* ``graphfaker load ladybug <dir>`` and ``graphfaker verify ladybug <dir>`` bring the embedded LadybugDB / Kùzu sink to parity with Neo4j: the ground truth is loaded as a subgraph (``Pattern`` nodes, ``IN_PATTERN`` memberships with roles, ``is_fraud`` on the money relationships, latent-factor nodes) using the database's own ``LOAD FROM`` Parquet scan, ``--blind`` leaves it out entirely, and a dataset already on disk can be loaded without regenerating it. Closes issue #40.
* ``graphfaker.sinks.verify`` holds the load checks once, against ``GraphTables``, behind a small per-database adapter; Neo4j and LadybugDB share them, and the corruption tests run against both. A third sink gets verification for the price of the adapter.
* ``--blind`` on ``graphfaker generate`` and ``graphfaker fraud`` for the ``neo4j`` and ``ladybug`` sinks.
* A PyTorch Geometric export, ``graphfaker/sinks/pyg.py``: ``to_hetero_data``, ``write_pyg``, ``from_directory`` and ``--sink pyg``. Features are encoded per table (standardised numerics, 0/1 booleans, days for dates, one-hot for repeating categories; identifiers, foreign keys and latent factors kept out of ``x``), the truth becomes ``y`` and ``decoy`` on accounts and ``y`` plus ``edge_time`` on transactions, and stratified train/val/test masks are drawn from a seed. ``examples/pyg_baseline.py`` and ``docs/pyg.md`` show what the graph is worth to a detector: a GraphSAGE goes from AUC 0.99 to 0.85 to 0.56 across low, medium and high hardness while account features alone stay at chance.
* A DuckDB sink, ``graphfaker/sinks/duckdb.py``: ``--sink duckdb``, ``graphfaker load duckdb`` and ``graphfaker verify duckdb``. The dataset's tables land as DuckDB tables (DuckDB reads the Parquet itself), the ground truth the same way as in LadybugDB, and one ``CREATE PROPERTY GRAPH`` declares the graph for SQL/PGQ pattern queries through the DuckPGQ community extension. ``load.sql`` records the whole load. Loads scale 0.01 in 5 s and passes the same 154 checks. The ``duckdb`` extra pins the DuckDB release the extension is built for.
* The verifier's questions moved behind the ``Backend`` protocol: ``CypherBackend`` asks them in Cypher (Neo4j and LadybugDB inherit it), ``DuckDBBackend`` in SQL. The checks themselves are unchanged.
* The LadybugDB driver is now ``ladybug`` (LadybugDB's own package): the ``examples`` extra installs it, the docs and the tour notebook import it, and ``kuzu`` still works as a fallback because the API is the same. The driver probe forces the native library to load, so a wheel that cannot load (the 0.20 Windows wheel looks for OpenSSL 3 DLLs it does not ship) is reported and skipped instead of failing on the first query.
* The LadybugDB sink loads from memory: tables are bound to ``COPY ... FROM $df`` and ``LOAD FROM $df`` as Arrow, so nothing is serialised between generation and database. ``GraphTables.to_arrow()`` / ``from_arrow()`` make Arrow the interchange boundary. Scale 0.01 loads in about 10 seconds, half the file path.

Scale:

* Node attributes are drawn a column at a time. ``graphfaker.engine.fastfaker`` draws Faker providers (names, emails, phones, addresses, cities, companies, dates, uuids) from Faker's own locale tables with numpy, a column per call, keeping the vocabulary and the weights; the simple samplers (constant, category, uniform, gaussian, lognormal, poisson, bernoulli, reference) and foreign keys are vectorised too. Only expressions, mixtures, subcategories and the few providers without a vectorised form (``iban``, ``catch_phrase``) still go row by row. Scale 0.02 went from 230 s to 21 s, scale 0.1 (a million accounts, nine million transactions) from 165 s to about 60 s single-process, and scale 1.0 (ten million accounts, ninety million transactions) from over two hours to eight minutes with four workers, plus two minutes to write 2 GB of Parquet, at a 32 GB peak.
* Foreign-key indexes are int32 positions per group rather than lists of id strings, and a worker pool receives them once through a file in its initialiser instead of with every shard: at scale 1.0 the old form spent two hours pickling seven million customer ids a thousand times. Pattern recruitment draws members in O(1) instead of a set difference over every account, and decoys reuse the account pools instead of scanning for them per pattern.
* Memory: transaction frames are built with polars columns rather than numpy arrays of Python strings, the channels are merged one at a time with the time ordering computed on a narrow frame instead of a concatenation of everything, and Parquet is written in 2M-row chunks. The true peak (sampled, workers included) is about five times the final tables: 3.7 GB at scale 0.1, 32 GB at scale 1.0. Streaming the transaction process to disk is the next step.
* **Datasets change.** Runs are still a pure function of seed and shard size, but the values drawn for a given seed differ from 0.5.0's, because the attribute columns now come from the shard's numpy stream rather than Faker's and ``random``'s call sequences. Node counts, structure and distributions are unchanged; edge counts move by about 0.15% at ``scale=0.01`` and 0.02% at ``scale=0.1``, because pattern injection consumes its randomness differently and so draws a slightly different number of transactions.

Documentation:

* The documentation site is rebuilt on pydata-sphinx-theme with a landing page, a header with six sections (Get started, Guides, Domains, Databases, Reference, Project), the full page tree in the left sidebar, the page outline on the right, breadcrumbs, previous and next links, a light and a dark variant, and an API reference generated from the docstrings. The Install, first commands, Python usage, domains overview and command-line pages are cut from the README at build time so the two cannot drift.
* The site is light only, the LadybugDB driver in every example is ``ladybug``, and the old ``readme``, ``installation``, ``quickstart`` and ``usage`` pages redirect to their new homes.
* A Credits section names what was borrowed: gen-fraud-graph's scale convention, evaluator levels and output layout; AMLworld's typology catalogue; Data Designer as the reference point. The fraud pack has a one-paragraph abstract at the top of its page and its README section.
* The OSM tests carry the ``network`` marker; CI no longer depends on Overpass answering.

0.5.0 (2026-09-15)
------------------

GraphFaker becomes a generator of synthetic graph data that behaves like the real thing: you describe the graph you need, or pick a ready-made domain, and get entities, relationships and events whose structure, attributes and timing agree, with the ground truth included. Tabular generators produce rows; GraphFaker generates the connections. This release adds schema-driven generation, the fraud domain pack, database sinks, a modular domain registry, and realistic graph topology.

Positioning and documentation:

* README, package description, package docstring and the docs index all carry the same statement of what GraphFaker is and who it is for.
* New guides: ``docs/how-it-works.md`` (schema to tables), ``docs/fraud-generation.md`` (every step of the bank and its typologies), ``docs/methods.md`` (families of synthetic graph generation and which GraphFaker uses), ``docs/adding-a-domain.md``.
* ``graphfaker.domains.registry``: every domain is a ``Domain`` with a name, an options model and a ``generate`` function; third-party domains register through the ``graphfaker.domains`` entry-point group. ``graphfaker domains`` lists them, ``graphfaker generate <domain> --option value`` runs any of them.
* The README explains what each option means (``--seed``, ``--scale``, ``--hardness`` and the rest) and when to change it.

* ``graphfaker.schema``: declarative ``GraphSchema``: node types with attribute samplers, latent factors with per-group parameters, edge families, topology models. Validated with pydantic; round-trips through YAML/JSON; content-hashed.
* ``graphfaker.engine``: ``generate(schema, seed, shard_size)`` → ``GraphRun`` with columnar tables (polars), ground truth, and a manifest. Reproducible across processes; node generation is sharded with independent seeded streams.
* ``graphfaker.backends.GraphTables``: node table per type, edge table per relationship; NetworkX view; Parquet read/write.
* ``graphfaker.domains.social``: the built-in social graph re-expressed as a schema. Same realism metrics as 0.5; every former magic number is now a schema field.
* ``GraphFaker.generate(schema)`` alongside the unchanged ``generate_graph``.
* ``graphfaker.domains.fraud``: the fraud / AML domain pack: customers, accounts, merchants, devices, counterparties; a vectorised transaction process with recurring flows, repeat partners, merchant popularity, seasonality and income scaling; eleven labelled typologies with decoys; ``hardness_report`` (single-feature AUCs), ``realism_report`` and an ``evaluate`` harness compatible with gen-fraud-graph's.
* ``graphfaker.sinks``: Neo4j admin-import files, LadybugDB/Kùzu (DDL + ``COPY`` from Parquet, loads when a driver is installed), and a gen-fraud-graph compatible layout.
* CLI is now multi-command: ``graphfaker gen`` (the previous behaviour), ``graphfaker fraud``, ``graphfaker evaluate``; ``graphfaker`` console script.
* ``workers`` parallelises node sampling across processes without changing the result. osmnx is imported on first use, halving import time.
* ``docs/notebooks/graphfaker_tour.ipynb`` (executed, with charts) and ``examples/fraud_tour.py``: schemas, the fraud pack, exploration, ground truth, hardness, detectors, read/write/query. ``graphfaker[examples]`` extra.
* Place nodes carry ``latitude`` / ``longitude`` columns instead of a ``coordinates`` tuple.
* New dependencies: pydantic, polars, pyarrow, numpy, pyyaml.
* Removed the superseded proposal documents.

Realistic graph topology (2026-08-01):

Until now both endpoints of every edge were drawn uniformly at random, so the synthetic generator produced an Erdos-Renyi graph: Poisson degree distribution, no hubs, effectively no clustering, no community structure, and attributes statistically independent of the topology. It was labelled "realistic" but was not, and anything measured against it was measured against noise.

Edges are now formed by preferential attachment, triadic closure, and homophily over latent communities. Measured on 600 nodes / 2400 edges, seed 1:

===========================  ===========  =================
metric                       realistic    uniform (pre-0.5)
===========================  ===========  =================
degree Fano factor           7.9          1.7
max degree                   88           19
degree Gini                  0.45         0.26
average clustering           0.180        0.013
community modularity         0.72         -0.004
age homophily                0.81         0.01
isolated nodes               0            4
===========================  ===========  =================

* ``graphfaker.metrics``: ``graph_stats()`` and ``compare_topology()``, so the claim is checkable rather than asserted. The tests are differential: each property is compared against ``topology="uniform"``, which reproduces the old behaviour and is retained solely as a baseline.
* Every node carries a latent ``community``; attributes are drawn from community-specific distributions, which is what makes homophily possible.
* Places, organizations, events, and products carry a heavy-tailed ``prominence`` driving popularity.
* ``population`` tracks connectivity and ``employee_count`` now correlates 0.95 with the ``WORKS_AT`` edges actually present, instead of contradicting them.
* ``industry`` holds an industry rather than ``fake.job()``'s job titles.
* ``LIVES_IN``, ``BORN_IN``, and ``HEADQUARTERED_IN`` are singular. A person could previously live in four cities.
* ``total_edges`` now means what ``number_of_edges()`` reports. Counting loop iterations overshot by ~14% when bidirectional friendships dominated and undershot by ~12% when skipped functional relationships did.
* No isolated nodes. Preferential attachment alone left 24% of a 600-node graph unreachable, so coverage and top-up passes guarantee a giant component.

Fixed:

* GraphML export raised on ``None`` attribute values, which broke export for any graph small enough to leave a community without a place.

Note for anyone comparing results across versions: entity resolution is measurably harder on a realistic graph. The same injected duplicates give ``resolve()`` precision 1.000 on a uniform graph and 0.88 on a realistic one, because homophily means distinct people share attributes and neighbours. Numbers produced before 0.5 were flattered by the generator.

0.4.0 (2026-07-31)
------------------

Graph-native entity resolution, reproducible generation, and several fixes.

New:

* ``graphfaker.export``: export connectors that write files rather than requiring a database driver: ``export_csv`` (key-union headers, so heterogeneous node types keep their values in the right columns), ``export_neo4j_csv`` (``neo4j-admin`` typed headers), and ``export_cypher`` for Neo4j, openCypher, and ISO GQL. Also exposed as ``--format`` on the CLI.
* ``graphfaker.corpus``: paired text/entity corpora for measuring how many nodes a graph builder creates per real entity. Documents are clean prose, not corrupted text; ``Corpus.audit()`` verifies that no surface form could be claimed by two entities, and ``duplication_report()`` produces the counts.
* ``examples/duplication_experiment.py``: runs that measurement across several graph-building frameworks and prints a comparison table. The Cognee adapter is written against the API verified in cognee 1.4.1 (``add``/``cognify``/``export`` are coroutines; ``export`` accepts ``format="graphml"``; there is no ``get_graph_data``). It writes to a run-specific dataset instead of calling ``cognee.prune``, so an existing local store is not destroyed.
* ``docs/notebooks/duplication_experiment.ipynb``: the same experiment end to end, including repairing the graph with ``resolve()``, a threshold sweep showing the precision/recall tradeoff, and export of the cleaned graph. Runs without an LLM key using a labelled simulated extraction.
* ``resolve_entities(token_subset_floor=...)``: floors the attribute score when one name's tokens are contained in the other's ("Hill" inside "Allison Hill"). Character ratios score that pair near 0.5, so it was previously discarded before neighbourhood overlap was consulted. The floor sits below the default threshold deliberately, so containment alone never merges anything.
* ``graphfaker.resolve``: find duplicate entities using attribute similarity **and** neighbourhood overlap, then merge each cluster onto one canonical node with its edges rewired. Available as ``GraphFaker.resolve()`` or the standalone ``resolve_entities()`` / ``merge_clusters()``.
* ``evaluate_clusters()``: pairwise and B-cubed precision/recall/F1 for scoring a predicted clustering against gold labels you supply.
* Seeding: ``GraphFaker(seed=...)``, ``generate_graph(..., seed=...)``, ``reseed()``, and ``--seed`` on the CLI. Identical seed and arguments now produce an identical graph, and seeding no longer touches global ``random`` state.

Fixed:

* **Security:** TLS certificate verification was disabled for all flight-data downloads. It is now enabled by default; opt out with ``GRAPHFAKER_INSECURE_TLS=1``, which warns loudly.
* **Packaging:** ``tqdm`` and ``urllib3`` were imported but never declared as dependencies, so a clean install could fail on ``import graphfaker``.
* The CLI logged through the standard library ``venv`` module's logger by accident (``from venv import logger``) instead of the package logger.
* ``generate_graph`` reported the wrong valid sources on error ("Use 'random' or 'osm'").
* GraphML export now flattens list, tuple, set, and dict attributes instead of failing on them.
* Three CLI tests were failing on ``main`` because their mocks returned strings where a graph was required; tests also no longer write artifacts into the repository root.

Docs:

* README no longer advertises unimplemented features (RDF/JSON-LD export, Neo4j/Kuzu/TigerGraph integration, million-node scale, LLM-driven fetching).

0.2.0 (2025-06-08)
------------------
GraphFaker v0.2.0 – June 2025

This release expands GraphFaker’s scope with a new data sources to support graph construction and entity recognition tutorials:

* Wikipedia fetcher (WikiFetcher)

  - Retrieve raw page data (title, summary, content, sections, links, references) via the wikipedia package
  - Export JSON dumps of article fields

Upgrade now to effortlessly pull in unstructured Wikipedia data

0.1.0 (2025-04-02)
------------------

* First release on PyPI.

