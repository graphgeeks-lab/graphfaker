# Docker

The image is for the cases where `pip install graphfaker` is the wrong first step: a CI job that needs a fixture in Neo4j, a data engineer who wants a bank in a volume and no Python on the host, a workshop where thirty laptops need the same thing, or a reviewer who wants to run the three commands without making an environment. It is published to the GitHub Container Registry on every release, for `linux/amd64` and `linux/arm64`, and it contains the CLI with the DuckDB and Neo4j clients. It does not contain PyTorch or the OpenStreetMap fetcher's geo libraries; the [PyG export](../pyg.md) and `--fetcher osm` want a Python environment with the `pyg` or `osm` extra.

```sh
docker pull ghcr.io/graphgeeks-lab/graphfaker:main
docker run --rm ghcr.io/graphgeeks-lab/graphfaker:main info
```

Tags: `latest` is the newest release and is what you get without a tag. A release also publishes its exact version and the prefixes of it, so 1.2.0 publishes `1.2.0`, `1.2`, `1` and `latest`; pin the exact version to reproduce a dataset. `main` is the development build, pushed after every change to `main` once it has passed the smoke test, and it moves. Every tag is built for `linux/amd64` and `linux/arm64`. `info` prints the version inside the image and which extras it carries.

## Run

`graphfaker` is the entrypoint, so every subcommand works as an argument, and `/data` is the working directory inside the container. Mount a host directory there and write to it:

```sh
docker run --rm ghcr.io/graphgeeks-lab/graphfaker:main domains
docker run --rm -v "$PWD/bank:/data" ghcr.io/graphgeeks-lab/graphfaker:main fraud --scale 0.01 --seed 42 --out /data
```

`bank/` on the host now holds `nodes/`, `edges/`, `truth/`, `schema.yaml` and `manifest.json`, the same layout `pip`-installed GraphFaker writes, and the same bytes for the same seed. On PowerShell use `${PWD}` in place of `$PWD`.

The container runs as an unprivileged user (uid 10001). If the mounted directory already exists and is owned by root or by another user, the run fails with a permission error; create the directory first as the user you are, or add `--user "$(id -u):$(id -g)"` to the command.

Load the bank into DuckDB and check the load against the Parquet:

```sh
docker run --rm -v "$PWD/bank:/data" ghcr.io/graphgeeks-lab/graphfaker:main load duckdb /data
docker run --rm -v "$PWD/bank:/data" ghcr.io/graphgeeks-lab/graphfaker:main verify duckdb /data
```

The first `load duckdb` fetches the DuckPGQ extension into the container's home directory; the container is discarded afterwards, so each run fetches it again (a few megabytes). To keep it, mount a volume at `/home/graphfaker/.duckdb`.

A schema of your own works the same way, from and to the volume:

```sh
docker run --rm -v "$PWD/work:/data" ghcr.io/graphgeeks-lab/graphfaker:main schema fraud --scale 0.05 --out /data/bank.yaml
docker run --rm -v "$PWD/work:/data" ghcr.io/graphgeeks-lab/graphfaker:main generate --schema /data/bank.yaml --seed 7 --out /data/bank
```

## Memory and CPUs

A fraud run peaks at about twice the size of its tables: 2.5 GB at `scale=0.1`, 5 GB at `0.3`, 15 GB at `1.0` ([the measurements](../scaling.md)). Docker Desktop's virtual machine is often set smaller than the machine it runs on, and a container that runs out of memory is killed without a Python traceback, so raise the VM's limit in Docker Desktop's settings before a large scale, or make the limit explicit and let the failure be legible:

```sh
docker run --rm --memory 20g --cpus 4 -v "$PWD/big:/data" ghcr.io/graphgeeks-lab/graphfaker:main fraud --scale 1.0 --seed 42 --workers 4 --out /data
```

`--workers` works inside the container; give it as many CPUs as workers, and expect about 1.5x from four of them from `scale=0.1` upward.

## With Neo4j

A compose file that starts Neo4j, generates a bank into a shared volume, and loads it:

```yaml
services:
  neo4j:
    image: neo4j:5
    environment:
      NEO4J_AUTH: neo4j/graphfaker
    ports: ["7474:7474", "7687:7687"]
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:7474"]
      interval: 5s
      retries: 20

  generate:
    image: ghcr.io/graphgeeks-lab/graphfaker:main
    command: fraud --scale 0.01 --seed 42 --out /data
    volumes: ["bank:/data"]

  load:
    image: ghcr.io/graphgeeks-lab/graphfaker:main
    command: load neo4j /data --wipe
    environment:
      NEO4J_URI: neo4j://neo4j:7687
      NEO4J_PASSWORD: graphfaker
    volumes: ["bank:/data"]
    depends_on:
      neo4j: { condition: service_healthy }
      generate: { condition: service_completed_successfully }

volumes:
  bank:
```

```sh
docker compose up --abort-on-container-exit --exit-code-from load
```

Then open http://localhost:7474 and query, or run `verify neo4j` the same way as `load`. `--sink neo4j` on the `fraud` command does the generate and the load in one container if you prefer.

## Building it yourself

The `Dockerfile` at the root of the repository builds the wheel in one stage and installs it in another, so the image carries no source tree or build tooling:

```sh
docker build -t graphfaker .
docker run --rm graphfaker info
```

The workflow (`.github/workflows/docker.yml`) builds the same file on every push and pull request and runs a generate, a load and a verify against the built image. A push to `main` publishes the `main` tag; a `v*` tag publishes the version tags and `latest`.

So publishing a release is tagging it:

```bash
git tag -a v1.2.0 -m "graphfaker 1.2.0" && git push origin v1.2.0
```

That builds both architectures, smoke-tests the exact image it is about to push, and publishes `1.2.0`, `1.2`, `1`, `latest` and a short sha. The version inside the image is the checkout's, and the PyPI workflow refuses a tag whose version does not match `pyproject.toml`, so the image and the release cannot drift.

When that path is not available, `scripts/publish-image.sh` does the same thing from a laptop: same checks in the same order, both architectures, and the same five tags. It refuses a version that does not match `pyproject.toml`, and `DRY_RUN=1` runs every check and pushes nothing.

```bash
DRY_RUN=1 scripts/publish-image.sh    # build and smoke-test only
scripts/publish-image.sh              # publish, including latest
```

It needs a token with `write:packages`, either in `GITHUB_TOKEN` or from `gh auth token`.
