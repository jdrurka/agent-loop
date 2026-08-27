#!/usr/bin/env python3
"""loop_check.py — deterministic per-ticket checker for the loop driver.

The only path from in_review to done. Given a plan dir and a ticket id:
  1. re-runs the ticket's declared verify command (output captured to T-###.verify.log)
  2. collects the ticket's OWN commits in --repo (plan-scoped message prefix
     match inside the base_sha..HEAD window) and checks their paths against the
     territory globs
  3. scans the same paths against the blast-radius globs
  4. lints the ticket file's evidence sections

Writes the verdict (exit code included) to tickets/T-###.verdict.json — the driver
reads that file, never a shell capture. Stdout gets a convenience copy.
Exit codes: 0 = all pass · 1 = any failure · 2 = pass but blast-radius gated.

Usage:
  python3 scripts/loop_check.py --plan-dir plans/2026-08-07-example --ticket T-001
  python3 scripts/loop_check.py --plan-dir plans/2026-08-07-example --lint-queue
  python3 scripts/loop_check.py --plan-dir ... --ticket ... --blast-globs-file globs.txt
  python3 scripts/loop_check.py --plan-dir ... --ticket ... --repo ~/Dev/projects/other-repo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# The blast-radius classification: which files are unrecoverable enough that a
# ticket touching them stops for a human. Decided 2026-08-07; lifted out of
# this file on 2026-08-19 so the engine is repo-portable.
#
# The lists live in config/blast-globs.txt at the repo root. Absent or unreadable,
# the generic fallbacks below apply, so a missing config narrows nothing silently
# and the gate still fails closed. A per-plan --blast-globs-file layers on top of
# whichever set is in force, never from inside a ticket.

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "blast-globs.txt"

_FALLBACK = {
    "blast": [
        "**/migrations/**", "**/migrations_pg/**", "**/*auth*", "**/.env*",
        "**/Dockerfile", "**/docker-compose*", "scripts/git-hooks/**",
        "credentials/**", "**/*secrets*",
    ],
    "judge": [
        "tools/verify_seam.sh", "apps/*/verify.sh", "scripts/loop_check.py",
        "scripts/git-hooks/**",
    ],
    "auth": ["**/*auth*", "**/*secrets*", "**/*token*", "**/.env*"],
    "assertion": [
        "**/tests/**", "**/test_*.py", "**/*.test.*", "**/*.spec.*",
        "tools/asserts/**",
    ],
}


def _load_sections(path: Path) -> dict[str, list[str]]:
    """Parse config/blast-globs.txt: [section] headers, one glob per line, # comments.

    Any section the file omits keeps its fallback. A section present but empty is
    honoured as empty, because deliberately emptying a class is a real choice and
    silently restoring the fallback would be the hole this whole file guards.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return dict(_FALLBACK)
    found: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            found.setdefault(current, [])
            continue
        if current is not None:
            found[current].append(line)
    merged = dict(_FALLBACK)
    merged.update(found)
    return merged


_SECTIONS = _load_sections(_CONFIG_PATH)

DEFAULT_BLAST_GLOBS = list(_SECTIONS["blast"])

# Exit-2 is a control-flow result, not a policy. Keep the classification and its
# handling beside the checker so drivers only have to execute the verdict. The
# receipt classes name their eventual handling here; later cause resolution
# narrows unresolved instances back to block before the driver consumes them.
GATE_POLICIES = {
    "unattributed_commit": {"policy": "receipt"},
    "blast_judge_file": {"policy": "receipt"},
    "blast_migration_file": {"policy": "ttl", "ttl_hours": 4},
    "blast_shipped_migration_edit": {"policy": "block"},
    "blast_auth_surface": {"policy": "ttl", "ttl_hours": 24},
    "blast_other": {"policy": "ttl", "ttl_hours": 4},
    "assertion_change": {"policy": "block"},
    "correlated_failure": {"policy": "block"},
}

JUDGE_FILE_GLOBS = list(_SECTIONS["judge"])
AUTH_SURFACE_GLOBS = list(_SECTIONS["auth"])
ASSERTION_GLOBS = list(_SECTIONS["assertion"])

REQUIRED_SECTIONS = ["Implementation plan", "Evidence", "Findings"]
ENGINE_VERSION = "2"
PROMPT_VERSION = "2026-08-13-v2"
LOCALE_VARIABLES = [
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_MESSAGES",
    "LC_MONETARY", "LC_NUMERIC", "LC_TIME", "LC_PAPER", "LC_NAME",
    "LC_ADDRESS", "LC_TELEPHONE", "LC_MEASUREMENT", "LC_IDENTIFICATION",
]


def ticket_prefix(plan_slug: str, ticket_id: str) -> str:
    """The exact commit-message prefix that identifies one ticket in one plan."""
    return f"loop({plan_slug}/{ticket_id}):"


def verdict_telemetry(ticket: dict) -> dict[str, str]:
    """Stable dimensions for comparing outcomes across engine runs."""
    return {
        "engine_version": ENGINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": ticket.get("model") or "unspecified",
    }


def ticket_is_ready(ticket: dict, done_ids: set[str],
                    cleared_blockers: set[str]) -> bool:
    """Separate durable ticket dependencies from temporary driver conditions."""
    return (ticket.get("status") == "active"
            and set(ticket.get("deps", [])).issubset(done_ids)
            and set(ticket.get("blocked_on", [])).issubset(cleared_blockers))


def parse_blast_globs(text: str) -> list[str]:
    """The per-plan globs file is a DIFF against the defaults, not a replacement.

    A bare line adds a glob, a leading '-' drops a default, and a first
    non-comment line of '!' means everything after it is the whole list.
    Replacement-by-default was the trap: a per-plan file silently inherited a
    hole for every default it forgot to restate, and a per-plan globs file once
    restated 13 defaults verbatim purely to defend against it. A '-' naming a glob that
    isn't in the list is ignored on purpose — a typo'd drop leaves the default
    gated, which fails safe and visibly (the ticket gates, a human looks).
    """
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if lines and lines[0] == "!":
        return lines[1:]
    globs = list(DEFAULT_BLAST_GLOBS)
    for ln in lines:
        if ln.startswith("-"):
            dropped = ln[1:].strip()
            if dropped in globs:
                globs.remove(dropped)
        elif ln not in globs:
            globs.append(ln)
    return globs


