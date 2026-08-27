#!/usr/bin/env bash
#
# Prove tools/verify_seam.sh refuses every live credential it claims to, and that its list has not
# fallen behind this repo.
#
# Ported from the author's own internal engine on 2026-08-27. The seam's refusal is an ALLOWLIST BY
# OMISSION: any name this repo resolves from the environment, that nobody remembered to add, defeats
# the guard in silence. The verify reports green while its writes land on production.
#
# Four parts:
#   1  every name the guard lists is actually refused, exit 66, message naming it
#   2  the guard is not simply always 66 (without which part 1 proves nothing)
#   3  the guard refuses to run at all when its config is missing or empty, exit 68
#   4  every credential-shaped environment name this repo resolves is either listed by the guard
#      or classified below with a reason
#
# Part 4 is the part that matters. Parts 1 to 3 confirm what someone already thought of; part 4 is
# what fails when a new secret appears. Expect that failure. It is the feature.
#
# DECOY VALUES ONLY, AND NOT FROM THIS SHELL. Every guard invocation runs under `env -i`, so the
# only variable set is one decoy string that authenticates nowhere. That also stops a runner who
# legitimately holds a real token from making part 1 pass for the wrong reason.
#
#   bash tools/tests/test_verify_seam_credentials.sh
#
# set -e is deliberately NOT used: this file's whole job is running commands that must fail.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$REPO/tools/verify_seam.sh"
CREDS="$REPO/config/live-credentials.txt"
DECOY='decoy-value-authenticates-nowhere'
REFUSAL_EXIT=66
NOCONFIG_EXIT=68
failures=0

fail() { printf '  FAIL: %s\n' "$*" >&2; failures=$((failures + 1)); }

[ -r "$GUARD" ] || { echo "no guard at $GUARD" >&2; exit 1; }
[ -r "$CREDS" ] || { echo "no credential list at $CREDS" >&2; exit 1; }

# Names that look credential-shaped but are not secrets. One per line, "NAME  reason".
# A plain list rather than an associative array: macOS ships bash 3.2, which has none.
CLASSIFIED_SAFE="
PATH                             shell path
HOME                             shell home
BASH_SOURCE                      a bash builtin
"

is_classified_safe() {
  printf '%s\n' "$CLASSIFIED_SAFE" | awk '{print $1}' | grep -qx "$1"
}


listed_names() {
  sed 's/#.*//' "$CREDS" | tr -d '[:blank:]' | grep -v '^$' | sort -u
}

echo "== part 1: every listed name is refused =="
while IFS= read -r name; do
  out="$(env -i "$name=$DECOY" bash "$GUARD" --repo "$REPO" --path README.md --run 'echo ran' 2>&1)"
  code=$?
  if [ "$code" -ne "$REFUSAL_EXIT" ]; then
    fail "$name: expected exit $REFUSAL_EXIT, got $code"
  elif ! printf '%s' "$out" | grep -q "$name"; then
    fail "$name: refused but the message did not name it"
  fi
done < <(listed_names)
echo "   checked $(listed_names | wc -l | tr -d ' ') names"

echo "== part 2: the guard is not always 66 =="
out="$(env -i PATH="$PATH" bash "$GUARD" --repo "$REPO" --path README.md --run 'echo ran' 2>&1)"
code=$?
[ "$code" -eq 0 ] || fail "a clean shell should pass, got exit $code: $out"
printf '%s' "$out" | grep -q ran || fail "a clean shell should have run the command"

echo "== part 3: no config means no run =="
tmp="$(mktemp -d)"
printf '# nothing here\n' > "$tmp/empty.txt"
out="$(env -i PATH="$PATH" CREDS_OVERRIDE="$tmp/empty.txt" bash -c \
  "cp '$CREDS' '$tmp/backup'; cp '$tmp/empty.txt' '$CREDS'; bash '$GUARD' --repo '$REPO' --path README.md --run 'echo ran'; rc=\$?; cp '$tmp/backup' '$CREDS'; exit \$rc" 2>&1)"
code=$?
[ "$code" -eq "$NOCONFIG_EXIT" ] || fail "an empty credential list should exit $NOCONFIG_EXIT, got $code"
rm -rf "$tmp"

echo "== part 4: no credential-shaped name in this repo is unclassified =="
# Names resolved from the environment anywhere in the repo, plus everything in .env.example.
# This repo has no .claude/ directory, so it is not scanned; scripts/ and tools/ cover every place
# this repo currently resolves environment names from.
discovered="$( {
  grep -rhoE 'os\.(environ(\.get)?|getenv)\(\s*["'"'"']([A-Z][A-Z0-9_]{2,})["'"'"']' \
    "$REPO/scripts" "$REPO/tools" 2>/dev/null | grep -oE '[A-Z][A-Z0-9_]{2,}'
  grep -rhoE '\$\{?([A-Z][A-Z0-9_]{2,})\}?' "$REPO/scripts" "$REPO/tools" 2>/dev/null \
    | grep -oE '[A-Z][A-Z0-9_]{2,}'
  [ -r "$REPO/.env.example" ] && sed 's/#.*//' "$REPO/.env.example" | grep -oE '^[A-Z][A-Z0-9_]{2,}'
} | sort -u )"

shaped="$(printf '%s\n' "$discovered" | grep -E '(KEY|TOKEN|SECRET|PASSWORD|PASS|URL|DSN|CREDENTIAL|ROLE|APIKEY)$')"
listed="$(listed_names)"

while IFS= read -r name; do
  [ -n "$name" ] || continue
  if printf '%s\n' "$listed" | grep -qx "$name"; then continue; fi
  if is_classified_safe "$name"; then continue; fi
  fail "$name is credential-shaped, resolved by this repo, and neither listed in config/live-credentials.txt nor classified safe in this test"
done < <(printf '%s\n' "$shaped")
echo "   scanned $(printf '%s\n' "$shaped" | grep -c . ) credential-shaped name(s)"

echo
if [ "$failures" -eq 0 ]; then
  echo "verify_seam credentials: PASS"
  exit 0
fi
echo "verify_seam credentials: $failures failure(s)" >&2
exit 1
