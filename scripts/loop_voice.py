#!/usr/bin/env python3
"""Voice pager for the loop: Fish Audio TTS -> OGG/Opus -> Telegram sendVoice.

Beside `loop_notify_local.py`, not a mode flag on it: the local pager is the
watchdog's dependency-free channel and stays that way. This one has network
dependencies and a richer failure mode, so it lives on its own.

    0  delivered (voice or, on TTS/conversion/send failure, text fallback)
    2  the page did not land anywhere
    3  misconfigured (missing/unreadable config, or a missing required field)

Credentials are never read from the environment. They resolve from two
pointer files at call time, both overridable by flag so tests never touch a
real config:

    ~/.config/fish-audio/speak.json      apiKey, voiceId, model (camelCase)
    ~/.config/loop-voice/telegram.json   bot_token, chat_id

speak.json's own `format` field is `mp3` (it's shared with other Fish
callers); this script always requests `opus` from Fish regardless of what
that file says. Its contents are never printed or logged.

    python3 scripts/loop_voice.py --message "T-004 quiet for 45m" \
        --run-context 2026-08-19-thing/run-3/jesses-mac
    python3 scripts/loop_voice.py --prove --run-context loop-voice/live-proof
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_FISH_CONFIG = Path.home() / ".config" / "fish-audio" / "speak.json"
DEFAULT_TELEGRAM_CONFIG = Path.home() / ".config" / "loop-voice" / "telegram.json"

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
TELEGRAM_API = "https://api.telegram.org"

HTTP_TIMEOUT = 30.0
FFMPEG_TIMEOUT = 30.0

EXIT_OK = 0
EXIT_DELIVERY = 2
EXIT_CONFIG = 3

FISH_REQUIRED_FIELDS = ("apiKey", "voiceId", "model")
TELEGRAM_REQUIRED_FIELDS = ("bot_token", "chat_id")

CLAUDE_BINARY = "claude"
DEFAULT_REWRITE_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_REWRITE_STYLE = (
    "Rewrite this into a natural, spoken paragraph for a text-to-speech voice "
    "note. No bullet points, no markdown, no bare file paths or ids read aloud "
    "verbatim. Keep it brief and conversational."
)
# Composed onto whatever tone directive is configured (default or the user's
# `summaryStyle`), never replacing it -- a helpful model narrates its own
# editorial reasoning unless told not to, and nothing downstream can tell
# that narration apart from the rewrite it's supposed to return.
REWRITE_OUTPUT_CONSTRAINT = (
    "Return ONLY the rewritten line, nothing else: no preamble, no "
    "surrounding quotation marks, no explanation of what you changed or why, "
    "no commentary before or after it."
)
# Layered on top of REWRITE_OUTPUT_CONSTRAINT and the tone directive, never
# folded into either -- REWRITE_OUTPUT_CONSTRAINT governs the SHAPE of the
# response (nothing but the rewritten line), this governs its REGISTER (what
# kind of spoken line it should be). Composed after both in the prompt.
BRIEFING_REGISTER = (
    "Register: this is a spoken status update, the kind you'd say out loud "
    "to your engineering manager or director, not a written report. Brief "
    "them the way you'd actually talk: plain and conversational, no jargon "
    "or over-technical phrasing. Don't read ticket ids aloud when a plain "
    "phrase works instead -- say \"the reminder feature\" rather than "
    "\"T-004\". State counts the way you'd say them out loud in "
    "conversation, not as a recited list of numbers."
)
# Measured 2026-08-22: a real subprocess call with the actual briefing
# prompt (T-014's Trace) took 18.7s against the prior 20.0s limit --
# succeeding by 1.3s, with model-call latency (not the Fish markers, which
# Fish's own docs confirm cost no tokens and add no latency) varying run to
# run. 40.0 gives roughly 2x headroom over that observed call so ordinary
# latency variance doesn't cross the line, while still bounding how long a
# genuinely hung call can block a page.
#
# T-018: that 40.0s constant was tuned to a single ~45-word prompt and
# turned out marginal for a longer one -- a real ~65-word run-end message
# exceeded it (`briefing rewrite timed out after 40.0s`) while the same
# content trimmed to ~45 words succeeded on the very next attempt. Two real
# measurements: 18.7s at ~45 words, over 40s at ~65 words -- latency scales
# with input length, by more than 2x between those two points. Rather than
# guess a new fixed constant from a third data point, this becomes the
# DEFAULT for an optional `rewriteTimeout` key in the fish-audio config
# (route (b) from T-018's acceptance criteria): Jesse is the one filing
# run-end messages and can tune the bound to his own observed latency
# without another code change, while every existing/unset config keeps
# today's exact behavior. See `_resolve_rewrite_timeout()`.
REWRITE_TIMEOUT = 40.0

# T-018: upper bound on a configured `rewriteTimeout` -- "genuinely hung
# call" must still be bounded per the acceptance criteria, so an absurd
# configured value (e.g. a pasted timestamp, an extra zero) falls back to
# the default instead of letting a page block near-indefinitely. 300.0 is
# generous (7.5x the default) for a legitimately slow model day while still
# being a real bound.
MAX_REWRITE_TIMEOUT = 300.0

# Small fixed palette keyed to event type, per Fish's bracketed inline
# direction. Verified 2026-08-22 against docs.fish.audio,
# developer-guide/core-features/emotions.mdx ("Placement Rules", the doc's
# own "Correct:" examples). Two things this corrects from the first cut of
# this palette (T-007): S2's bracket cues are natural-language descriptions,
# NOT limited to a fixed emotion-word list -- the doc's own example is
# `[warm and happy]` -- so T-007's `[neutral, warm]` / `[flat, urgent]` were
# never invalid syntax, and sentence-level cues only "usually work best" at
# a sentence start for S2, that is not a hard placement rule (S1's legacy
# best-practices page is where "must open the sentence" and "one emotion
# per sentence" actually live). What's below is a phrasing change, not a
# validity fix: two-word natural-language descriptions in the doc's own
# "warm and happy" shape, one bracket per event type, still never
# free-form and never LLM-chosen -- the caller states the event, this dict
# picks the tag, deterministically. Deliberately no "sarcastic"-class
# phrase -- measured from real use, the TTS overacts that register, so
# flat/measured delivery is what stays.
EVENT_TAG_PALETTE: dict[str, str] = {
    "completion": "[calm and confident]",
    "blocker": "[urgent and determined]",
    "gate": "[urgent and determined]",
}
DEFAULT_EVENT_TYPE = "completion"

# Mid-text cue allowlist. Fish's own docs make bracket cues free-form for
# S2 -- any natural-language description is valid syntax, and sound
# effects/tone controls/emotion cues can all appear anywhere in the text.
# That permissiveness is exactly why code has to constrain what a rewrite
# is allowed to emit here: this is a PRODUCT decision (Jesse's register
# preference), not a syntax fix, the same way `[sarcastic]` being real,
# valid Fish syntax doesn't stop us from stripping it below. Nothing
# outside this allowlist reaches Fish, no matter how well-formed it is.
#
# POSITIONAL_CUES are the doc's own Tone Markers/pauses that "can go
# anywhere in the text" (developer-guide/core-features/emotions.mdx,
# Placement Rules) -- not subject to the sentence-start rule below.
POSITIONAL_CUES: frozenset[str] = frozenset({"break", "long-break", "emphasis"})

# MID_TEXT_TONE_TAGS is deliberately the SAME small set as
# EVENT_TAG_PALETTE's values (stripped of brackets) -- one approved
# vocabulary for tone/register, whether it's leading or mid-text, so there
# is a single place to extend it. These are restricted to sentence starts
# below not because Fish's docs require it (they don't, for S2 -- tone
# controls "can go anywhere"; only "usually works best" applies to
# sentence-level emotion cues) but because our own register wants a tone
# shift anchored to a sentence boundary, so a listener can place it. That
# is a restraint we are choosing, same footing as the allowlist itself.
MID_TEXT_TONE_TAGS: frozenset[str] = frozenset(
    tag.strip("[]") for tag in EVENT_TAG_PALETTE.values()
)

# "3-4 cues total per note" per Fish's own best-practices guidance not to
# overuse tags in short text; 4 is the permissive end of that range.
MAX_CUES_PER_NOTE = 4

_SENTENCE_DELIMITER_RE = re.compile(r"[.!?]+\s+")
_BRACKET_CUE_RE = re.compile(r"\[([^\[\]]+)\]")
_COLLAPSE_WHITESPACE_RE = re.compile(r"[ \t]{2,}")
# T-015: matches a single leading bracket cue (plus its trailing whitespace)
# on text that has already been through `enforce_cue_allowlist()` -- used
# only by `apply_direction_tag()` to detect a rewrite that opened with the
# same kind of tone cue the event tag is about to add.
_LEADING_CUE_RE = re.compile(r"^\[([^\[\]]+)\]\s*")

# Composed into the rewrite prompt so a model knows cues exist and what the
# allowlist actually permits -- proposing a cue outside this set is not an
# error, `enforce_cue_allowlist` below silently strips it, per the product
# decision recorded there.
# T-016: the previous wording ("sparingly" ... "most short notes need none
# at all") gave a compliant model explicit permission to emit zero cues --
# and a run-end note IS short, so a compliant model reliably took that
# permission. Verified live 2026-08-22: the note landed with exactly one
# bracket, the leading event tag, and zero mid-text cues, even though the
# allowlist/caps/placement rules all worked correctly -- they just never
# engaged. This version asks for cues in the normal case (a floor, not a
# permitted zero) and says concretely what a cue is FOR, so the model has a
# reason to reach for one instead of only a syntax list to ignore. The
# ceiling stays exactly `MAX_CUES_PER_NOTE` -- this only raises the floor.
CUE_GUIDANCE = (
    "Add Fish Audio inline cues to this rewrite -- a note this length "
    f"should normally carry two to {MAX_CUES_PER_NOTE} of them, not zero. "
    "Only these survive -- anything else is stripped before this reaches "
    "the listener: " + ", ".join(f"[{tag}]" for tag in sorted(POSITIONAL_CUES | MID_TEXT_TONE_TAGS)) +
    ". Use them with purpose: a [break] before a run of counts gives the "
    "listener a beat to take the numbers in, and [emphasis] on the one "
    "number that matters most makes sure it lands. A tone cue should open "
    "the sentence it applies to, at most one tone cue per sentence; "
    "[break], [long-break] and [emphasis] can go anywhere. Stay at or "
    f"under {MAX_CUES_PER_NOTE} cues in the whole note."
)


def fail(message: str, code: int) -> int:
    print(f"loop_voice: {message}", file=sys.stderr)
    return code


def _load_config(path: Path, required_fields: tuple[str, ...]) -> dict[str, Any] | None:
    """Read and validate a config file. Returns None on any problem; never logs contents."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for field in required_fields:
        value = data.get(field)
        if not isinstance(value, str) or not value:
            return None
    return data