def validate_verify(cmd: str | None) -> list[str]:
    """Refuse a verify string that can't run as written, before the seam tries it.

    Two live shapes, both from this campaign. An unresolved manifest placeholder
    (`<chain…>`, `<T018 file>`) reaches the shell as a redirect and the command
    dies before the seam runs, so the failure looks nothing like the ticket. And a
    `cd` into a path that doesn't exist (one typo under a `2>/dev/null`) silently
    lands every later command in the home directory. Both otherwise burn three
    attempts and park a correct ticket; caught here they fail attempt 1 with the
    reason named. An absent command is not this function's concern (verify_exit
    -1 already reports it), so it returns clean.

    Only an absolute or `~` `cd` target is judged: a relative one's existence
    depends on a cwd this checker doesn't own, so flagging it would false-positive.
    """
    if not cmd:
        return []
    problems = []
    if re.search(r"<[A-Za-z][A-Za-z0-9 _./-]*(?:…)?\s*>", cmd):
        problems.append("verify carries a <...> placeholder — materialize the real "
                        "command at Phase 0; a literal '<' is a shell redirect and "
                        "dies before the seam runs")
    for m in re.finditer(r"\bcd\s+(['\"]?)([^\s;|&]+)\1", cmd):
        target = m.group(2)
        if target.startswith(("/", "~")) and not Path(target).expanduser().exists():
            problems.append(f"verify does `cd {target}` to a path that does not exist")
    return problems


def check_receipt(output: str, expect: str | None) -> list[str]:
    """Refuse a hollow pass: exit 0 with no evidence the check actually ran.

    Exit codes measure "no command errored", not "the assertion happened" — a
    verify script without `set -e`, a psql assert that returns zero rows, or a
    grep guarded by `|| true` all exit 0 having checked nothing (T-004; the
    2026-08-14 research found 67 governance checks elsewhere passing empty for
    months). A ticket may declare `verify_expect`, a regex its verify output
    must match for a pass to count. Absent field = today's behavior exactly.
    An invalid regex fails loudly with its own name — a broken receipt must
    never quietly widen into no receipt at all.
    """
    if not expect:
        return []
    try:
        if re.search(expect, output, re.M):
            return []
    except re.error as err:
        return [f"verify_expect is not a valid regex ({err}): {expect!r}"]
    return [f"verify exited 0 but its output lacks the declared receipt "
            f"{expect!r} — hollow pass refused"]


def verify_environment() -> dict[str, str]:
    """Preserve the process environment while making locale behavior deterministic."""
    env = os.environ.copy()
    env.update({name: "C.UTF-8" for name in LOCALE_VARIABLES})
    return env


def glob_to_regex(glob: str) -> re.Pattern:
    """Translate a glob with ** support into a full-path regex."""
    out = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i : i + 2] == "**":
                out.append(".*")
                i += 2
                if i < len(glob) and glob[i] == "/":
                    i += 1  # '**/' already covered by .*
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(path: str, globs: list[str]) -> bool:
    """gitignore semantics: a glob matching the path OR any ancestor directory
    of it matches, so one directory glob (`**/*auth*`) covers every file under
    a matching directory. glob_to_regex anchors `**/*x*` to the last path
    segment, so before T-016 gating a directory took the two-glob pair
    `**/*x*` + `**/*x*/**`; the pair still works, it's just no longer required.
    """
    parts = path.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return any(any(rx.match(p) for p in prefixes)
               for rx in (glob_to_regex(g) for g in globs))


def derive_gate_class(verdict: dict, added_paths: set[str] | None = None) -> str:
    """Name one exit-2 cause, choosing the strictest class for mixed gates."""
    added_paths = added_paths or set()
    blast_paths = verdict.get("blast_radius", [])

    if verdict.get("correlated_failure"):
        return "correlated_failure"
    if verdict.get("unattributed"):
        return "unattributed_commit"
    if (verdict.get("apply", {}).get("opted_in")
            and verdict.get("apply", {}).get("action") == "human_gate"
            and not blast_paths):
        # Applying a migration is irreversible even when authoring its file was
        # not blast-gated by this plan. It must never inherit the file TTL.
        return "blast_shipped_migration_edit"
    if any(matches_any(path, JUDGE_FILE_GLOBS) for path in blast_paths):
        return "blast_judge_file"
    migration_paths = [path for path in blast_paths if migration_identity(path)]
    if any(path not in added_paths for path in migration_paths):
        return "blast_shipped_migration_edit"
    if any(matches_any(path, ASSERTION_GLOBS) for path in blast_paths):
        return "assertion_change"
    if any(matches_any(path, AUTH_SURFACE_GLOBS) for path in blast_paths):
        return "blast_auth_surface"
    if migration_paths:
        return "blast_migration_file"
    return "blast_other"


