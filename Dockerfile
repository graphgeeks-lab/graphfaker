# GraphFaker as a container: the CLI, the Parquet writer, and the DuckDB and
# Neo4j clients, for people who do not have a Python they want to touch.
#
# Two stages. The builder makes the wheel and the runtime installs it, so the
# final image carries no build tooling, no source tree and no tests, only the
# package and its dependencies.
#
#   docker build -t graphfaker .
#   docker run --rm graphfaker domains
#   docker run --rm -v "$PWD/bank:/data" graphfaker fraud --scale 0.01 --seed 42 --out /data
#   docker run --rm -v "$PWD/bank:/data" graphfaker load duckdb /data
#
# `/data` is the working directory and the place to mount a volume; a relative
# `--out` lands there too. Swap `graphfaker` for ghcr.io/graphgeeks-lab/graphfaker
# once a v* tag has published it.

# --- build ---------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /src

# Only what the wheel needs. Copying the whole tree would rebuild it on any
# change to docs or tests; .dockerignore keeps those out of the context too.
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY graphfaker/ ./graphfaker/

RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir dist/

# --- runtime -------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="graphfaker" \
      org.opencontainers.image.description="Generate realistic graph datasets from a schema" \
      org.opencontainers.image.source="https://github.com/graphgeeks-lab/graphfaker" \
      org.opencontainers.image.documentation="https://graphfaker.readthedocs.io" \
      org.opencontainers.image.licenses="MIT"

# Unbuffered so progress and the hardness report stream to `docker logs`
# rather than arriving in one block at the end of a seven-minute run.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# `[neo4j,duckdb]`: the two database clients are what make `load` and `verify`
# work from inside the container. Not `[pyg]` (torch is over two gigabytes) and
# not `[osm]` (150 MB of geo libraries for a fetcher that needs the network
# anyway); both belong in a variant only if someone asks for it.
COPY --from=builder /src/dist/*.whl /tmp/
RUN set -eux; \
    pip install --no-cache-dir "$(ls /tmp/*.whl)[neo4j,duckdb]"; \
    rm -f /tmp/*.whl

# Nothing here needs root, and the output directory is a bind mount that the
# host user will want to own. DuckDB installs the duckpgq extension under
# $HOME/.duckdb on first use, so the user needs a writable home.
RUN useradd --create-home --uid 10001 graphfaker \
    && mkdir -p /data && chown graphfaker:graphfaker /data
USER graphfaker
WORKDIR /data

# `graphfaker` as the entrypoint means every subcommand works as an argument:
#   docker run --rm graphfaker fraud --help
#   docker run --rm graphfaker schema fraud
# With no argument it prints the help.
ENTRYPOINT ["graphfaker"]
CMD ["--help"]

# Memory: a fraud run peaks at about twice the size of its tables, 2.5 GB at
# scale 0.1 and 15 GB at scale 1.0 (docs/scaling.md). Docker Desktop's default
# VM is often smaller than the machine, so raise its limit, or pass
# `--memory` explicitly, before a large scale. `--workers` works inside the
# container; give it as many CPUs as workers.
#
# Neo4j from the same compose network:
#   docker run --rm --network graphfaker -v "$PWD/bank:/data" \
#     -e NEO4J_URI=neo4j://neo4j:7687 -e NEO4J_PASSWORD=... \
#     graphfaker load neo4j /data