def load_fish_config(path: Path) -> dict[str, Any] | None:
    return _load_config(path, FISH_REQUIRED_FIELDS)


def _resolve_rewrite_timeout(fish_cfg: dict[str, Any]) -> float:
    """T-018: resolve the rewrite subprocess timeout from the optional
    `rewriteTimeout` key in the fish-audio config, defaulting to
    `REWRITE_TIMEOUT` when unset. A non-numeric, non-finite, non-positive,
    or absurdly large configured value falls back to the default rather
    than propagating -- this must still bound a genuinely hung call, never
    wait unboundedly."""
    value = fish_cfg.get("rewriteTimeout")
    if value is None:
        return REWRITE_TIMEOUT
    # bool is a subclass of int; True/False are not a meaningful timeout.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return REWRITE_TIMEOUT
    if not math.isfinite(value) or value <= 0 or value > MAX_REWRITE_TIMEOUT:
        return REWRITE_TIMEOUT
    return float(value)


def load_telegram_config(path: Path) -> dict[str, Any] | None:
    return _load_config(path, TELEGRAM_REQUIRED_FIELDS)


def _clean_rewrite_response(text: str) -> str:
    """Defensive backstop for a model that ignores REWRITE_OUTPUT_CONSTRAINT
    and returns its rewritten line wrapped in quotes followed by commentary
    -- the exact shape observed live. One rule, applied once: if the
    response opens with a quote character and that same quote character
    (its matching close for smart quotes) reappears later, keep only the
    text between that first pair and drop everything else, including a
    trailing commentary tail. Otherwise the response is returned unchanged.

    This promises only that shape of cleanup. It does NOT detect commentary
    that isn't quote-delimited, commentary that precedes rather than follows
    a quoted line, or a response quoted with an unmatched/exotic quote
    character. Those still reach Fish's TTS verbatim -- the prompt
    constraint above is the real fix; this only softens the worst case.
    """
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    quote_pairs = {'"': '"', "'": "'", "“": "”"}
    closer = quote_pairs.get(cleaned[0])
    if closer is not None:
        end = cleaned.find(closer, 1)
        if end != -1:
            return cleaned[1:end].strip()
    return cleaned