def material_digest(territory: list[str], repo: Path) -> dict:
    """Bind the verdict to the tree state it judged (T-006, informational).

    A green checkmark is a statement about the past: once the tree moves, the
    evidence is stale and nothing says so. The digest records HEAD plus a
    content hash over the tracked files matching the ticket's territory at
    adjudication time, so a later audit (or a cloud landing step) can detect
    that the verified tree is no longer the delivered tree. Recorded, not
    gating — gating on it is a stage-2 decision.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True).stdout.strip()
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=repo, capture_output=True,
            text=True, check=True).stdout.splitlines()
    except (subprocess.CalledProcessError, OSError) as err:
        # Informational must never be fatal: adjudication proceeds, the
        # staleness signal is just absent and says so.
        return {"head": None, "territory_tree": None,
                "error": f"digest unavailable: {err}"}
    digest = hashlib.sha256()
    for path in sorted(p for p in tracked if p.strip() and matches_any(p, territory)):
        try:
            content = (repo / path).read_bytes()
        except OSError:
            content = b"<unreadable>"
        digest.update(path.encode() + b"\0"
                      + hashlib.sha256(content).hexdigest().encode() + b"\n")
    return {"head": head, "territory_tree": digest.hexdigest()}


def read_ticket(plan_dir: Path, ticket_id: str) -> dict:
    queue = plan_dir / "tickets" / "queue.jsonl"
    if not queue.exists():
        sys.exit(f"ERROR: {queue} not found")
    for line in queue.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("id") == ticket_id:
            return row
    sys.exit(f"ERROR: ticket {ticket_id} not in {queue}")


def is_directory_glob(territory: str) -> bool:
    """Directory territories are the recursive globs that serialize a subtree."""
    return territory == "**" or territory.endswith("/**")


def lint_queue(plan_dir: Path) -> dict:
    """Report recursive directory territories assigned to multiple tickets."""
    queue = plan_dir / "tickets" / "queue.jsonl"
    if not queue.exists():
        return {"ok": False, "errors": [f"{queue} not found"],
                "shared_directory_globs": []}
    owners: dict[str, list[str]] = {}
    errors: list[str] = []
    for line_number, line in enumerate(queue.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as err:
            errors.append(f"line {line_number}: invalid JSON ({err})")
            continue
        ticket_id = row.get("id") or f"line-{line_number}"
        for territory in set(row.get("territory", [])):
            if is_directory_glob(territory):
                owners.setdefault(territory, []).append(ticket_id)
    shared = [
        {"glob": territory, "tickets": sorted(ticket_ids)}
        for territory, ticket_ids in sorted(owners.items())
        if len(ticket_ids) > 1
    ]
    return {"ok": not errors and not shared, "errors": errors,
            "shared_directory_globs": shared}


def parse_utc_timestamp(value: str) -> datetime:
    """Parse queue timestamps and normalize them to timezone-aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def append_ttl_history(plan_dir: Path, ticket_id: str, receipt: dict) -> None:
    """Append one idempotent expiry receipt to the ticket's History."""
    ticket_file = plan_dir / "tickets" / f"{ticket_id}.md"
    if not ticket_file.exists():
        return
    marker = f"<!-- loop-ttl-clear:{ticket_id}:{receipt['approved_at']} -->"
    text = ticket_file.read_text()
    if marker in text:
        return
    line = (
        f"{marker}\n- Approved by TTL after {receipt['elapsed_hours']:.2f}h "
        f"({receipt['gate_class']}, limit {receipt['ttl_hours']}h); "
        f"veto: `{receipt['veto_command']}`.\n"
    )
    ticket_file.write_text(text.rstrip() + "\n\n" + line)


def sweep_gates(plan_dir: Path, now: datetime | None = None) -> dict:
    """Approve expired reversible gates while leaving irreversible gates untouched."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    queue = plan_dir / "tickets" / "queue.jsonl"
    if not queue.exists():
        return {"swept": [], "untouched": [],
                "errors": [f"{queue} not found"]}
    rows = [json.loads(line) for line in queue.read_text().splitlines()
            if line.strip()]
    swept: list[dict] = []
    untouched: list[dict] = []
    errors: list[str] = []
    changed = False
    for row in rows:
        if row.get("status") != "gated":
            continue
        ticket_id = row.get("id", "unknown")
        gate_class = row.get("gate_class")
        policy = GATE_POLICIES.get(gate_class, {"policy": "block"})
        if row.get("gate_policy", "block") != "ttl" or policy["policy"] != "ttl":
            untouched.append({"ticket": ticket_id, "reason": "block"})
            continue
        try:
            gated_at = parse_utc_timestamp(row["gated_at"])
        except (KeyError, TypeError, ValueError) as err:
            errors.append(f"{ticket_id}: invalid gated_at ({err})")
            continue
        ttl_hours = policy["ttl_hours"]
        elapsed_hours = (now - gated_at).total_seconds() / 3600
        if elapsed_hours < ttl_hours:
            untouched.append({"ticket": ticket_id, "reason": "unexpired",
                              "remaining_hours": ttl_hours - elapsed_hours})
            continue
        verdict_path = plan_dir / "tickets" / f"{ticket_id}.verdict.json"
        try:
            prior = json.loads(verdict_path.read_text())
        except (OSError, json.JSONDecodeError) as err:
            errors.append(f"{ticket_id}: gate verdict unavailable ({err})")
            continue
        shas = list(prior.get("commit_shas", []))
        if prior.get("satisfied_by") and prior["satisfied_by"] not in shas:
            shas.append(prior["satisfied_by"])
        if not shas:
            errors.append(f"{ticket_id}: gate verdict has no commit SHAs for veto")
            continue
        approved_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        receipt = {
            "kind": "approved-by-ttl",
            "approved_at": approved_at,
            "gate_class": gate_class,
            "ttl_hours": ttl_hours,
            "elapsed_hours": elapsed_hours,
            "paths": prior.get("blast_radius") or prior.get("paths", []),
            "commit_shas": shas,
            "veto_command": f"git revert --no-commit {' '.join(shas)}",
        }
        approved = dict(prior)
        approved.update({"outcome": "approved-by-ttl", "exit_code": 0,
                         "gate_class": gate_class, "gate_policy": "ttl",
                         "receipt": receipt})
        out = json.dumps(approved, indent=2) + "\n"
        verdict_path.write_text(out)
        (plan_dir / "tickets"
         / f"{ticket_id}.verdict-approved-by-ttl.json").write_text(out)
        append_ttl_history(plan_dir, ticket_id, receipt)
        row.update({"status": "done", "closed": approved_at,
                    "approved_by": "ttl"})
        swept.append({"ticket": ticket_id, "receipt": receipt})
        changed = True
    if changed:
        temp = queue.with_suffix(".jsonl.tmp")
        temp.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n"
                                for row in rows))
        temp.replace(queue)
    return {"swept": swept, "untouched": untouched, "errors": errors}


def run_verify(ticket: dict, plan_dir: Path, timeout: int) -> tuple[int, str]:
    cmd = ticket.get("verify")
    if not cmd:
        return -1, ""
    attempt = int(ticket.get("attempts", 0)) + 1
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            env=verify_environment(),
        )
        code, out = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        code, out = -2, f"TIMEOUT after {timeout}s"
    write_verify_log(plan_dir, ticket["id"], attempt, out, code)
    return code, out


def write_verify_log(plan_dir: Path, ticket_id: str, attempt: int,
                     output: str, code: int) -> None:
    """Write exactly one log block for an attempt, replacing a partial rerun."""
    log = plan_dir / "tickets" / f"{ticket_id}.verify.log"
    existing = log.read_text() if log.exists() else ""
    pattern = re.compile(
        rf"^=== attempt {attempt} .*?(?=^=== attempt |\Z)", re.M | re.S)
    existing = pattern.sub("", existing).rstrip()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = f"=== attempt {attempt} @ {ts} — exit {code} ===\n{output.rstrip()}\n"
    log.write_text((existing + "\n" if existing else "") + block)


def record_failed_attempt(plan_dir: Path, ticket_id: str, verdict: dict) -> int:
    """Atomically increment the durable queue counter for a failed adjudication."""
    queue = plan_dir / "tickets" / "queue.jsonl"
    rows = [json.loads(line) for line in queue.read_text().splitlines() if line.strip()]
    attempt = 0
    for row in rows:
        if row.get("id") == ticket_id:
            attempt = int(row.get("attempts", 0)) + 1
            row.update({"attempts": attempt, "status": "active",
                        "claimed_by": None, "claimed_at": None, "base_sha": None})
            break
    if not attempt:
        raise ValueError(f"ticket {ticket_id} missing from {queue}")
    temp = queue.with_suffix(".jsonl.tmp")
    temp.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n"
                            for row in rows))
    temp.replace(queue)
    verdict["attempt"] = attempt
    return attempt


def append_attempt_history(plan_dir: Path, ticket_id: str, attempt: int,
                           verdict: dict) -> None:
    """Append one retry receipt, keyed so repeated writes cannot duplicate it."""
    ticket_file = plan_dir / "tickets" / f"{ticket_id}.md"
    text = ticket_file.read_text()
    marker = f"<!-- loop-attempt:{ticket_id}:{attempt} -->"
    if marker in text:
        return
    receipt = (
        f"\n{marker}\n- Attempt {attempt} checker failure: verify exit "
        f"{verdict.get('verify_exit')}; verdict exit {verdict.get('exit_code')}.\n")
    ticket_file.write_text(text.rstrip() + "\n" + receipt)


def failure_fingerprint(output: str) -> str:
    """Hash the exact captured verify output so only byte-identical failures group."""
    return hashlib.sha256(output.encode()).hexdigest()


def correlated_failure_count(plan_dir: Path, ticket_id: str,
                             fingerprint: str) -> int:
    """Count this failure plus the immediately preceding identical failures."""
    verdicts = [path for path in (plan_dir / "tickets").glob("*.verdict.json")
                if path.stem.removesuffix(".verdict") != ticket_id]
    verdicts.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    count = 1
    for path in verdicts:
        try:
            prior = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            break
        if (prior.get("exit_code") != 1
                or prior.get("failure_fingerprint") != fingerprint):
            break
        count += 1
    return count


def assert_base_sha(base_sha: str, repo: Path) -> None:
    """Fail loudly when base_sha is not a commit in repo.

    Silence here is the whole bug: a base_sha captured in a different repository
    makes the log walk below return nothing, and territory plus blast-radius then
    both pass having inspected no files at all. git's own stderr is carried through
    so a missing object and a directory that isn't a checkout can't be confused.
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{base_sha}^{{commit}}"],
            cwd=repo, capture_output=True, text=True,
        )
    except OSError as err:
        sys.exit(f"ERROR: repo {repo} is not usable: {err}")
    if proc.returncode != 0:
        sys.exit(f"ERROR: base_sha {base_sha} is not a commit in {repo}. "
                 f"The ticket was claimed against a different repository. "
                 f"git: {proc.stderr.strip()}")


