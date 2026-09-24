GraphFaker
==========

.. raw:: html

   <div class="gf-landing">
   <section class="gf-hero">
     <div>
       <p class="gf-kicker">Open-source Python library</p>
       <h1>Synthetic graph data that behaves like the real thing.</h1>
       <p class="gf-lede">Generate graph data that behaves the way real people and real money do: paid on payday, the same few shops paid again and again, friends who know each other's friends. No real person in it, and the ground truth included.</p>
       <div class="gf-actions">
         <a class="gf-button" href="get-started/index.html">Get started</a>
         <a class="gf-button secondary" href="how-it-works.html">Read how it works</a>
       </div>
       <p class="gf-position">Data Designer generates the tables. <strong>GraphFaker generates the connections.</strong></p>
     </div>
     <div class="gf-terminal">
       <div class="gf-terminal-bar">
         <i></i><i></i><i></i>
         <div class="gf-terminal-tabs" role="tablist" aria-label="Database sink">
           <button type="button" role="tab" data-sink-tab aria-selected="true">duckdb</button>
           <button type="button" role="tab" data-sink-tab aria-selected="false">ladybug</button>
           <button type="button" role="tab" data-sink-tab aria-selected="false">neo4j</button>
         </div>
       </div>
       <div class="gf-terminal-panels">
       <pre data-sink-panel role="tabpanel" class="current"><span class="dim">$</span> pip install "graphfaker[duckdb]"
   <span class="dim">$</span> graphfaker generate fraud --scale 0.01 --hardness high \
       --seed 42 --out ./bank --sink duckdb

   <span class="dim">loaded into database 'bank/graph.duckdb' in 5.2s</span>
   <span class="dim">  nodes    255,772  Account=100000, Customer=71429, ...</span>
   <span class="dim">  edges  1,088,552  PAYS=619689, TRANSFERS=247336, ...</span>
   <span class="dim">  truth        492  Pattern=33, IN_PATTERN=205, ...</span>
   <span class="ok">PASS: 154/154 checks on bank/graph.duckdb</span></pre>
       <pre data-sink-panel role="tabpanel" aria-hidden="true"><span class="dim">$</span> pip install "graphfaker[ladybug]"
   <span class="dim">$</span> graphfaker generate fraud --scale 0.01 --hardness high \
       --seed 42 --out ./bank --sink ladybug

   <span class="dim">loaded into database 'bank/graph.lbdb' in 10.6s</span>
   <span class="dim">  nodes    255,772  Account=100000, Customer=71429, ...</span>
   <span class="dim">  edges  1,088,552  PAYS=619689, TRANSFERS=247336, ...</span>
   <span class="dim">  truth        492  Pattern=33, IN_PATTERN=205, ...</span>
   <span class="ok">PASS: 154/154 checks on bank/graph.lbdb</span></pre>
       <pre data-sink-panel role="tabpanel" aria-hidden="true"><span class="dim">$</span> pip install "graphfaker[neo4j]"
   <span class="dim">$</span> graphfaker generate fraud --scale 0.01 --hardness high \
       --seed 42 --out ./bank --sink neo4j

   <span class="dim">loaded into database 'fraud' in 81.4s</span>
   <span class="dim">  nodes    255,772  Account=100000, Customer=71429, ...</span>
   <span class="dim">  edges  1,088,552  PAYS=619689, TRANSFERS=247336, ...</span>
   <span class="dim">  truth        622  Pattern=33, IN_PATTERN=205, ...</span>
   <span class="ok">PASS: 154/154 checks on database 'fraud'</span></pre>
       </div>
     </div>
   </section>

   <section class="gf-cards">
     <div class="gf-card">
       <svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2"></circle><circle cx="18" cy="8" r="2"></circle><circle cx="9" cy="18" r="2"></circle><circle cx="17" cy="17" r="2"></circle><path d="M7.7 7.2l8.6 0.5M7 8l1.5 8M16.5 9.8l0.4 5.3M11 18h4"></path></svg>
       <h3>Realistic</h3>
       <p>Salary on payday, rent on the first, transfers that go to the same few people, friends who know each other's friends. Who someone is, who they know and when they pay all come from the same model, so they never contradict each other.</p>
     </div>
     <div class="gf-card">
       <svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M4 12h16M4 17h10"></path><path d="M17 15l2 2 3-3"></path></svg>
       <h3>Labelled</h3>
       <p>Every injected pattern is recorded with its accounts, roles and transactions. Hardness is measured against the simple rules a detector would try first.</p>
     </div>
     <div class="gf-card">
       <svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="6" rx="7" ry="2.6"></ellipse><path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"></path></svg>
       <h3>Loadable</h3>
       <p>Neo4j, LadybugDB, DuckDB with SQL/PGQ, PyTorch Geometric, Parquet, NetworkX. Loaded from Arrow, verified against the files it came from, with a blind copy for honest benchmarks.</p>
     </div>
   </section>

   <section class="gf-section gf-audience-section">
     <p class="gf-section-title">Built for</p>
     <div class="gf-audience">
       <div><h3>Graph database engineers</h3><p>Demo and load-test with data that has hubs and communities, not a random soup.</p></div>
       <div><h3>Fraud and AML teams</h3><p>Eleven laundering typologies with decoys, and a score for every rule you try.</p></div>
       <div><h3>ML engineers</h3><p>Train and evaluate GNNs against a known answer, at a size you choose.</p></div>
       <div><h3>Knowledge-graph builders</h3><p>Corpora with known entities for measuring what an extraction pipeline duplicates.</p></div>
     </div>
   </section>

   <section class="gf-section">
     <div class="gf-docs-head">
       <h2>Documentation</h2>
       <span>Everything here is reproducible: same seed, same bytes, on any machine.</span>
     </div>
     <div class="gf-docs">
       <div class="gf-doc">
         <a class="gf-doc-kicker" href="get-started/index.html">Get started</a>
         <a href="get-started/install.html">Install</a>
         <a href="get-started/first-commands.html">Three things to try</a>
         <a href="get-started/python.html">Using GraphFaker from Python</a>
         <a href="get-started/docker.html">Docker</a>
         <a href="notebooks/graphfaker_tour.html">The tour notebook</a>
       </div>
       <div class="gf-doc">
         <a class="gf-doc-kicker" href="guides/index.html">Guides</a>
         <a href="how-it-works.html">How generation works</a>
         <a href="fraud-generation.html">How the fraud graph is generated</a>
         <a href="methods.html">Ways to generate synthetic graphs</a>
         <a href="adding-a-domain.html">Adding a domain</a>
         <a href="pyg.html">Training a GNN on the bank</a>
       </div>
       <div class="gf-doc">
         <a class="gf-doc-kicker" href="domains/index.html">Domains</a>
         <a href="domains/social.html">Social</a>
         <a href="domains/fraud.html">Fraud and AML</a>
         <a href="domains/real-world.html">Real-world networks</a>
       </div>
       <div class="gf-doc">
         <a class="gf-doc-kicker" href="databases/index.html">Databases</a>
         <a href="neo4j.html">Neo4j</a>
         <a href="ladybug.html">LadybugDB</a>
         <a href="duckdb.html">DuckDB and SQL/PGQ</a>
         <a href="notebooks/neo4j_fraud_analysis.html">Finding money laundering in a synthetic bank</a>
       </div>
       <div class="gf-doc">
         <a class="gf-doc-kicker" href="reference/index.html">Reference</a>
         <a href="reference/api.html">Python API</a>
         <a href="reference/cli.html">Command line</a>
       </div>
       <div class="gf-doc">
         <a class="gf-doc-kicker" href="project/index.html">Project</a>
         <a href="history.html">Release history</a>
         <a href="contributing.html">Contributing</a>
         <a href="authors.html">Authors</a>
       </div>
     </div>
   </section>
   </div>

.. toctree::
   :hidden:

   get-started/index
   guides/index
   domains/index
   databases/index
   reference/index
   project/index