_COMMENTARY_MARKERS = (
    "i rewrote", "i changed", "i've rewritten", "i have rewritten",
    "here is the", "here's the", "as an ai", "note:", "explanation:",
    "the rewrite", "this rewrite",
)

# A briefing register asks counts to be "stated naturally rather than
# recited as a list of numbers" -- for small counts that naturally means
# spelled out ("nine tickets", "nothing's blocked"), not the literal digit.
# The counts check below accepts either form so it doesn't fight the
# register it's supposed to be checking.
_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = (
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
)


def _spell_number(n: int) -> str:
    """Spell an integer 0-99 as a word, hyphenating compounds (e.g.
    "twenty-one") per standard English number-word style. Generated, not
    hand-listed, so there is no arbitrary cliff on the count this covers."""
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] if ones == 0 else f"{_TENS[tens]}-{_ONES[ones]}"


# Zero's exempt-only synonyms are register words, not generated spellings
# ("no", "none", "nothing" all read as zero in a spoken briefing), so they
# stay hand-listed on top of the generated "zero". Every other count 0-99
# gets exactly one generated spelling.
_NUMBER_WORDS: dict[str, tuple[str, ...]] = {
    "0": ("zero", "no", "none", "nothing"),
    **{str(n): (_spell_number(n),) for n in range(1, 100)},
}