def resolve_window_base(base_sha: str, plan_slug: str, ticket_id: str,
                        claimed_at: str | None,
                        repo: Path) -> tuple[str | None, str]:
    """Existence, then ANCESTRY — and self-heal when a rewrite broke the window.

    `git cat-file -e` only proves the object exists, and an orphaned commit
    still exists via the reflog. So after a `pull --rebase` on a shared
    checkout (routine on this project — it fired mid-claim at 21:18 on
    2026-08-07), the recorded base passes the existence check while
    `base..HEAD` silently expands to everything since the rewrite point,
    and siblings' rebased commits false-gate the ticket as unattributed.

    Ancestry intact: the base is returned unchanged. Base orphaned by a
    rewrite: re-anchor deterministically to the parent of the ticket's
    EARLIEST prefixed commit in the current history (rebase preserves the
    prefix, which is why attribution survives rewrites), or HEAD when the
    executor hasn't landed anything. `claimed_at` bounds the prefix search so
    an identically-prefixed ticket from an old plan can't be picked up.
    Accepted residual, pinned in tests: after a re-anchor, unprefixed commits
    that landed between the claim and the executor's first commit escape the
    unattributed tripwire — the freeze rule remains the discipline there.

    Returns (effective_base_or_None, note). None means "no window bound"
    (unbounded log walk), which only happens in the no-parent root edge.
    """
    assert_base_sha(base_sha, repo)
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return base_sha, "intact"

    args = ["git", "log", "--format=%H", "--fixed-strings",
            "--grep", ticket_prefix(plan_slug, ticket_id)]
    if claimed_at:
        args += ["--since", claimed_at]
    hits = subprocess.run(args + ["HEAD"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.split()
    if hits:
        earliest = hits[-1]
        parent = subprocess.run(["git", "rev-parse", f"{earliest}^"],
                                cwd=repo, capture_output=True, text=True)
        new_base = parent.stdout.strip() if parent.returncode == 0 else None
    else:
        new_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                  capture_output=True, text=True,
                                  check=True).stdout.strip()
    label = new_base[:12] if new_base else "history root"
    return new_base, (f"base_sha {base_sha[:12]} orphaned by a history rewrite; "
                      f"window re-anchored to {label}")


def ticket_commits(base_sha: str, plan_slug: str, ticket_id: str,
                   repo: Path) -> list[str]:
    """SHAs in base_sha..HEAD carrying this plan and ticket's exact prefix.

    Attribution is never by time window or ticket number alone. Every plan starts
    at T-001, so matching only '/T-001):' cross-attributes concurrent loops. The
    window bounds the search and the exact plan-scoped prefix selects within it.
    """
    window = "HEAD" if base_sha is None else f"{base_sha}..HEAD"
    proc = subprocess.run(
        ["git", "log", "--format=%H", "--fixed-strings",
         "--grep", ticket_prefix(plan_slug, ticket_id), window],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return [s for s in proc.stdout.splitlines() if s.strip()]


def earliest_ticket_commit_parent(plan_slug: str, ticket_id: str,
                                  repo: Path) -> tuple[str, str] | None:
    """Return the earliest attributed commit and its parent in current history."""
    proc = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", "--fixed-strings",
         "--grep", ticket_prefix(plan_slug, ticket_id), "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    commits = [sha for sha in proc.stdout.splitlines() if sha.strip()]
    if not commits:
        return None
    earliest = commits[0]
    parent = subprocess.run(
        ["git", "rev-parse", f"{earliest}^"], cwd=repo,
        capture_output=True, text=True,
    )
    if parent.returncode != 0:
        return None
    return earliest, parent.stdout.strip()


def commit_paths(shas: list[str], repo: Path) -> list[str]:
    """Union of paths touched by the given commits, first-seen order."""
    paths: list[str] = []
    for sha in shas:
        proc = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        for p in proc.stdout.splitlines():
            if p.strip() and p not in paths:
                paths.append(p)
    return paths


def added_commit_paths(shas: list[str], repo: Path) -> set[str]:
    """Paths introduced by the ticket commits, used to separate new migrations."""
    paths: set[str] = set()
    for sha in shas:
        proc = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only",
             "--diff-filter=A", "-r", sha],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        paths.update(path for path in proc.stdout.splitlines() if path.strip())
    return paths


def commit_diff(sha: str, paths: list[str], repo: Path) -> str:
    """Render the committed patch for a receipt, limited to named paths."""
    proc = subprocess.run(
        ["git", "show", "--format=", "--no-ext-diff", "--unified=3",
         sha, "--", *paths],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return proc.stdout


def is_sanctioned_bookkeeping_commit(sha: str, plan_dir: Path,
                                     ticket_id: str, repo: Path) -> bool:
    """True when a commit contains only queue and this ticket's audit files."""
    paths = commit_paths([sha], repo)
    if not paths:
        return False
    queue = (plan_dir / "tickets" / "queue.jsonl").as_posix()
    own = (plan_dir / "tickets" / f"{ticket_id}*").as_posix()
    return all(path == queue or matches_any(path, [own]) for path in paths)


def migration_identity(path: str) -> tuple[str, str] | None:
    """Return (registry directory, version) for a recognized SQL migration."""
    parts = Path(path).parts
    indices = [i for i, part in enumerate(parts)
               if part in {"migrations", "migrations_pg"}]
    if not indices or not path.endswith(".sql"):
        return None
    match = re.match(r"^([^_]+)_", parts[-1])
    if not match:
        return None
    index = indices[-1]
    return "/".join(parts[:index + 1]), match.group(1)


def migration_version_collisions(base_sha: str, candidate_paths: list[str],
                                 repo: Path) -> list[str]:
    """Find candidate versions already registered in the claim's target tree.

    Same-path matches are excluded (T-031): commit_paths is name-only, so a
    ticket DELETING or EDITING a migration lists the very path the base tree
    registers, and the file "collided" with itself — a live ticket that
    correctly git-rm'd a withdrawn, never-applied migration parked
    on exit-1 retries with no human asked. A version in the SAME file is that
    file being edited or removed, which the migrations blast glob already
    gates to a human; only the same version appearing in a DIFFERENT file is
    a genuine registry collision.
    """
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base_sha], cwd=repo,
        capture_output=True, text=True, check=True,
    )
    registry: dict[tuple[str, str], str] = {}
    for existing in proc.stdout.splitlines():
        identity = migration_identity(existing)
        if identity:
            registry.setdefault(identity, existing)
    problems: list[str] = []
    for candidate in candidate_paths:
        identity = migration_identity(candidate)
        if (identity and identity in registry
                and registry[identity] != candidate):
            directory, version = identity
            problems.append(
                f"migration version {version} in {directory} already exists on "
                f"the target tree as {registry[identity]}")
    return problems


