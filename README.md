# agent-loop

A coding agent that says "done, all tests pass" is reporting on itself. This repo removes
that report from the decision.

`agent-loop` is a dispatch loop with one rule underneath it: **a ticket closes only when a
deterministic checker says so.** The checker re-runs the ticket's own verify command, walks
the commits the executor actually made, matches every changed path against the territory the
ticket declared, scans those paths for blast radius, lints the evidence, and writes a verdict
file. The driver reads the verdict file. Nothing an agent says about its own work moves a
ticket to `done`.

Everything else here exists to make that rule enforceable: one fresh agent per ticket with an
empty context window, a verify guard that refuses to run against live credentials or
uncommitted code, commit attribution by an exact per-plan prefix, and a queue only the driver
writes. Work the loop discovers along the way becomes new tickets and gets worked too, until
the queue runs dry.

It also talks to you. When a run ends, it can send a spoken briefing to your phone as a
Telegram voice note, using [Fish Audio](https://fish.audio/?fpr=jesse28) for the speech. That
part is optional and the loop runs fine without it.

## How a run actually goes

1. You hand the driver a plan with a ticket manifest. It materializes `queue.jsonl` plus one
   markdown file per ticket.
2. The driver claims one ticket, records the current HEAD as `base_sha`, and dispatches a
   fresh executor with the ticket, its territory, and the standards. The executor never sees
   the driver's reasoning. That separation is the only reason the verdict means anything.
3. The executor writes its implementation plan into the ticket, makes the change, commits by
   explicit pathspec under the prefix `loop(<plan-slug>/T-00N):`, runs the verify through the
   guard, and fills in evidence and findings.
4. The driver runs `loop_check.py` on the ticket and reads `T-00N.verdict.json`. Exit 0 closes
   it. Exit 1 sends it back with the attempt logged, and three failures park it. Exit 2 means
   the diff touched something in the blast radius, so it gates.
5. Findings from the closed ticket become new tickets. Back to step 2.

A run ends when the ready set is empty or the dispatch budget runs out. Then it writes a Loop
Report and a retrospective, and (if you set up the voice channel) tells you out loud.

## What's in here

```
skills/run-loop/SKILL.md      the driver: the state machine, the phases, the rules
skills/plan-loop/SKILL.md     writes a loop-shaped plan the driver can materialize
scripts/loop_check.py         the checker. The only path from in_review to done
scripts/loop_voice.py         the notifier: text to speech, to Telegram voice note
scripts/tests/                98 tests for the notifier, no network, 0.1 seconds
tools/verify_seam.sh          the guard every verify runs through
config/blast-globs.txt        which changes stop for a human
config/live-credentials.txt   which environment names the guard refuses
```

The skills are plain markdown. The engine is Python and bash, standard library only, no
virtualenv, no install step. That's deliberate: this was built inside Claude Code, but nothing
here is Claude Code, so a Codex or Cursor loop can read the same skill file and drive the same
checker.

## Requirements

- **Python 3.10 or newer.** `loop_check.py` uses `X | None` annotations at import time.
- **git**, because attribution, the claim window and territory matching are all commits.
- **bash**, for the verify guard.
- **pytest**, only if you want to run the notifier's test suite.

Two more things are optional and covered below: the `claude` binary and `ffmpeg`. Neither is
needed for a loop to run.

## Install

### As a Claude Code plugin

```
/plugin marketplace add jdrurka/agent-loop
/plugin install agent-loop@agent-loop
```

Restart, and `/run-loop <path-to-your-plan>` is available. Claude Code namespaces plugin
skills, so the fully qualified form is `/agent-loop:run-loop` if the short name collides with
something else you have installed.

To try it out of a clone without installing anything, point a single session at the directory:

```
claude --plugin-dir ~/agent-loop
```

### As a plain clone

```
git clone https://github.com/jdrurka/agent-loop.git ~/agent-loop
```

There's nothing to build. Two roots matter from here on, and the difference shows up in every
command:

- `<engine>` is the clone (`~/agent-loop` above). It holds the scripts, the guard and the
  config.
- `<project>` is the repo the work lands in, and the directory that holds `plans/`.

The checker resolves `--plan-dir` against the working directory, so run it from `<project>`:

```
cd ~/code/my-project
python3 ~/agent-loop/scripts/loop_check.py --plan-dir plans/my-plan --ticket T-001 --repo .
```

And the guard wraps whatever your verify is:

```
bash ~/agent-loop/tools/verify_seam.sh --repo . \
  --path src/thing.py --path tests/test_thing.py \
  --run 'python3 -m pytest tests/test_thing.py -q'
```

For the driver itself on a harness that isn't Claude Code, hand your agent
`~/agent-loop/skills/run-loop/SKILL.md` and tell it to follow it as written. It's a protocol
document, not a plugin format. Those two commands, plus the notifier below, are the whole
interface between the skill and the machine.

## Your first loop

Write a plan with two header lines the driver reads:

```markdown
**Execution:** loop
**Thoroughness:** velocity
```

Then a `## Ticket Manifest` section: a table of tickets, each with an id, a trace, acceptance
criteria, a territory glob, a runnable verify command, and a receipt. The receipt is the part
people skip and shouldn't. It's a regex the verify's own output has to match, and it exists
because exit 0 means "nothing errored", not "the assertion ran". A pytest verify gets
`\d+ passed`. A shell script gets whatever token it prints on success, and if it prints
nothing, give it one.

`/plan-loop` writes that shape for you. You can also write it by hand; the driver only cares
about the section, not who typed it.

Then run the driver against the plan and let it work. Budget defaults to 15 dispatches per
run, and it asks before taking more.

## Voice notes on your phone

`scripts/loop_voice.py` turns a run-end line into speech and sends it to you on Telegram as a
real voice note. Long runs are the point: you go do something else, and the loop briefs you
when it's finished or when a gate needs you.

Three values set it up. All three live in files on disk, never in environment variables.

**1 and 2. A Telegram bot token and a chat id.** Message [@BotFather](https://t.me/BotFather)
on Telegram, send `/newbot`, and it hands you a token. Then message your new bot once, and
read your chat id out of `https://api.telegram.org/bot<token>/getUpdates`.

```bash
mkdir -p ~/.config/loop-voice
cat > ~/.config/loop-voice/telegram.json <<'JSON'
{
  "bot_token": "123456789:AA...",
  "chat_id": "987654321"
}
JSON
chmod 600 ~/.config/loop-voice/telegram.json
```

**3. A Fish Audio API key.** Get one at [fish.audio](https://fish.audio/?fpr=jesse28), pick or
clone a voice, and copy its id.

```bash
mkdir -p ~/.config/fish-audio
cat > ~/.config/fish-audio/speak.json <<'JSON'
{
  "apiKey": "your-fish-key",
  "voiceId": "the-voice-reference-id",
  "model": "s2.1-pro-free"
}
JSON
chmod 600 ~/.config/fish-audio/speak.json
```

Use `s2.1-pro-free` while you're setting up and `s2.1-pro` once you care about latency
guarantees. Stay on an S2 model either way: the notifier steers delivery with Fish's
`[bracket]` cues, and S1 uses a different syntax, so an S1 voice reads the brackets out loud
instead of acting on them.

`apiKey`, `voiceId` and `model` are all required, and a missing or empty one exits 3 rather
than half-sending something. Optional keys that file also reads: `speed`, `summarize` (set it
to `false` to send your text verbatim), `summaryModel`, `summaryStyle`, and `rewriteTimeout`
in seconds (default 40, capped at 300, and anything non-numeric or out of range falls back to
the default). If that file already has a `format` key from another tool, it's ignored here.
The notifier always asks Fish for opus.

Check the channel before you rely on it:

```bash
python3 ~/agent-loop/scripts/loop_voice.py --prove --run-context "setup/first-check"
```

Exit codes are the contract: `0` delivered (by voice, or by the text fallback), `2` it didn't
land anywhere, `3` misconfigured. Never best-effort, never silent.

Skip all of this and the loop is unchanged. With no pointer files on disk the driver never
calls the notifier, so there's no note and no complaint about the missing one. (Call the
script directly anyway and it does tell you, with exit 3 and the path it couldn't read.)

### Why files on disk instead of environment variables

This is the detail worth stealing even if you never use the voice channel.

`tools/verify_seam.sh` refuses to run a verify while any credential-shaped environment name is
set. The list lives in `config/live-credentials.txt`, and if that file is missing or empty the
guard refuses to run at all, because an empty denylist and a wide-open one look identical from
inside. `FISH_API_KEY`, `FISH_AUDIO_API_KEY`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are
all on that list.

So a notifier that read its keys from the environment could never be called from inside a
verify. It would be locked out by the same guard that protects your production database. The
pointer files are what let the voice channel work without cutting a hole in the guard: the
script reads them at call time, they're never logged or printed, and both paths are
overridable with `--fish-config` and `--telegram-config`, which is how all 98 tests run
without touching a real config.

## The two optional dependencies

Both are enhancements. The repo works without either one, and each has a defined fallback.

### `claude` on PATH (optional)

A run-end line reads like a status dump. The rewrite step turns it into something you'd
actually say out loud, by shelling out to `claude -p` on Haiku with a briefing prompt, then
checking the result before it's spoken. When your message carries counts, the non-zero ones
have to come back (as digits or as spoken words), and at least a couple of content words have
to survive with them. Editorial commentary is rejected either way, because a model that
explains what it changed will otherwise have that explanation read aloud to you.

If the binary isn't there, it prints

```
loop_voice: briefing rewrite skipped: claude binary not found
```

to stderr and sends your message exactly as you wrote it. Same behaviour on a timeout, a
non-zero exit, empty output, or a rewrite that failed the checks above, each with its own
line on stderr naming which one fired. A failed rewrite never blocks the send; you just get
the terse version.

### `ffmpeg` (optional)

Telegram's `sendVoice` wants OGG. Fish returns opus, and when those bytes already arrive in an
OGG container, nothing converts and `ffmpeg` is never called. When they don't, `ffmpeg`
rewraps them.

Without `ffmpeg` on that path, the conversion returns nothing and delivery falls through to a
plain Telegram text message with the same content. That's still exit 0, and stdout says which
route it took:

```
delivered: text :: my-plan/run-3/my-mac :: T-004 done, nothing parked...
```

So `delivered: voice` versus `delivered: text` is how you tell, and there's no stderr
diagnostic on the missing-`ffmpeg` path specifically.

## Making it yours

Three files decide how the engine behaves in your repo.

**`config/blast-globs.txt`** is the one to read first. It answers a single question: which
changes are unrecoverable enough that a ticket touching them stops for a human. Four sections,
`[blast]`, `[judge]`, `[auth]` and `[assertion]`, one glob per line. The shipped list covers
migrations, anything matching `*auth*` or `*token*` or `*secrets*`, `.env*`, Dockerfiles, git
hooks, and the judge files themselves (the checker, the guard, and this config), because a
ticket that can edit its own gate can close green having weakened it.

Add your own. A section you delete entirely keeps the checker's built-in fallback, but a
section you leave present and empty is honoured as empty, because deliberately emptying a
class is a real choice and silently restoring a default would be the hole this whole file
guards against.

A single plan can layer a diff on top without editing the shared file. Point the checker at
`--blast-globs-file plans/<name>/blast-globs.txt` and:

- a bare line **adds** a glob to that section
- a line starting with `-` **drops** one of the defaults it matches
- a first non-comment line of `!` **replaces** that section's list wholesale

A default the per-plan file doesn't mention stays gated. That's the point of the diff format:
forgetting to list something can't quietly ungate it. And none of this is amendable from
inside a ticket, so an executor can't widen the gate it's about to be judged by.

**`config/live-credentials.txt`** is the list of environment names that mean "this shell can
reach live data". The guard checks every one before it runs anything and exits 66 naming the
offender. It ships with cloud keys, database URLs, model-provider keys, Stripe, generic OAuth,
and the voice channel's four names. Add every secret name your repo resolves. This list is an
allowlist by omission, so a name nobody adds defeats the guard silently: the verify goes green
while its writes land on production.

**Your plan's manifest** is the third one, and the one you'll touch every time. Territory
globs, verify commands and receipts are per-ticket policy, and they're what the checker
actually enforces.

## Fish Audio

The voice channel uses [Fish Audio](https://fish.audio/?fpr=jesse28) for text to speech. A few
things made it the pick here:

- The current generation carries a 61% win rate against the previous one.
- The free tier runs the same model as the paid tier, free for a limited time, so you can
  build the whole channel and hear it before you decide anything.
- It's the voice behind products including HeyGen, Retell and Plaud.

The API is a single POST. `scripts/loop_voice.py` runs to about 770 lines and the Fish call is
roughly 25 of them, which is the honest ratio: almost all of that file is the fallback
handling this README describes, not the vendor.

**Disclosure:** the Fish Audio links in this README are affiliate links. If you sign up
through one, I may earn a commission at no extra cost to you. I use Fish Audio in this repo
because I use it, and swapping it out is a matter of replacing one function.

## Exit codes

`scripts/loop_check.py`

| Code | Meaning |
|---|---|
| 0 | everything passed, the ticket can close |
| 1 | something failed: verify, attribution, territory, or the evidence lint |
| 2 | passed, but the diff hit the blast radius, so it gates |

`tools/verify_seam.sh`

| Code | Meaning |
|---|---|
| 64 | bad usage |
| 65 | `--repo` isn't a git repo |
| 66 | a live credential is set in this shell, named in the message |
| 67 | uncommitted files in the claimed territory |
| 68 | `config/live-credentials.txt` is missing, unreadable, or empty |

`scripts/loop_voice.py`

| Code | Meaning |
|---|---|
| 0 | delivered, by voice or by the text fallback |
| 2 | didn't land anywhere |
| 3 | misconfigured |

## Running the tests

```bash
cd ~/agent-loop
python3 -m pytest scripts/tests/test_loop_voice.py -q
```

98 tests, about a tenth of a second, no network and no real config touched. They're the proof
that every fallback path above does what this README says it does, including both of the
optional dependencies going missing.

---

Built by Jesse Rurka. The voice on the other end is
[Fish Audio](https://fish.audio/?fpr=jesse28), which is an affiliate link, as disclosed above.