def _count_present(count: str, rewritten_digits: set[str], rewritten_lower: str) -> bool:
    if count in rewritten_digits:
        return True
    words = _NUMBER_WORDS.get(count)
    return bool(words) and any(
        re.search(rf"\b{re.escape(word)}\b", rewritten_lower) for word in words
    )


def _meets_briefing_bar(original: str, rewritten: str) -> bool:
    """Live-checkable gate on the REWRITTEN STRING, before it is sent:
    counts from the original survive (as a digit or, for small counts,
    the natural spoken word), at least a couple of significant words of
    what-was-built coverage survive, and no editorial commentary leaked
    through despite REWRITE_OUTPUT_CONSTRAINT and
    _clean_rewrite_response(). This is a cheap proxy, not a semantic
    check -- it cannot tell a good spoken register from a flat one, only
    that the response still looks like content and not commentary. Used
    only to decide fallback; never raises."""
    if not rewritten:
        return False

    # Ticket ids (e.g. "T-004") carry digits that are not "counts" -- the
    # register explicitly asks the model NOT to read those aloud, so
    # requiring them to survive verbatim would fight the register instead
    # of gating it. Strip them before pulling the counts this checks for.
    original_without_ids = re.sub(r"\b[A-Za-z]+-\d+\b", "", original)
    original_counts = set(re.findall(r"\d+", original_without_ids))
    rewritten_counts = set(re.findall(r"\d+", rewritten))
    rewritten_lower = rewritten.lower()

    # Both checks below only make sense against a message that actually
    # carries counts -- i.e. a run-end-shaped message. A message with no
    # counts at all (an arbitrary page or test fixture) has nothing for
    # "counts present" or "what-was-built coverage" to be checked against,
    # so both are skipped rather than judged against unrelated wording.
    if original_counts:
        # A zero count is naturally dropped in spoken register ("ten done,
        # all clean" rather than "ten done, zero parked") -- only a
        # NON-zero count going missing is the misleading case this bar
        # exists to catch, so zeros are exempt from the survive check.
        non_zero_counts = {count for count in original_counts if count != "0"}
        if not all(
            _count_present(count, rewritten_counts, rewritten_lower)
            for count in non_zero_counts
        ):
            return False

        significant_original = {w.lower() for w in re.findall(r"[A-Za-z']{5,}", original)}
        significant_rewritten = {w.lower() for w in re.findall(r"[A-Za-z']{5,}", rewritten)}
        if significant_original and len(significant_original & significant_rewritten) < 2:
            return False

    if any(marker in rewritten_lower for marker in _COMMENTARY_MARKERS):
        return False

    return True