def classify_migration_apply(*, checker_green: bool, opted_in: bool,
                             drift_exit: int | None,
                             blast_paths: list[str]) -> str:
    """Return the only allowed next action for a migration set."""
    if not checker_green:
        return "blocked"
    if not opted_in or drift_exit != 0 or blast_paths:
        return "human_gate"
    return "auto_apply"


def assess_migration_apply(checker_green: bool, opted_in: bool,
                           blast_paths: list[str], drift_check) -> tuple[str, int | None]:
    """Run drift before classifying, including the blast-radius path."""
    drift_exit = drift_check() if opted_in else None
    return (classify_migration_apply(
        checker_green=checker_green, opted_in=opted_in,
        drift_exit=drift_exit, blast_paths=blast_paths), drift_exit)


def read_apply_policy(plan_dir: Path) -> dict:
    """Load an explicit plan opt-in; absence preserves human-gate-all."""
    path = plan_dir / "apply-policy.json"
    if not path.exists():
        return {"enabled": False}
    policy = json.loads(path.read_text())
    policy["enabled"] = policy.get("enabled") is True
    return policy


def run_drift_check(cmd: str | None, repo: Path, timeout: int,
                    log: Path) -> int:
    """Run and record the plan's drift command; a missing command fails closed."""
    if not cmd:
        log.write_text("apply policy has no drift_check command\n")
        return -1
    try:
        proc = subprocess.run(cmd, cwd=repo, shell=True, capture_output=True,
                              text=True, timeout=timeout)
        output = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        output = f"TIMEOUT after {timeout}s"
        code = -2
    log.write_text(output)
    return code


def satisfied_commit_checks(sha: str, ticket: dict, plan_dir: Path,
                            repo: Path) -> tuple[list[str], list[str]]:
    """Validate an owner-landed commit against HEAD and the ticket declaration."""
    problems: list[str] = []
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo,
        capture_output=True, text=True,
    )
    if exists.returncode != 0:
        return [], [f"satisfied-by commit {sha} does not exist in {repo}"]
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=repo,
        capture_output=True, text=True,
    )
    if ancestor.returncode != 0:
        problems.append(f"satisfied-by commit {sha} is not an ancestor of HEAD")
    paths = commit_paths([sha], repo)
    allowed = list(ticket.get("territory", []))
    allowed.append(f"{plan_dir}/tickets/{ticket['id']}*")
    outside = [path for path in paths if not matches_any(path, allowed)]
    problems.extend(f"satisfied-by commit touched undeclared path: {path}"
                    for path in outside)
    if not paths:
        problems.append("satisfied-by commit touched no files")
    return paths, problems


def append_satisfied_history(plan_dir: Path, ticket_id: str, sha: str,
                             paths: list[str]) -> None:
    """Write one durable, idempotent close receipt into the ticket History."""
    ticket_file = plan_dir / "tickets" / f"{ticket_id}.md"
    text = ticket_file.read_text()
    receipt = (f"- Satisfied by commit `{sha}` after checker verification; "
               f"declared paths: {', '.join(f'`{p}`' for p in paths)}.")
    if receipt in text:
        return
    if not re.search(r"^## History\s*$", text, re.M):
        raise ValueError(f"{ticket_file.name} has no History section")
    separator = "" if text.endswith("\n") else "\n"
    ticket_file.write_text(text + separator + receipt + "\n")


