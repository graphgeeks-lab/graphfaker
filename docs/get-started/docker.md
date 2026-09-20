# Docker

The image is for the cases where `pip install graphfaker` is the wrong first step: a CI job that needs a fixture in Neo4j, a data engineer who wants a bank in a volume and no Python on the host, a workshop where thirty laptops need the same thing, or a reviewer who wants to run the three commands without making an environment. It is published to the GitHub Container Registry on every release, for `linux/amd64` and `linux/arm64`, and it contains the CLI with the DuckDB and Neo4j clients. It does not contain PyTorch or the OpenStreetMap fetcher's geo libraries; the [PyG export](../pyg.md) and `--fetcher osm` want a Python environment with the `pyg` or `osm` extra.

```sh
docker pull ghcr.io/graphgeeks-lab/graphfaker:latest
```

Tags follow the release: `1.0.0`, `1.0`, `1` and `latest`. Pin the one you want to reproduce. `main` is the current development build, pushed after the same smoke test; use it to try something before it is released, not to reproduce a dataset.

## Run

`graphfaker` is the entrypoint, so every subcommand works as an argument, and `/data` is the working directory inside the container. Mount a host directory there and write to it:

```sh
docker run --rm ghcr.io/graphgeeks-lab/graphfaker domains
docker run --rm -v "$PWD/bank:/data" ghcr.io/graphgeeks-lab/graphfaker fraud --scale 0.01 --seed 42 --out /data
```

`bank/` on the host now holds `nodes/`, `edges/`, `truth/`, `schema.yaml` and `manifest.json`, the same layout `pip`-installed GraphFaker writes, and the same bytes for the same seed. On PowerShell use `${PWD}` in place of `$PWD`.

The container runs as an unprivileged user (uid 10001). If the mounted directory already exists and is owned by root or by another user, the run fails with a permission error; create the directory first as the user you are, or add `--user "$(id -u):$(id -g)"` to the command.

Load the bank into DuckDB and check the load against the Parquet:

```sh
docker run --rm -v "$PWD/bank:/data" ghcr.io/graphgeeks-lab/graphfaker load duckdb /data
docker run --rm -v "$PWD/bank:/data" ghcr.io/graphgeeks-lab/graphfaker verify duckdb /data
```

The first `load duckdb` fetches the DuckPGQ extension into the container's home directory; the container is discarded afterwards, so each run fetches it again (a few megabytes). To keep it, mount a volume at `/home/graphfaker/.duckdb`.

A schema of your own works the same way, from and to the volume:

```sh
docker run --rm -v "$PWD/work:/data" ghcr.io/graphgeeks-lab/graphfaker schema fraud --scale 0.05 --out /data/bank.yaml
docker run --rm -v "$PWD/work:/data" ghcr.io/graphgeeks-lab/graphfaker generate --schema /data/bank.yaml --seed 7 --out /data/bank
```

## Memory and CPUs

A fraud run peaks at about twice the size of its tables: 2.5 GB at `scale=0.1`, 5 GB at `0.3`, 15 GB at `1.0` ([the measurements](../scaling.md)). Docker Desktop's virtual machine is often set smaller than the machine it runs on, and a container that runs out of memory is killed without a Python traceback, so raise the VM's limit in Docker Desktop's settings before a large scale, or make the limit explicit and let the failure be legible:

```sh
docker run --rm --memory 20g --cpus 4 -v "$PWD/big:/data" ghcr.io/graphgeeks-lab/graphfaker fraud --scale 1.0 --seed 42 --workers 4 --out /data
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
    image: ghcr.io/graphgeeks-lab/graphfaker
    command: fraud --scale 0.01 --seed 42 --out /data
    volumes: ["bank:/data"]

  load:
    image: ghcr.io/graphgeeks-lab/graphfaker
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
docker run --rm graphfaker domains
```

The release workflow (`.github/workflows/docker.yml`) builds the same file on every push, runs a generate, a load and a verify against the built image, and publishes it only from a `v*` tag.