def _briefing_bar_failure_reason(original: str, rewritten: str) -> str:
    """Re-derive WHY `_meets_briefing_bar` rejected `rewritten`, for a
    stderr diagnostic only -- never called unless the bar already returned
    False, and never itself used to decide fallback. Mirrors the checks in
    `_meets_briefing_bar` exactly so the reason it reports is the reason
    that actually fired."""
    if not rewritten:
        return "empty rewrite"

    original_without_ids = re.sub(r"\b[A-Za-z]+-\d+\b", "", original)
    original_counts = set(re.findall(r"\d+", original_without_ids))
    rewritten_counts = set(re.findall(r"\d+", rewritten))
    rewritten_lower = rewritten.lower()

    if original_counts:
        non_zero_counts = {count for count in original_counts if count != "0"}
        missing = sorted(
            (count for count in non_zero_counts
             if not _count_present(count, rewritten_counts, rewritten_lower)),
            key=int,
        )
        if missing:
            return f"missing count(s) {', '.join(missing)}"

        significant_original = {w.lower() for w in re.findall(r"[A-Za-z']{5,}", original)}
        significant_rewritten = {w.lower() for w in re.findall(r"[A-Za-z']{5,}", rewritten)}
        if significant_original and len(significant_original & significant_rewritten) < 2:
            return "insufficient content-word overlap with the original"

    for marker in _COMMENTARY_MARKERS:
        if marker in rewritten_lower:
            return f"commentary marker detected ({marker!r})"

    return "did not meet the briefing bar"


def rewrite_for_speech(message: str, fish_cfg: dict[str, Any]) -> str:
    """Rewrite `message` into a spoken paragraph via headless `claude -p` on Haiku,
    matching the Part 1 fish-speak.mjs pattern. Falls back to the verbatim message
    on any failure -- binary missing, non-zero exit, timeout, or empty output --
    and never raises."""
    if fish_cfg.get("summarize") is False:
        return message

    claude_path = shutil.which(CLAUDE_BINARY)
    if not claude_path:
        print("loop_voice: briefing rewrite skipped: claude binary not found", file=sys.stderr)
        return message

    model = fish_cfg.get("summaryModel") or DEFAULT_REWRITE_MODEL
    style = fish_cfg.get("summaryStyle") or DEFAULT_REWRITE_STYLE
    prompt = (
        f"{style}\n\n{REWRITE_OUTPUT_CONSTRAINT}\n\n{BRIEFING_REGISTER}"
        f"\n\n{CUE_GUIDANCE}\n\nText to rewrite:\n{message}"
    )

    timeout = _resolve_rewrite_timeout(fish_cfg)
    try:
        result = subprocess.run(
            [claude_path, "-p", prompt, "--model", model],
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"loop_voice: briefing rewrite timed out after {timeout}s",
            file=sys.stderr,
        )
        return message
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"loop_voice: briefing rewrite failed to run: {exc}", file=sys.stderr)
        return message

    if result.returncode != 0:
        print(f"loop_voice: briefing rewrite exited {result.returncode}", file=sys.stderr)
        return message

    rewritten = result.stdout.strip()
    if not rewritten:
        print("loop_voice: briefing rewrite returned empty output", file=sys.stderr)
        return message

    cleaned = _clean_rewrite_response(rewritten)
    if not cleaned:
        print(
            "loop_voice: briefing rewrite returned empty output after cleaning",
            file=sys.stderr,
        )
        return message

    if not _meets_briefing_bar(message, cleaned):
        reason = _briefing_bar_failure_reason(message, cleaned)
        print(f"loop_voice: briefing rewrite rejected: {reason}", file=sys.stderr)
        return message

    return cleaned


