# graphfaker
### Problem Statement
Graph data is essential for solving complex problems in various fields, including social network analysis, transportation modeling, recommendation systems, and fraud detection. However, many professionals, researchers, and students face a common challenge: a lack of easily accessible, realistic graph datasets for testing, learning, and benchmarking. Real-world graph data is often restricted due to privacy concerns, complexity, or large size, making experimentation difficult.

### Solution: GraphFaker
GraphFaker is an open-source Python library designed to generate, load, and export synthetic graph datasets in a user-friendly and configurable way. It enables users to create realistic yet customizable graph structures tailored to their specific needs, allowing for better experimentation and learning without relying on sensitive or proprietary data.

#### Key Features
- Synthetic Graph Generation
  -  Create graphs for social networks, transportation systems, knowledge graphs, and more.
  -  Configurable number of nodes, edges, and relationships.
  -  Support for weighted, directed, and attributed graphs.

- Prebuilt Benchmark Graphs
  -  Load small, structured datasets for graph learning and algorithm testing.
  -  Support for loading into NetworkX, Pandas, Kuzu, and Neo4j.
  -  Export to formats like CSV, JSON, GraphML, and RDF.

- Knowledge Graph Creation
  -  Generate knowledge graphs with predefined schemas (people, organizations, locations, etc.).
  -  Randomized entity and relationship generation.
  -  Output in JSON-LD, RDF, or Neo4j formats.

### Impact
GraphFaker simplifies graph data experimentation by providing an accessible, open-source solution for professionals and students alike. It helps researchers test algorithms, developers prototype applications, and educators teach graph concepts without dealing with data access barriers.

References:
https://arxiv.org/pdf/2203.00112
https://research.google/pubs/graphworld-fake-graphs-bring-real-insights-for-gnns/