def window_commits(base_sha: str, repo: Path) -> list[str]:
    """Every SHA in the window, prefix or not."""
    window = "HEAD" if base_sha is None else f"{base_sha}..HEAD"
    proc = subprocess.run(
        ["git", "log", "--format=%H", window],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return [s for s in proc.stdout.splitlines() if s.strip()]


def commit_author(sha: str, repo: Path) -> dict[str, str]:
    """Return stable author fields for a receipt or cause decision."""
    proc = subprocess.run(
        ["git", "show", "-s", "--format=%an%x00%ae", sha], cwd=repo,
        capture_output=True, text=True, check=True,
    )
    name, email = proc.stdout.rstrip("\n").split("\0", 1)
    return {"name": name, "email": email}


def seat_email(repo: Path) -> str | None:
    """The repository's configured author email identifies the active human seat."""
    proc = subprocess.run(
        ["git", "config", "--get", "user.email"], cwd=repo,
        capture_output=True, text=True,
    )
    return proc.stdout.strip().lower() if proc.returncode == 0 else None


def is_preclaim_upstream(sha: str, claimed_at: str | None, repo: Path) -> bool:
    """True for an origin/main ancestor committed no later than the claim."""
    if not claimed_at:
        return False
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=repo,
        capture_output=True, text=True,
    )
    if ancestor.returncode != 0:
        return False
    committed = subprocess.run(
        ["git", "show", "-s", "--format=%cI", sha], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    try:
        return datetime.fromisoformat(committed) <= datetime.fromisoformat(
            claimed_at.replace("Z", "+00:00"))
    except ValueError:
        return False


def unattributed_in_territory(base_sha: str, attributed: list[str],
                              territory: list[str], repo: Path,
                              plan_slug: str | None = None,
                              plan_dir: Path | None = None,
                              ticket_id: str | None = None,
                              claimed_at: str | None = None,
                              ) -> tuple[list[str], list[str], list[dict]]:
    """Window commits WITHOUT the ticket's prefix that touch its territory.

    Closes the fail-open half of attribution: a commit missing its prefix is
    invisible to the territory check above, so an executor's mis-prefixed
    commit (or another session violating the territory freeze) would pass
    silently. It can't be auto-attributed — same author, same machine — so it
    becomes a GATED condition: a human looks, then clears or fails the ticket.
    Unprefixed commits outside territory are other sessions' legitimate work
    and stay ignored.

    T-026: a window commit carrying THIS PLAN's prefix for a DIFFERENT ticket
    is a batch sibling's own work — fully identified, and adjudicated by its
    own ticket's checker run — so it returns in the second list (informational
    `attributed_to_sibling`), never in the gate-worthy first. The first live
    batch (T-021+T-025, 2026-08-14) cross-gated both tickets exactly here. A
    foreign PLAN's prefix and an unprefixed commit both stay unattributed:
    every plan starts at T-001, so only this plan's slug identifies a sibling.
    """
    hits: list[str] = []
    siblings: list[str] = []
    benign: list[dict] = []
    seen = set(attributed)
    configured_seat_email = seat_email(repo)
    sibling_rx = (re.compile(re.escape(f"loop({plan_slug}/") + r"T-\d+\):")
                  if plan_slug else None)
    for sha in window_commits(base_sha, repo):
        if sha in seen:
            continue
        if (plan_dir is not None and ticket_id is not None
                and is_sanctioned_bookkeeping_commit(
                    sha, plan_dir, ticket_id, repo)):
            continue
        touched = [p for p in commit_paths([sha], repo)
                   if matches_any(p, territory)]
        if not touched:
            continue
        message = subprocess.run(
            ["git", "log", "-1", "--format=%B", sha], cwd=repo,
            capture_output=True, text=True, check=True).stdout
        author = commit_author(sha, repo)
        cause = None
        if sibling_rx and sibling_rx.search(message):
            cause = "same_plan_sibling"
            siblings.extend(f"{sha[:12]}: {p}" for p in touched)
        elif (configured_seat_email
              and author["email"].lower() == configured_seat_email):
            cause = "seat_human_author"
        elif is_preclaim_upstream(sha, claimed_at, repo):
            cause = "pre_claim_upstream_ancestor"
        if cause:
            benign.append({"sha": sha, "author": author, "cause": cause,
                           "paths": touched})
        else:
            hits.extend(f"{sha[:12]}: {p}" for p in touched)
    return hits, siblings, benign


def porcelain_paths(repo: Path) -> list[str]:
    """Uncommitted paths, -uall so files under a brand-new directory are
    listed individually instead of collapsing to 'newdir/' and dodging the
    territory scoping below."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return [ln[3:].strip() for ln in status.stdout.splitlines() if ln.strip()]


def ticket_file_committed(plan_dir: Path, ticket_id: str) -> list[str]:
    """The ticket file must exist in the workspace repo's HEAD.

    The evidence lint reads the working tree, the uncommitted scan excludes the
    tickets/ dir (T-014), and `allowed` merely permits the path — so nothing
    else asserts the file was ever committed. A filled ticket file that exists
    only in a working tree passes every gate and then vanishes on a clean
    checkout, taking Evidence and Findings with it. HEAD presence, not
    cleanliness: the driver appends History between attempts without
    committing, and a cleanliness requirement would fail every retry. Always
    the checker's own repo, never --repo: cross-repo tickets keep their file
    in the workspace by contract.
    """
    path = (plan_dir / "tickets" / f"{ticket_id}.md").as_posix()
    proc = subprocess.run(["git", "cat-file", "-e", f"HEAD:{path}"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return [f"{path} is not committed in the workspace repo — "
                "evidence vanishes on a clean checkout"]
    return []


def lint_evidence(plan_dir: Path, ticket_id: str) -> list[str]:
    """Missing or empty required sections in T-###.md."""
    md = plan_dir / "tickets" / f"{ticket_id}.md"
    if not md.exists():
        return [f"{md.name} missing"]
    text = md.read_text()
    problems = []
    for section in REQUIRED_SECTIONS:
        m = re.search(rf"^## {re.escape(section)}\s*$(.*?)(?=^## |\Z)",
                      text, re.M | re.S)
        if not m:
            problems.append(f"section missing: {section}")
        elif not m.group(1).strip():
            problems.append(f"section empty: {section}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan-dir", required=True, help="e.g. plans/2026-08-07-example")
    ap.add_argument("--ticket", help="e.g. T-001")
    ap.add_argument("--lint-queue", action="store_true",
                    help="reject recursive directory territories shared by tickets")
    ap.add_argument("--sweep-gates", action="store_true",
                    help="approve elapsed ttl-policy gates and write receipts")
    ap.add_argument("--blast-globs-file",
                    help="file of globs (one per line, # comments) replacing the defaults")
    ap.add_argument("--repo", default=".",
                    help="repo the ticket's work happened in (default: the driver's own)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="verify command timeout in seconds (default 600)")
    ap.add_argument("--satisfied-by", metavar="SHA",
                    help="owner-controlled close: verify one landed commit")
    args = ap.parse_args()

    plan_dir = Path(args.plan_dir)
    if args.lint_queue and args.sweep_gates:
        ap.error("--lint-queue and --sweep-gates are mutually exclusive")
    if args.lint_queue:
        result = lint_queue(plan_dir)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 1)
    if args.sweep_gates:
        result = sweep_gates(plan_dir)
        print(json.dumps(result, indent=2))
        sys.exit(0 if not result["errors"] else 1)
    if not args.ticket:
        ap.error("--ticket is required unless --lint-queue is used")
    ticket = read_ticket(plan_dir, args.ticket)

    blast_globs = DEFAULT_BLAST_GLOBS
    if args.blast_globs_file:
        blast_globs = parse_blast_globs(Path(args.blast_globs_file).read_text())

    verdict = {"ticket": args.ticket, "verify": "fail", "verify_exit": None,
               "verify_string": [], "receipt": [], "commits": 0, "window": None, "attribution": [],
               "territory": [], "uncommitted": [], "unattributed": [],
               "attributed_to_sibling": [],
               "benign_unattributed": [],
               "blast_radius": [], "evidence": [], "ticket_file": [],
               "closure": [], "satisfied_by": args.satisfied_by, "paths": [],
               "commit_shas": [], "ship_class": False,
               "gate_class": None, "gate_policy": None}
    verdict.update(verdict_telemetry(ticket))
    verdict["failure_fingerprint"] = None
    verdict["correlated_failure"] = []
    verdict["registerability"] = []
    verdict["apply"] = {"action": "not_applicable", "opted_in": False,
                        "drift_exit": None, "command": None}

    # 1. verify — but refuse a string that can't run as written before the seam
    # tries it, so a placeholder or a bad cd fails attempt 1 named, not parks at 3.
    verdict["verify_string"] = validate_verify(ticket.get("verify"))
    if verdict["verify_string"]:
        pass  # not run; the string can't. verify stays "fail".
    else:
        code, verify_output = run_verify(ticket, plan_dir, args.timeout)
        verdict["verify_exit"] = code
        if code == -1:
            verdict["evidence"].append("no verify command declared")
        elif code == 0:
            verdict["verify"] = "pass"
            verdict["receipt"] = check_receipt(verify_output,
                                               ticket.get("verify_expect"))
        else:
            fingerprint = failure_fingerprint(verify_output)
            verdict["failure_fingerprint"] = fingerprint
            count = correlated_failure_count(plan_dir, args.ticket, fingerprint)
            if count >= 3:
                verdict["correlated_failure"].append(
                    f"shared-harness pause: {count} consecutive byte-identical "
                    f"verify failures ({fingerprint[:12]})")

    # 2. attribution + territory (own ticket file + verify log are always
    # allowed; queue.jsonl is NOT — an executor committing it is exactly what
    # this check must catch)
    cwd = Path(args.repo).expanduser()
    verdict["material_digest"] = material_digest(
        list(ticket.get("territory", [])), cwd)
    added_paths: set[str] = set()
    if args.satisfied_by:
        verdict["paths"], verdict["closure"] = satisfied_commit_checks(
            args.satisfied_by, ticket, plan_dir, cwd)
        verdict["commits"] = 1 if not verdict["closure"] else 0
        verdict["commit_shas"] = [args.satisfied_by]
        added_paths = added_commit_paths([args.satisfied_by], cwd)
        verdict["territory"] = [problem for problem in verdict["closure"]
                                if "undeclared path" in problem]
        verdict["blast_radius"] = [
            path for path in verdict["paths"] if matches_any(path, blast_globs)
        ]
        if ticket.get("base_sha"):
            verdict["registerability"] = migration_version_collisions(
                ticket["base_sha"], verdict["paths"], cwd)
    else:
        if ticket.get("mode") == "core":
            verdict["closure"].append(
                "owner-controlled core ticket requires --satisfied-by <sha>")
        base_sha = ticket.get("base_sha")
        if not base_sha:
            verdict["attribution"].append(
                "base_sha missing — the ticket was never claimed, or a failed "
                "attempt reset the claim (exit 1 clears claimed_by/base_sha); "
                "re-claim before re-adjudicating")
            anchor = earliest_ticket_commit_parent(
                plan_dir.name, args.ticket, cwd)
            if anchor:
                earliest, parent = anchor
                verdict["attribution"].append(
                    f"cleared-claim re-anchor: use {parent} (parent of earliest "
                    f"attributed commit {earliest}), never current HEAD")
        else:
            plan_slug = plan_dir.name
            base_sha, verdict["window"] = resolve_window_base(
                base_sha, plan_slug, args.ticket, ticket.get("claimed_at"), cwd)
            shas = ticket_commits(base_sha, plan_slug, args.ticket, cwd)
            work_shas = [
                sha for sha in shas
                if not is_sanctioned_bookkeeping_commit(
                    sha, plan_dir, args.ticket, cwd)
            ]
            if not work_shas and shas and ticket.get("mode") == "ship":
                # A ship ticket lands no new product code by definition: its
                # work is the phase's commits, which all predate its claim,
                # and its receipt is the verify gate (the whole loop, green),
                # not an attribution count. Without this, every commit inside
                # its window is bookkeeping, work_shas is necessarily empty,
                # and exit 0 is structurally unreachable for the whole class —
                # measured on T-412 (2026-08-18): the identical commits
                # counted 2 pre-guard and 0 post-guard. The fallback is
                # deliberately narrow: it fires only when the guard emptied
                # the set, so a ship ticket that DID land product code is
                # still adjudicated on that code alone.
                work_shas = shas
                verdict["ship_class"] = True
            verdict["commits"] = len(work_shas)
            verdict["commit_shas"] = work_shas
            if not work_shas:
                verdict["attribution"].append(
                    f"no non-bookkeeping commits matching "
                    f"'{ticket_prefix(plan_slug, args.ticket)}' in "
                    f"{base_sha[:12]}..HEAD — nothing landed, or the commit "
                    "prefix is missing")
            territory = list(ticket.get("territory", []))
            allowed = territory + [f"{plan_dir}/tickets/{args.ticket}*"]
            committed = commit_paths(work_shas, cwd)
            added_paths = added_commit_paths(work_shas, cwd)
            verdict["paths"] = committed
            verdict["territory"] = [p for p in committed if not matches_any(p, allowed)]
            tickets_dir = (Path(args.plan_dir) / "tickets").as_posix() + "/"
            verdict["uncommitted"] = [p for p in porcelain_paths(cwd)
                                      if matches_any(p, territory)
                                      and not p.startswith(tickets_dir)]
            (verdict["unattributed"], verdict["attributed_to_sibling"],
             verdict["benign_unattributed"]) = unattributed_in_territory(
                base_sha, shas, territory, cwd, plan_slug,
                plan_dir, args.ticket, ticket.get("claimed_at"))
            verdict["blast_radius"] = [p for p in committed
                                       if matches_any(p, blast_globs)]
            verdict["registerability"] = migration_version_collisions(
                base_sha, committed, cwd)

    # 4. evidence lint
    verdict["evidence"] += lint_evidence(plan_dir, args.ticket)

    # 5. the ticket file itself is committed (always the workspace repo, never --repo)
    verdict["ticket_file"] = ticket_file_committed(plan_dir, args.ticket)

    migration_paths = [path for path in verdict["paths"]
                       if migration_identity(path)]
    if migration_paths:
        policy = read_apply_policy(plan_dir)
        opted_in = policy.get("enabled") is True
        checker_green = not (
            verdict["verify"] != "pass" or verdict["verify_string"]
            or verdict["attribution"] or verdict["territory"]
            or verdict["uncommitted"] or verdict["unattributed"]
            or verdict["evidence"] or verdict["ticket_file"]
            or verdict["closure"] or verdict["registerability"]
            or verdict["correlated_failure"])
        drift_log = plan_dir / "tickets" / f"{args.ticket}.drift.log"
        action, drift_exit = assess_migration_apply(
            checker_green, opted_in, verdict["blast_radius"],
            lambda: run_drift_check(policy.get("drift_check"), cwd,
                                    args.timeout, drift_log),
        )
        verdict["apply"] = {
            "action": action,
            "opted_in": opted_in,
            "drift_exit": drift_exit,
            "command": policy.get("apply") if opted_in else None,
            "migrations": migration_paths,
        }

    failed = (verdict["verify"] != "pass" or verdict["verify_string"]
              or verdict["receipt"]
              or verdict["attribution"] or verdict["territory"]
              or verdict["uncommitted"] or verdict["evidence"]
              or verdict["ticket_file"] or verdict["closure"]
              or verdict["registerability"])
    judge_file_receipt = bool(
        args.satisfied_by and verdict["blast_radius"]
        and all(matches_any(path, JUDGE_FILE_GLOBS)
                for path in verdict["blast_radius"])
    )
    gated = ((verdict["blast_radius"] and not judge_file_receipt)
             or verdict["unattributed"]
             or (verdict["apply"]["opted_in"]
                 and verdict["apply"]["action"] == "human_gate"))
    exit_code = 2 if verdict["correlated_failure"] else (
        1 if failed else (2 if gated else 0))
    verdict["exit_code"] = exit_code
    if exit_code == 2:
        verdict["gate_class"] = derive_gate_class(verdict, added_paths)
        policy = GATE_POLICIES[verdict["gate_class"]]
        verdict["gate_policy"] = policy["policy"]
        if verdict["gate_class"] in {
                "unattributed_commit", "blast_judge_file"}:
            verdict["gate_policy"] = "block"
        if "ttl_hours" in policy:
            verdict["gate_ttl_hours"] = policy["ttl_hours"]
    elif exit_code == 0 and judge_file_receipt:
        verdict["gate_class"] = "blast_judge_file"
        verdict["gate_policy"] = "receipt"
        verdict["receipt"] = {
            "kind": "satisfied-by-judge-file",
            "sha": args.satisfied_by,
            "paths": verdict["blast_radius"],
            "diff": commit_diff(
                args.satisfied_by, verdict["blast_radius"], cwd),
            "veto_command": f"git revert --no-commit {args.satisfied_by}",
        }
    elif exit_code == 0 and verdict["benign_unattributed"]:
        shas = [entry["sha"] for entry in verdict["benign_unattributed"]]
        verdict["gate_class"] = "unattributed_commit"
        verdict["gate_policy"] = "receipt"
        verdict["receipt"] = {
            "kind": "benign-unattributed",
            "commits": verdict["benign_unattributed"],
            "veto_command": f"git revert --no-commit {' '.join(shas)}",
        }

    if args.satisfied_by and exit_code == 0:
        append_satisfied_history(plan_dir, args.ticket, args.satisfied_by,
                                 verdict["paths"])
    elif not args.satisfied_by and exit_code == 1:
        attempt = record_failed_attempt(plan_dir, args.ticket, verdict)
        append_attempt_history(plan_dir, args.ticket, attempt, verdict)

    # The verdict file is the adjudication record the driver reads — never a
    # shell capture of this process's exit status (a piped tail's $? reported
    # exit 0 on a failing ticket, 2026-08-07). Stdout is a convenience copy.
    # T-005: every adjudication ALSO lands a per-attempt copy. verdict.json is
    # rewritten each run, so before this, attempt N-1's structured verdict was
    # destroyed and an after-the-fact audit fell back to prose. The latest-file
    # contract is unchanged; the attempt files are the retained history.
    out = json.dumps(verdict, indent=2)
    (plan_dir / "tickets" / f"{args.ticket}.verdict.json").write_text(out + "\n")
    attempt_n = verdict.get("attempt") or int(ticket.get("attempts", 0)) + 1
    (plan_dir / "tickets"
     / f"{args.ticket}.verdict-attempt-{attempt_n}.json").write_text(out + "\n")
    print(out)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