def apply_direction_tag(text: str, event_type: str) -> str:
    """Prepend the fixed direction tag for `event_type`, per Fish's bracketed
    plain-English inline-direction format. Deterministic: the same event type
    always yields the same tag. Falls back to the completion tag for an
    unrecognized value -- defensive only, since argparse `choices` already
    restricts the CLI to `EVENT_TAG_PALETTE` keys."""
    tag = EVENT_TAG_PALETTE.get(event_type, EVENT_TAG_PALETTE[DEFAULT_EVENT_TYPE])

    # T-015: `text` has already been through `enforce_cue_allowlist()`, so
    # any leading bracket cue here is by definition allowlisted and
    # correctly placed. `CUE_GUIDANCE` invites the rewrite to open a
    # sentence with a tone cue, and the completion palette entry IS such a
    # phrase, so the model proposing the natural one for a completion note
    # is the expected case, not a rare one -- left alone, it would double up
    # with the tag below. The event-type tag stays authoritative (T-007's
    # decision, preserved through every ticket since): it is the
    # deterministic, caller-controlled register signal, while the model's
    # leading cue is only a proposal, so the model's leading TONE cue is
    # dropped in favor of the event tag rather than the reverse. A leading
    # POSITIONAL cue (`[break]`, `[long-break]`, `[emphasis]`) is not a
    # register duplicate of the event tag, so it is left in place and the
    # event tag is still prepended in front of it -- deliberate stacking,
    # which Fish documents as correct (`[sad][whispering]`), not an
    # accidental duplicate.
    match = _LEADING_CUE_RE.match(text)
    if match and match.group(1).strip().lower() in MID_TEXT_TONE_TAGS:
        text = text[match.end():]

    return f"{tag} {text}"


def enforce_cue_allowlist(text: str) -> str:
    """Strip every bracketed cue the rewrite proposed that isn't on the
    allowlist (`POSITIONAL_CUES` / `MID_TEXT_TONE_TAGS`), and enforce our own
    budget on top of the ones that are: at most `MAX_CUES_PER_NOTE` cues
    total, at most one tone/emotion cue per sentence, and a tone/emotion cue
    kept only when it opens the sentence it's in (a misplaced one is
    stripped, never moved). `[break]`/`[long-break]`/`[emphasis]` are
    positional per Fish's own docs and exempt from the sentence-start rule.

    Called on the REWRITTEN text only, before `apply_direction_tag` prepends
    the leading event-type tag -- this function never sees that tag, so it
    can neither strip nor alter it. Runs only on the rewrite path; `--raw`
    never reaches this function at all.
    """
    if not text:
        return text

    sentence_starts = {0}
    for match in _SENTENCE_DELIMITER_RE.finditer(text):
        sentence_starts.add(match.end())
    sorted_starts = sorted(sentence_starts)

    def sentence_start_for(pos: int) -> int:
        idx = bisect.bisect_right(sorted_starts, pos) - 1
        return sorted_starts[idx]

    pieces: list[str] = []
    last_end = 0
    total_cues = 0
    tone_used_in_sentence: set[int] = set()

    for match in _BRACKET_CUE_RE.finditer(text):
        pieces.append(text[last_end:match.start()])
        last_end = match.end()
        cue_name = match.group(1).strip().lower()

        keep = False
        if total_cues < MAX_CUES_PER_NOTE:
            if cue_name in POSITIONAL_CUES:
                keep = True
            elif cue_name in MID_TEXT_TONE_TAGS:
                sentence_start = sentence_start_for(match.start())
                if match.start() == sentence_start and sentence_start not in tone_used_in_sentence:
                    keep = True

        if keep:
            # T-017: emit the normalized `cue_name` (already `.strip().lower()`'d
            # above for the allowlist check), not `match.group(0)`. Fish's docs
            # write every marker lowercase; recognition is case-insensitive but
            # emission must match the documented form or Fish either drops the
            # unrecognized casing or speaks it literally.
            pieces.append(f"[{cue_name}]")
            total_cues += 1
            if cue_name in MID_TEXT_TONE_TAGS:
                tone_used_in_sentence.add(sentence_start_for(match.start()))
    pieces.append(text[last_end:])

    stripped = "".join(pieces)
    stripped = _COLLAPSE_WHITESPACE_RE.sub(" ", stripped)
    return stripped.strip()


