#!/usr/bin/env bash
# Publish the image to GHCR by hand, including `latest`.
#
# The tag push is the normal path: tagging `v1.2.0` makes
# .github/workflows/docker.yml build both architectures, smoke-test the exact
# image it is about to publish, and push `1.2.0`, `1.2`, `1`, `latest` and a
# short sha. Use this script when that path is not available: the workflow is
# failing, the runner cannot reach the registry, or an image has to go out
# from a laptop.
#
#   scripts/publish-image.sh            # version from pyproject.toml
#   scripts/publish-image.sh 1.2.0      # or say it, and it is checked
#   DRY_RUN=1 scripts/publish-image.sh  # build and smoke-test, push nothing
#
# It does what the workflow does, in the same order, because the point of the
# workflow is that nothing reaches `latest` without passing a generate, a load
# and a verify first. It refuses to publish a version that does not match
# pyproject.toml, for the same reason python-publish.yml does: the image and
# the PyPI release must not drift.
set -euo pipefail

# MSYS and git-bash rewrite anything that looks like a unix path when they
# spawn a native binary, which mangles the *container* side of a mount:
# `--out /data` arrives as `--out C:/Program Files/Git/data`, the container
# writes a directory literally called `C:`, and the host sees no dataset. The
# host side is converted deliberately, by host_path below.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

IMAGE="${IMAGE:-ghcr.io/graphgeeks-lab/graphfaker}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }

# A bind mount needs a path the host's docker understands. Under MSYS or
# git-bash on Windows, $PWD is a virtual path: docker accepts it, mounts
# something inside its own VM, and the container writes a bank the host cannot
# see. The smoke test would then pass having proved nothing about the one thing
# a user cares about, which is the output landing in their directory.
host_path() {
    if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

PROJECT_VERSION="$(sed -nE 's/^version = "(.*)"/\1/p' pyproject.toml | head -1)"
[ -n "$PROJECT_VERSION" ] || die "no version in pyproject.toml"
VERSION="${1:-$PROJECT_VERSION}"

if [ "$VERSION" != "$PROJECT_VERSION" ]; then
    die "asked to publish $VERSION but pyproject.toml says $PROJECT_VERSION.
Bump the version and HISTORY.rst first; an image whose version does not match
the release it claims to be is worse than no image."
fi

MAJOR="${VERSION%%.*}"
MINOR="${VERSION%.*}"
case "$VERSION" in
    *[!0-9.]*) die "'$VERSION' is not a release version; this script does not publish pre-releases to latest" ;;
esac

say "Publishing $IMAGE $VERSION as $VERSION, $MINOR, $MAJOR and latest"
command -v docker >/dev/null || die "docker is not on PATH"
docker buildx version >/dev/null 2>&1 || die "docker buildx is not available"

# arm64 is emulated when the host is amd64, so QEMU has to be registered
# before buildx can target it. Harmless when it is already there.
say "Registering QEMU for the other architecture"
docker run --privileged --rm tonistiigi/binfmt --install all >/dev/null 2>&1 \
    || echo "   (could not register QEMU; a cross build may fail)"

# Built to the local daemon first, so the smoke test runs against the exact
# image that gets pushed rather than a rebuild of it.
say "Building for this machine, to smoke-test"
docker buildx build --load -t "graphfaker:publish" .

WORK="$(mktemp -d)"
# The bind mount lives under the checkout rather than in a temp directory,
# because that is a path both the host and docker agree about on every
# platform. Removed on the way out, including on failure.
SMOKE="$ROOT/.publish-smoke"
rm -rf "$SMOKE" || die "could not clear $SMOKE; remove it and try again"
trap 'rm -rf "$WORK" "$SMOKE" 2>/dev/null || true' EXIT

# Output is captured to a file before it is searched. Piping `docker run` into
# `grep -q` makes grep exit on its first match, docker take SIGPIPE, and
# `pipefail` fail the whole thing: "write /dev/stdout: The pipe is being
# closed", reported as a missing fraud domain. The workflow has the same note
# over the same two commands.
say "The CLI is there and reports the version in pyproject.toml"
docker run --rm graphfaker:publish --version > "$WORK/version.txt"
grep -qx "graphfaker $VERSION" "$WORK/version.txt" \
    || { cat "$WORK/version.txt"; die "the image reports a different version"; }
docker run --rm graphfaker:publish domains > "$WORK/domains.txt"
grep -q fraud "$WORK/domains.txt" \
    || die "the fraud domain did not ship in the image"

say "It runs as a non-root user"
[ "$(docker run --rm --entrypoint id graphfaker:publish -u)" != "0" ] \
    || die "the image runs as root"

say "It generates a bank into a volume, loads it and verifies it"
mkdir -p "$SMOKE/bank" && chmod 777 "$SMOKE/bank"
MOUNT="$(host_path "$SMOKE/bank")"
docker run --rm -v "$MOUNT:/data" graphfaker:publish \
    fraud --scale 0.001 --seed 42 --out /data --no-report
[ -f "$SMOKE/bank/manifest.json" ] \
    || die "the container wrote no manifest.json the host can see.
Mounted $MOUNT; if that is not a path your docker resolves to this directory,
the bind mount went somewhere else."
docker run --rm -v "$MOUNT:/data" graphfaker:publish load duckdb /data --no-verify
docker run --rm -v "$MOUNT:/data" graphfaker:publish verify duckdb /data

if [ "${DRY_RUN:-}" = "1" ]; then
    say "DRY_RUN=1, so nothing was pushed. The image passed every check."
    exit 0
fi

say "Logging in to ghcr.io"
if [ -n "${GITHUB_TOKEN:-}" ]; then
    echo "$GITHUB_TOKEN" | docker login ghcr.io -u "${GITHUB_ACTOR:-$USER}" --password-stdin
elif command -v gh >/dev/null; then
    gh auth token | docker login ghcr.io -u "$(gh api user --jq .login)" --password-stdin
else
    die "set GITHUB_TOKEN (a classic token with write:packages) or install gh"
fi

say "Building both architectures and pushing"
docker buildx build \
    --platform "$PLATFORMS" \
    --tag "$IMAGE:$VERSION" \
    --tag "$IMAGE:$MINOR" \
    --tag "$IMAGE:$MAJOR" \
    --tag "$IMAGE:latest" \
    --push .

say "What is on the registry now"
docker manifest inspect "$IMAGE:latest" > "$WORK/manifest.json" \
    || die "latest was pushed but its manifest cannot be read back"
grep -E '"architecture"' "$WORK/manifest.json" \
    || die "latest was pushed but its manifest lists no architecture"
printf '\nPublished:\n'
for tag in "$VERSION" "$MINOR" "$MAJOR" latest; do
    printf '  %s:%s\n' "$IMAGE" "$tag"
done
printf '\nPull it somewhere else to be sure:\n  docker run --rm %s:latest info\n' "$IMAGE"
