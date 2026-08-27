#!/usr/bin/env bash
# Guard an implement-loop verify against shared-checkout dirt and live data credentials.

set -euo pipefail

usage() {
  echo "usage: verify_seam.sh --repo <path> --path <git-pathspec> [--path <git-pathspec> ...] --run <command>" >&2
  exit 64
}

repo=""
run=""
paths=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      [ "$#" -ge 2 ] || usage
      repo="$2"
      shift 2
      ;;
    --path)
      [ "$#" -ge 2 ] || usage
      paths+=("$2")
      shift 2
      ;;
    --run)
      [ "$#" -eq 2 ] || usage
      run="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done

[ -n "$repo" ] && [ -n "$run" ] && [ "${#paths[@]}" -gt 0 ] || usage
git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "verify_seam.sh: refusing non-git repo: $repo" >&2
  exit 65
}

# Names whose presence means this shell can reach LIVE data or a live surface. A verify must run
# against the throwaway seam, so holding any of these while one runs is refused outright.
#
# THE LIST IS AN ALLOWLIST BY OMISSION, WHICH IS WHY IT HAS A TEST. Any name this repo resolves from
# the environment that nobody remembered to add defeats the guard silently: the verify reports green
# while its writes land on production. tools/tests/test_verify_seam_credentials.sh scans for
# credential-shaped environment names and fails when it finds one that is neither listed nor
# explicitly classified there. Run it after touching the list, and expect it to fail the first time
# a new secret appears. That failure is the feature.
#
# The list lives in config/live-credentials.txt so this script stays repo-portable. If that file is
# missing or empty the seam REFUSES TO RUN rather than running unguarded, because an empty denylist
# and a fully-permissive one are indistinguishable from in here.
creds_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/live-credentials.txt"
if [ ! -r "$creds_file" ]; then
  echo "verify_seam.sh: refusing to run: cannot read $creds_file" >&2
  exit 68
fi

live_credentials=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(echo "$line" | tr -d '[:space:]')"
  [ -n "$line" ] && live_credentials+=("$line")
done < "$creds_file"

if [ "${#live_credentials[@]}" -eq 0 ]; then
  echo "verify_seam.sh: refusing to run: $creds_file lists no credentials" >&2
  exit 68
fi


for name in "${live_credentials[@]}"; do
  if [ -n "${!name:-}" ]; then
    echo "verify_seam.sh: refusing live credential environment: $name is set" >&2
    exit 66
  fi
done

dirty="$(git -C "$repo" status --porcelain --untracked-files=all -- "${paths[@]}")"
if [ -n "$dirty" ]; then
  echo "verify_seam.sh: refusing uncommitted verify inputs in claimed territory:" >&2
  echo "$dirty" >&2
  exit 67
fi

cd "$repo"
exec bash -c "$run"