def fish_tts(text: str, fish_cfg: dict[str, Any]) -> bytes | None:
    """POST to Fish Audio's TTS endpoint, always requesting opus. None on any failure."""
    body: dict[str, Any] = {
        "text": text,
        "reference_id": fish_cfg["voiceId"],
        "format": "opus",
    }
    if "speed" in fish_cfg:
        body["speed"] = fish_cfg["speed"]
    request = urllib.request.Request(
        FISH_TTS_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {fish_cfg['apiKey']}",
            "Content-Type": "application/json",
            "model": fish_cfg["model"],
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            if response.status != 200:
                return None
            return response.read()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def is_ogg(data: bytes) -> bool:
    return data[:4] == b"OggS"


def ensure_ogg_opus(data: bytes) -> bytes | None:
    """Return OGG/Opus bytes, rewrapping via ffmpeg only when needed. None on failure."""
    if is_ogg(data):
        return data

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None

    with tempfile.TemporaryDirectory() as tmp_dir:
        in_path = Path(tmp_dir) / "in.audio"
        out_path = Path(tmp_dir) / "out.ogg"
        try:
            in_path.write_bytes(data)
            subprocess.run(
                [ffmpeg, "-y", "-i", str(in_path), "-c:a", "libopus", str(out_path)],
                timeout=FFMPEG_TIMEOUT,
                capture_output=True,
                check=True,
            )
            if not out_path.exists():
                return None
            converted = out_path.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None
        return converted or None


def _multipart_body(fields: dict[str, str], file_field: str, filename: str,
                     content_type: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def _telegram_ok(response_bytes: bytes) -> bool:
    try:
        payload = json.loads(response_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return bool(isinstance(payload, dict) and payload.get("ok") is True)


def send_voice(ogg_bytes: bytes, caption: str, tg_cfg: dict[str, Any]) -> bool:
    body, boundary = _multipart_body(
        {"chat_id": tg_cfg["chat_id"], "caption": caption[:1024]},
        "voice", "voice.ogg", "audio/ogg", ogg_bytes,
    )
    url = f"{TELEGRAM_API}/bot{tg_cfg['bot_token']}/sendVoice"
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            if response.status != 200:
                return False
            return _telegram_ok(response.read())
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def send_text(text: str, tg_cfg: dict[str, Any]) -> bool:
    url = f"{TELEGRAM_API}/bot{tg_cfg['bot_token']}/sendMessage"
    request = urllib.request.Request(
        url,
        data=json.dumps({"chat_id": tg_cfg["chat_id"], "text": text}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            if response.status != 200:
                return False
            return _telegram_ok(response.read())
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def deliver(message: str, run_context: str, fish_cfg: dict[str, Any],
            tg_cfg: dict[str, Any]) -> int:
    caption = run_context or "-"

    audio = fish_tts(message, fish_cfg)
    if audio is not None:
        ogg = ensure_ogg_opus(audio)
        if ogg is not None and send_voice(ogg, caption, tg_cfg):
            print(f"delivered: voice :: {caption} :: {' '.join(message.split())}")
            return EXIT_OK

    if send_text(message, tg_cfg):
        print(f"delivered: text :: {caption} :: {' '.join(message.split())}")
        return EXIT_OK

    return fail("could not deliver by voice or text", EXIT_DELIVERY)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="loop_voice", add_help=True)
    parser.add_argument("--message")
    parser.add_argument("--run-context", default="", dest="run_context")
    parser.add_argument("--prove", action="store_true",
                        help="send a fixed proof sentence mentioning the run context")
    parser.add_argument("--raw", action="store_true",
                        help="skip the spoken-summary rewrite and send the message verbatim")
    parser.add_argument("--event-type", dest="event_type",
                        choices=list(EVENT_TAG_PALETTE), default=DEFAULT_EVENT_TYPE,
                        help="event type driving the direction tag (default: completion)")
    parser.add_argument("--fish-config", dest="fish_config")
    parser.add_argument("--telegram-config", dest="telegram_config")
    args = parser.parse_args(argv)

    fish_path = Path(args.fish_config) if args.fish_config else DEFAULT_FISH_CONFIG
    telegram_path = Path(args.telegram_config) if args.telegram_config else DEFAULT_TELEGRAM_CONFIG

    fish_cfg = load_fish_config(fish_path)
    if fish_cfg is None:
        return fail(f"unreadable or invalid Fish config at {fish_path}", EXIT_CONFIG)

    tg_cfg = load_telegram_config(telegram_path)
    if tg_cfg is None:
        return fail(f"unreadable or invalid Telegram config at {telegram_path}", EXIT_CONFIG)

    if args.prove:
        context = args.run_context or "prove"
        message = f"Loop voice channel proof for {context}."
        return deliver(message, args.run_context, fish_cfg, tg_cfg)

    if not args.message or not args.message.strip():
        return fail("nothing to send: pass --message TEXT (or --prove)", EXIT_CONFIG)

    message = (
        args.message if args.raw
        else apply_direction_tag(
            enforce_cue_allowlist(rewrite_for_speech(args.message, fish_cfg)),
            args.event_type,
        )
    )
    return deliver(message, args.run_context, fish_cfg, tg_cfg)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
