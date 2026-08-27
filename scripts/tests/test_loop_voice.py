"""Tests for the voice pager.

All HTTP (urllib) and ffmpeg calls are mocked; nothing here touches a real
network or a real binary. The exit codes and the `delivered: voice` /
`delivered: text` stdout tokens are the whole contract with the driver and a
later ticket's receipt check, so those are what these tests pin down.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import loop_voice as voice  # noqa: E402


def run(*argv):
    return voice.main(list(argv))


def write_fish_config(tmp_path, **overrides):
    data = {
        "apiKey": "fish-secret-key",
        "voiceId": "e13fa398a7f445a685316a3de6089ce7",
        "model": "s2.1-pro-free",
        "speed": 1.1,
        "format": "mp3",
    }
    data.update(overrides)
    path = tmp_path / "speak.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_telegram_config(tmp_path, **overrides):
    data = {"bot_token": "123456:ABCDEF", "chat_id": "987654"}
    data.update(overrides)
    path = tmp_path / "telegram.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def base_args(fish_path, telegram_path, *extra):
    return [
        "--fish-config", str(fish_path),
        "--telegram-config", str(telegram_path),
        *extra,
    ]


@pytest.fixture(autouse=True)
def _no_real_claude_binary(monkeypatch):
    """Default every test to the rewrite-unavailable fallback (no `claude`
    binary found) so the suite never shells out to the real `claude` binary.
    This mirrors the pattern `test_rewrite_falls_back_to_verbatim_when_claude_
    binary_missing` already uses for a single test, applied suite-wide as the
    default. Tests that specifically exercise the rewrite's `claude -p` call
    override `shutil.which` themselves (after this fixture runs), the same
    way they already do today.
    """
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)


# ---- config resolution ----------------------------------------------------

def test_missing_fish_config_file_is_misconfigured(tmp_path):
    telegram_path = write_telegram_config(tmp_path)
    missing = tmp_path / "nope.json"
    assert run(*base_args(missing, telegram_path, "--message", "hi")) == 3


def test_missing_telegram_config_file_is_misconfigured(tmp_path):
    fish_path = write_fish_config(tmp_path)
    missing = tmp_path / "nope.json"
    assert run(*base_args(fish_path, missing, "--message", "hi")) == 3


def test_fish_config_missing_required_field_is_misconfigured(tmp_path):
    fish_path = write_fish_config(tmp_path, apiKey="")
    telegram_path = write_telegram_config(tmp_path)
    assert run(*base_args(fish_path, telegram_path, "--message", "hi")) == 3


def test_telegram_config_missing_required_field_is_misconfigured(tmp_path):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path, chat_id="")
    assert run(*base_args(fish_path, telegram_path, "--message", "hi")) == 3


def test_flag_override_of_both_config_paths_is_used(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)
    assert run(*base_args(fish_path, telegram_path, "--message", "hi")) == 0


def test_nothing_to_send_is_misconfigured(tmp_path):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    assert run(*base_args(fish_path, telegram_path)) == 3
    assert run(*base_args(fish_path, telegram_path, "--message", "   ")) == 3


# ---- container handling ----------------------------------------------------

def test_already_ogg_bytes_pass_through_without_ffmpeg(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("ffmpeg should not be invoked for already-OGG data")
    monkeypatch.setattr(voice.shutil, "which", explode)
    data = b"OggS" + b"\x00" * 40
    assert voice.ensure_ogg_opus(data) == data


def test_non_ogg_bytes_are_rewrapped_via_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, timeout=None, capture_output=None, check=None):
        out_path = Path(cmd[-1])
        out_path.write_bytes(b"OggS" + b"converted")
        return None

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    result = voice.ensure_ogg_opus(b"not-ogg-raw-opus-bytes")
    assert result == b"OggS" + b"converted"


def test_missing_ffmpeg_and_non_ogg_payload_is_conversion_failure(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)
    assert voice.ensure_ogg_opus(b"not-ogg-bytes") is None


# ---- delivery ladder --------------------------------------------------------

def test_voice_success_exits_zero_and_prints_delivered_voice(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)
    monkeypatch.setattr(voice, "send_text", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("send_text should not run when voice succeeds")))
    code = run(*base_args(fish_path, telegram_path, "--message", "hello there"))
    assert code == 0
    assert "delivered: voice" in capsys.readouterr().out


def test_tts_failure_falls_back_to_text(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: None)
    monkeypatch.setattr(voice, "send_text", lambda *a, **k: True)
    code = run(*base_args(fish_path, telegram_path, "--message", "hello there"))
    assert code == 0
    assert "delivered: text" in capsys.readouterr().out


def test_conversion_failure_falls_back_to_text(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"not-ogg-bytes")
    monkeypatch.setattr(voice, "ensure_ogg_opus", lambda *a, **k: None)
    monkeypatch.setattr(voice, "send_text", lambda *a, **k: True)
    code = run(*base_args(fish_path, telegram_path, "--message", "hello there"))
    assert code == 0
    assert "delivered: text" in capsys.readouterr().out


def test_voice_send_failure_falls_back_to_text(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: False)
    monkeypatch.setattr(voice, "send_text", lambda *a, **k: True)
    code = run(*base_args(fish_path, telegram_path, "--message", "hello there"))
    assert code == 0
    assert "delivered: text" in capsys.readouterr().out


def test_telegram_failure_on_both_voice_and_text_exits_two(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: False)
    monkeypatch.setattr(voice, "send_text", lambda *a, **k: False)
    code = run(*base_args(fish_path, telegram_path, "--message", "hello there"))
    assert code == 2


def test_telegram_failure_when_tts_already_failed_exits_two(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: None)
    monkeypatch.setattr(voice, "send_text", lambda *a, **k: False)
    code = run(*base_args(fish_path, telegram_path, "--message", "hello there"))
    assert code == 2


# ---- --prove -----------------------------------------------------------------

def test_prove_sends_a_fixed_sentence_mentioning_run_context(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)
    code = run(*base_args(fish_path, telegram_path, "--prove",
                          "--run-context", "loop-voice/live-proof"))
    assert code == 0
    assert "loop-voice/live-proof" in captured["text"]


def test_prove_without_run_context_still_exits_zero(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)
    assert run(*base_args(fish_path, telegram_path, "--prove")) == 0


# ---- spoken-summary rewrite --------------------------------------------------

class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_rewrite_runs_claude_haiku_and_uses_rewritten_text(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    def fake_run(cmd, timeout=None, capture_output=None, text=None, check=None):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return _FakeCompletedProcess(returncode=0, stdout="Spoken paragraph.\n")

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "* T-004 done\n* path /a/b"))
    assert code == 0
    assert captured["text"] == "[calm and confident] Spoken paragraph."
    assert captured["cmd"][0] == "/usr/bin/claude"
    assert "-p" in captured["cmd"]
    assert "claude-haiku-4-5-20251001" in captured["cmd"]


def test_raw_flag_bypasses_rewrite_entirely(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def explode(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked with --raw")

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice.subprocess, "run", explode)
    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "raw text here", "--raw"))
    assert code == 0
    assert captured["text"] == "raw text here"


def test_prove_takes_raw_path_and_never_invokes_rewrite(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)

    def explode(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked for --prove")

    monkeypatch.setattr(voice.subprocess, "run", explode)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--prove", "--run-context", "loop-voice/x"))
    assert code == 0


def test_summarize_false_in_config_skips_rewrite(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path, summarize=False)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def explode(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked when summarize is false")

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice.subprocess, "run", explode)
    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "verbatim text"))
    assert code == 0
    assert captured["text"] == "[calm and confident] verbatim text"


def test_rewrite_falls_back_to_verbatim_when_claude_binary_missing(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: None)
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "verbatim text"))
    assert code == 0
    assert captured["text"] == "[calm and confident] verbatim text"


def test_rewrite_falls_back_to_verbatim_on_nonzero_exit(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=1, stdout="ignored"),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "verbatim text"))
    assert code == 0
    assert captured["text"] == "[calm and confident] verbatim text"


def test_rewrite_falls_back_to_verbatim_on_timeout(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_run(*_a, **_k):
        raise voice.subprocess.TimeoutExpired(cmd="claude", timeout=voice.REWRITE_TIMEOUT)

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "verbatim text"))
    assert code == 0
    assert captured["text"] == "[calm and confident] verbatim text"


def test_rewrite_falls_back_to_verbatim_on_empty_output(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout="   \n"),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "verbatim text"))
    assert code == 0
    assert captured["text"] == "[calm and confident] verbatim text"


# ---- T-014: every fallback names its cause on stderr -----------------------

def test_rewrite_for_speech_prints_stderr_diagnostic_on_missing_binary(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)

    fish_cfg = voice.load_fish_config(fish_path)
    result = voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert result == "verbatim text"
    err = capsys.readouterr().err
    assert "loop_voice:" in err
    assert "briefing rewrite skipped: claude binary not found" in err


def test_rewrite_for_speech_prints_stderr_diagnostic_on_nonzero_exit(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=1, stdout="ignored"),
    )

    fish_cfg = voice.load_fish_config(fish_path)
    result = voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert result == "verbatim text"
    err = capsys.readouterr().err
    assert "loop_voice:" in err
    assert "briefing rewrite exited 1" in err


def test_rewrite_for_speech_prints_stderr_diagnostic_on_timeout(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)

    def fake_run(*_a, **_k):
        raise voice.subprocess.TimeoutExpired(cmd="claude", timeout=voice.REWRITE_TIMEOUT)

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)

    fish_cfg = voice.load_fish_config(fish_path)
    result = voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert result == "verbatim text"
    err = capsys.readouterr().err
    assert "loop_voice:" in err
    assert f"briefing rewrite timed out after {voice.REWRITE_TIMEOUT}s" in err


def test_rewrite_for_speech_prints_stderr_diagnostic_on_empty_output(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout="   \n"),
    )

    fish_cfg = voice.load_fish_config(fish_path)
    result = voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert result == "verbatim text"
    err = capsys.readouterr().err
    assert "loop_voice:" in err
    assert "briefing rewrite returned empty output" in err


def test_rewrite_for_speech_prints_stderr_diagnostic_on_empty_after_cleaning(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout='"   "'),
    )

    fish_cfg = voice.load_fish_config(fish_path)
    result = voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert result == "verbatim text"
    err = capsys.readouterr().err
    assert "loop_voice:" in err
    assert "briefing rewrite returned empty output after cleaning" in err


def test_rewrite_for_speech_summarize_false_prints_nothing_to_stderr(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path, summarize=False)

    def explode(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked when summarize is false")

    monkeypatch.setattr(voice.subprocess, "run", explode)

    fish_cfg = voice.load_fish_config(fish_path)
    result = voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert result == "verbatim text"
    assert capsys.readouterr().err == ""


# ---- T-018: rewrite timeout resolution --------------------------------------

def test_resolve_rewrite_timeout_defaults_when_unset(tmp_path):
    fish_path = write_fish_config(tmp_path)
    fish_cfg = voice.load_fish_config(fish_path)

    assert voice._resolve_rewrite_timeout(fish_cfg) == voice.REWRITE_TIMEOUT


def test_resolve_rewrite_timeout_uses_configured_override(tmp_path):
    fish_path = write_fish_config(tmp_path, rewriteTimeout=90)
    fish_cfg = voice.load_fish_config(fish_path)

    assert voice._resolve_rewrite_timeout(fish_cfg) == 90.0


@pytest.mark.parametrize(
    "bad_value",
    [
        0,
        -5,
        "60",
        True,
        False,
        float("nan"),
        float("inf"),
        voice.MAX_REWRITE_TIMEOUT + 1,
    ],
)
def test_resolve_rewrite_timeout_falls_back_on_bad_value(tmp_path, bad_value):
    fish_path = write_fish_config(tmp_path, rewriteTimeout=bad_value)
    fish_cfg = voice.load_fish_config(fish_path)

    assert voice._resolve_rewrite_timeout(fish_cfg) == voice.REWRITE_TIMEOUT


def test_rewrite_for_speech_timeout_message_uses_configured_value(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path, rewriteTimeout=5)

    def fake_run(*_a, **_k):
        raise voice.subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)

    fish_cfg = voice.load_fish_config(fish_path)
    result = voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert result == "verbatim text"
    err = capsys.readouterr().err
    assert "briefing rewrite timed out after 5.0s" in err


def test_rewrite_for_speech_passes_configured_timeout_to_subprocess(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path, rewriteTimeout=7)
    captured_kwargs = {}

    def fake_run(*_a, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeCompletedProcess(returncode=0, stdout="Spoken paragraph.\n")

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)

    fish_cfg = voice.load_fish_config(fish_path)
    voice.rewrite_for_speech("verbatim text", fish_cfg)

    assert captured_kwargs["timeout"] == 7.0


# ---- rewrite response cleaning -----------------------------------------------

def test_clean_single_line_response_passes_through_unchanged(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout="Spoken paragraph.\n"),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "T-004 done"))
    assert code == 0
    assert captured["text"] == "[calm and confident] Spoken paragraph."


def test_quoted_response_is_unquoted(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout='"Spoken paragraph."'),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "T-004 done"))
    assert code == 0
    assert captured["text"] == "[calm and confident] Spoken paragraph."


def test_commentary_tail_after_quoted_line_is_dropped(tmp_path, monkeypatch):
    """Fixture is the real observed defect: the model's first live run-end
    output quoted its rewritten line, then explained its editorial choices
    for two more sentences. This is the best-possible case for the cleaning
    backstop -- the response opens with a quote."""
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}
    observed_output = (
        '"loop-voice-channel run complete: 8 done, 0 parked, 0 gated, 0 '
        'proposed. Voice channel delivered." The rewrite drops the whimsical '
        'phrase about the channel "announcing its own completion" and '
        'replaces it with concrete language ("delivered"). Keeping the '
        'numeric breakdown preserves precision while streamlining from '
        '"announcing" to a simple statement of outcome.'
    )

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout=observed_output),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "run summary"))
    assert code == 0
    assert captured["text"] == (
        "[calm and confident] loop-voice-channel run complete: 8 done, 0 parked, "
        "0 gated, 0 proposed. Voice channel delivered."
    )


def test_all_commentary_response_falls_back_to_verbatim(tmp_path, monkeypatch):
    """Quoted whitespace cleans down to empty, so the whole rewrite is
    treated as a failure and the caller falls back to the verbatim message,
    exactly like every other failure mode."""
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout='"   "'),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "verbatim text"))
    assert code == 0
    assert captured["text"] == "[calm and confident] verbatim text"


# ---- direction tags ----------------------------------------------------------

def test_completion_event_type_gets_calm_confident_tag(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path, summarize=False)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "run complete",
                          "--event-type", "completion"))
    assert code == 0
    assert captured["text"] == "[calm and confident] run complete"


def test_blocker_event_type_gets_urgent_determined_tag(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path, summarize=False)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "run blocked",
                          "--event-type", "blocker"))
    assert code == 0
    assert captured["text"] == "[urgent and determined] run blocked"


def test_gate_event_type_gets_urgent_determined_tag(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path, summarize=False)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "gate check",
                          "--event-type", "gate"))
    assert code == 0
    assert captured["text"] == "[urgent and determined] gate check"


def test_default_event_type_is_completion(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path, summarize=False)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "no flag given"))
    assert code == 0
    assert captured["text"] == "[calm and confident] no flag given"


def test_raw_flag_emits_untagged_text(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def explode(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked with --raw")

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice.subprocess, "run", explode)
    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    code = run(*base_args(fish_path, telegram_path, "--message", "raw text here",
                          "--raw", "--event-type", "blocker"))
    assert code == 0
    assert captured["text"] == "raw text here"


def test_invalid_event_type_is_rejected_by_argparse(tmp_path):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    with pytest.raises(SystemExit):
        run(*base_args(fish_path, telegram_path, "--message", "hi",
                       "--event-type", "sarcastic"))


def test_palette_has_no_sarcastic_class_tag():
    assert "sarcastic" not in voice.EVENT_TAG_PALETTE
    for tag in voice.EVENT_TAG_PALETTE.values():
        assert "sarcas" not in tag.lower()


def test_palette_values_are_single_bracket_natural_language_phrases():
    # T-013: not a fixed-vocabulary requirement (Fish's S2 bracket cues are
    # free-form natural language) -- just one bracket, one phrase, no
    # comma-combined pair, per this ticket's own acceptance criteria.
    for tag in voice.EVENT_TAG_PALETTE.values():
        assert tag.startswith("[") and tag.endswith("]")
        inner = tag[1:-1]
        assert "," not in inner
        assert inner == inner.strip() and inner


# ---- T-013: mid-text cue allowlist -----------------------------------------

def test_enforce_cue_allowlist_strips_sarcastic_tag():
    # [sarcastic] is real, valid Fish syntax -- our allowlist still strips
    # it, since the ban is a product/register decision, not a syntax one.
    text = "[sarcastic] Oh sure, that went great."
    assert voice.enforce_cue_allowlist(text) == "Oh sure, that went great."


def test_enforce_cue_allowlist_strips_invented_cue():
    text = "[glorious triumph] We shipped it."
    assert voice.enforce_cue_allowlist(text) == "We shipped it."


def test_enforce_cue_allowlist_keeps_positional_cue_anywhere():
    text = "We shipped it, [break] and nothing's blocked."
    assert voice.enforce_cue_allowlist(text) == text


def test_enforce_cue_allowlist_keeps_emphasis_mid_sentence():
    text = "This is [emphasis] the important part."
    assert voice.enforce_cue_allowlist(text) == text


def test_enforce_cue_allowlist_keeps_sentence_start_tone_cue():
    text = "[calm and confident] Nine done, nothing blocked."
    assert voice.enforce_cue_allowlist(text) == text


def test_enforce_cue_allowlist_strips_midsentence_tone_cue_but_keeps_break():
    # Pinned per T-013 acceptance: a misplaced tone cue is stripped, never
    # moved; a positional [break] in the same text survives untouched.
    text = "Nine done, [urgent and determined] one gate is stuck. [break] Nothing else pending."
    assert voice.enforce_cue_allowlist(text) == (
        "Nine done, one gate is stuck. [break] Nothing else pending."
    )


def test_enforce_cue_allowlist_allows_only_one_tone_cue_per_sentence():
    # Second sentence-start tone cue lands in a NEW sentence, so it's kept;
    # a second one inside the SAME sentence is not.
    text = "[calm and confident] Nine done. [urgent and determined] One gate is stuck."
    assert voice.enforce_cue_allowlist(text) == text

    text_two_in_one_sentence = (
        "[calm and confident] Nine done, [urgent and determined] but one gate is stuck."
    )
    assert voice.enforce_cue_allowlist(text_two_in_one_sentence) == (
        "[calm and confident] Nine done, but one gate is stuck."
    )


def test_enforce_cue_allowlist_caps_at_max_cues_per_note():
    text = (
        "One. [break] Two. [break] Three. [break] Four. [break] Five. [break]"
    )
    result = voice.enforce_cue_allowlist(text)
    assert result.count("[break]") == voice.MAX_CUES_PER_NOTE


def test_enforce_cue_allowlist_leaves_plain_text_untouched():
    text = "Nine done, nothing blocked, nothing gated."
    assert voice.enforce_cue_allowlist(text) == text


def test_enforce_cue_allowlist_empty_text_returns_empty():
    assert voice.enforce_cue_allowlist("") == ""


# ---- T-017: kept cues normalize to Fish's documented lowercase form -------

def test_enforce_cue_allowlist_normalizes_capitalized_positional_cue():
    # The live-note defect: [Break] must reach Fish as [break], not spoken
    # literally or silently dropped for being unrecognized casing.
    text = "We shipped it, [Break] and nothing's blocked."
    assert voice.enforce_cue_allowlist(text) == "We shipped it, [break] and nothing's blocked."


def test_enforce_cue_allowlist_normalizes_all_caps_positional_cue():
    text = "We shipped it, [BREAK] and nothing's blocked."
    assert voice.enforce_cue_allowlist(text) == "We shipped it, [break] and nothing's blocked."


def test_enforce_cue_allowlist_normalizes_capitalized_long_break():
    text = "Nine done. [Long-Break] Onto the next."
    assert voice.enforce_cue_allowlist(text) == "Nine done. [long-break] Onto the next."


def test_enforce_cue_allowlist_normalizes_capitalized_emphasis():
    text = "This is [Emphasis] the important part."
    assert voice.enforce_cue_allowlist(text) == "This is [emphasis] the important part."


def test_enforce_cue_allowlist_normalizes_capitalized_tone_cue():
    text = "[Calm And Confident] Nine done, nothing blocked."
    assert voice.enforce_cue_allowlist(text) == "[calm and confident] Nine done, nothing blocked."


def test_enforce_cue_allowlist_strips_disallowed_cue_regardless_of_casing():
    # Odd casing on a disallowed cue must still be STRIPPED, never
    # normalized and kept -- recognition is case-insensitive, allowlist
    # membership is not expanded by it.
    text = "[Sarcastic] Oh sure, that went great."
    assert voice.enforce_cue_allowlist(text) == "Oh sure, that went great."


def test_leading_event_tag_survives_cue_stripping(tmp_path, monkeypatch):
    # enforce_cue_allowlist runs on the REWRITTEN text only, before
    # apply_direction_tag prepends the real leading tag -- it must never
    # see, strip, or alter that tag.
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(
            returncode=0,
            stdout="[sarcastic] Nine done, nothing blocked, shipped the voice channel.\n",
        ),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    message = "loop-voice-channel run-end: 9 done, 0 parked, 0 gated, 0 proposed. Shipped the voice channel."
    code = run(*base_args(fish_path, telegram_path, "--message", message))
    assert code == 0
    assert captured["text"] == (
        "[calm and confident] Nine done, nothing blocked, shipped the voice channel."
    )
    assert captured["text"].count("[") == 1


def test_raw_flag_emits_no_cues_even_with_bracket_looking_text(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def explode(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked with --raw")

    def fake_fish_tts(text, fish_cfg):
        captured["text"] = text
        return b"OggS" + b"x" * 20

    monkeypatch.setattr(voice.subprocess, "run", explode)
    monkeypatch.setattr(voice, "fish_tts", fake_fish_tts)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    raw_message = "[sarcastic] raw text with brackets in it"
    code = run(*base_args(fish_path, telegram_path, "--message", raw_message, "--raw"))
    assert code == 0
    assert captured["text"] == raw_message


# ---- T-015: no doubled leading tag ------------------------------------------

def test_apply_direction_tag_dedupes_leading_tone_cue():
    text = "[urgent and determined] One gate is stuck."
    result = voice.apply_direction_tag(text, "completion")
    assert result == "[calm and confident] One gate is stuck."
    assert result.count("[") == 1


def test_apply_direction_tag_keeps_leading_positional_cue_stacked():
    text = "[break] Nine done, nothing blocked."
    result = voice.apply_direction_tag(text, "completion")
    assert result == "[calm and confident] [break] Nine done, nothing blocked."


def test_apply_direction_tag_plain_text_unchanged():
    text = "Nine done, nothing blocked."
    result = voice.apply_direction_tag(text, "completion")
    assert result == "[calm and confident] Nine done, nothing blocked."


def test_leading_tone_cue_from_rewrite_does_not_double_the_event_tag(tmp_path, monkeypatch):
    # Companion to test_leading_event_tag_survives_cue_stripping, but with
    # the mocked rewrite proposing an ALLOWLISTED leading tone cue instead
    # of [sarcastic] -- the expected case per CUE_GUIDANCE, not a rare one.
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(
            returncode=0,
            stdout="[urgent and determined] Nine done, one gate stuck, shipped the voice channel.\n",
        ),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    message = "loop-voice-channel run-end: 9 done, 1 gated. Shipped the voice channel."
    code = run(*base_args(fish_path, telegram_path, "--message", message))
    assert code == 0
    assert captured["text"] == (
        "[calm and confident] Nine done, one gate stuck, shipped the voice channel."
    )
    assert captured["text"].count("[") == 1


# ---- T-016: cue guidance asks for cues, not permission to skip them --------

def test_cue_guidance_has_no_zero_cue_escape():
    assert "none at all" not in voice.CUE_GUIDANCE
    assert "sparingly" not in voice.CUE_GUIDANCE


def test_cue_guidance_states_a_floor_below_the_ceiling():
    assert "two" in voice.CUE_GUIDANCE
    assert str(voice.MAX_CUES_PER_NOTE) in voice.CUE_GUIDANCE


def test_cue_guidance_names_a_concrete_purpose_for_break_and_emphasis():
    lowered = voice.CUE_GUIDANCE.lower()
    assert "[break]" in lowered and "counts" in lowered
    assert "[emphasis]" in lowered and "matters" in lowered


def test_cue_guidance_still_lists_every_allowlisted_cue():
    for tag in voice.POSITIONAL_CUES | voice.MID_TEXT_TONE_TAGS:
        assert f"[{tag}]" in voice.CUE_GUIDANCE


# ---- config content never leaks into stdout/stderr --------------------------

def test_fish_config_secrets_never_printed(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path, apiKey="super-secret-do-not-print")
    telegram_path = write_telegram_config(tmp_path)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)
    run(*base_args(fish_path, telegram_path, "--message", "hello there"))
    captured = capsys.readouterr()
    assert "super-secret-do-not-print" not in captured.out
    assert "super-secret-do-not-print" not in captured.err


# ---- briefing register (prompt) and briefing bar (live-checkable shape) -----

def test_prompt_composes_briefing_register_after_output_constraint(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    def fake_run(cmd, timeout=None, capture_output=None, text=None, check=None):
        captured["prompt"] = cmd[cmd.index("-p") + 1]
        return _FakeCompletedProcess(returncode=0, stdout="9 done, 0 parked. Shipped the thing.\n")

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    monkeypatch.setattr(voice, "fish_tts", lambda *a, **k: b"OggS" + b"x" * 20)
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    run(*base_args(fish_path, telegram_path, "--message", "9 done, 0 parked."))

    prompt = captured["prompt"]
    style_idx = prompt.index(voice.DEFAULT_REWRITE_STYLE)
    constraint_idx = prompt.index(voice.REWRITE_OUTPUT_CONSTRAINT)
    register_idx = prompt.index(voice.BRIEFING_REGISTER)
    text_idx = prompt.index("Text to rewrite:")
    assert style_idx < constraint_idx < register_idx < text_idx


def test_briefing_bar_missing_count_falls_back_to_verbatim(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(
            returncode=0, stdout="We finished building the voice thing.\n",
        ),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    message = "loop-voice-channel run-end: 9 done, 0 parked, 0 gated, 0 proposed."
    code = run(*base_args(fish_path, telegram_path, "--message", message))
    assert code == 0
    assert captured["text"] == f"[calm and confident] {message}"


def test_briefing_bar_no_content_coverage_falls_back_to_verbatim(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout="9 done, 0 parked.\n"),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    message = "9 done, 0 parked. Built a Telegram voice channel for the loop."
    code = run(*base_args(fish_path, telegram_path, "--message", message))
    assert code == 0
    assert captured["text"] == f"[calm and confident] {message}"


def test_briefing_bar_commentary_marker_falls_back_to_verbatim(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(
            returncode=0,
            stdout="Here is the rewrite: nine tickets done, built the voice channel.\n",
        ),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    message = "9 done, 0 parked. Built a voice channel for the loop."
    code = run(*base_args(fish_path, telegram_path, "--message", message))
    assert code == 0
    assert captured["text"] == f"[calm and confident] {message}"


def test_briefing_bar_passes_a_realistic_run_end_rewrite(tmp_path, monkeypatch):
    fish_path = write_fish_config(tmp_path)
    telegram_path = write_telegram_config(tmp_path)
    captured = {}

    rewritten = (
        "The loop voice channel work is done. We finished all 9 tickets, "
        "nothing's blocked and nothing's still proposed. We built a way for "
        "the loop to speak status updates over Telegram using Fish Audio, "
        "with a text fallback if the voice send ever fails."
    )
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout=rewritten + "\n"),
    )
    monkeypatch.setattr(voice, "fish_tts", lambda text, fish_cfg: (captured.__setitem__("text", text), b"OggS" + b"x" * 20)[1])
    monkeypatch.setattr(voice, "send_voice", lambda *a, **k: True)

    message = (
        "loop-voice-channel run-end: 9 done, 0 parked, 0 gated, 0 proposed. "
        "Built a voice channel for the loop: Fish Audio text-to-speech over "
        "Telegram, gate pages and a run-end summary, with a text fallback "
        "when voice fails."
    )
    code = run(*base_args(fish_path, telegram_path, "--message", message))
    assert code == 0
    assert captured["text"] == f"[calm and confident] {rewritten}"


def test_meets_briefing_bar_live_run_end_note_passes_because_zero_is_exempt():
    # The live 2026-08-22 note: original counts were {"10", "0"}. The
    # rewrite never mentions a zero at all -- it must still pass, but now
    # because zero counts are exempt from the survive check, not because
    # "no" happens to hide inside "notes" (the old, coincidental reason).
    original = "loop-voice-channel run-end: 10 done, 0 parked, 0 gated, 0 proposed."
    rewritten = (
        "Ten done. Built the voice channel for the loop-Fish Audio "
        "text-to-speech sending voice notes to Telegram on gates and "
        "run-end, with text fallback if speech fails."
    )
    assert voice._meets_briefing_bar(original, rewritten) is True


def test_meets_briefing_bar_live_note_without_the_word_notes_also_passes():
    # Same message with the one word that made the old substring match
    # coincidentally succeed ("notes" containing "no") removed. Under the
    # old logic this flipped to a fail; under the fix it passes for the
    # same reason as the note above -- zeros are exempt.
    original = "loop-voice-channel run-end: 10 done, 0 parked, 0 gated, 0 proposed."
    rewritten = (
        "Ten done. Built the voice channel for the loop-Fish Audio "
        "text-to-speech sending voice memos to Telegram on gates and "
        "run-end, with text fallback if speech fails."
    )
    assert voice._meets_briefing_bar(original, rewritten) is True


def test_meets_briefing_bar_one_does_not_match_inside_done():
    # A non-zero count of "1" must not be considered present just because
    # the rewrite happens to contain "done" (which contains "one" as a
    # substring). No word-boundary "one" and no digit "1" -> the count is
    # genuinely missing, so the bar must fail.
    original = "1 done: shipped the reminder feature to production today."
    rewritten = "Done: shipped the reminder feature to production today."
    assert voice._meets_briefing_bar(original, rewritten) is False


def test_meets_briefing_bar_dropped_non_zero_count_still_fails():
    # "two parked" silently vanishing is exactly the misleading briefing
    # this bar exists to catch -- a non-zero count going missing must still
    # fail even though zero counts are now exempt.
    original = "9 done, 2 parked, 0 gated, 0 proposed. Built the voice channel."
    rewritten = (
        "Nine done, nothing gated, nothing proposed. Built the voice channel."
    )
    assert voice._meets_briefing_bar(original, rewritten) is False


def test_meets_briefing_bar_ignores_ticket_id_digits():
    # Ticket ids (e.g. T-004) carry digits that aren't "counts" -- the
    # register asks the model not to read them aloud, so a rewrite that
    # drops the id in favor of a plain phrase must not be penalized for it.
    original = "T-004 done: reminder feature shipped to production today."
    rewritten = "The reminder feature is done and shipped to production."
    assert voice._meets_briefing_bar(original, rewritten) is True


# ---- T-012: the number-word cliff at eleven, and the silent fallback ------

def test_count_present_eleven_done_satisfies_count_of_11():
    assert voice._count_present("11", set(), "eleven done") is True


def test_count_present_twenty_one_satisfies_count_of_21():
    assert voice._count_present("21", set(), "we shipped twenty-one things") is True


def test_meets_briefing_bar_the_real_11_ticket_cliff_case_now_passes():
    # The exact vector from this ticket's Trace: an 11-ticket run-end note
    # whose correctly-briefed rewrite used to fail the bar purely because
    # "11" had no entry in the old 0-10 _NUMBER_WORDS table.
    original = (
        "loop-voice-channel run-end: 11 done, 0 parked, 0 gated, 0 proposed. "
        "Built the voice channel for the loop: Fish Audio text-to-speech "
        "over Telegram, gate pages and a run-end summary, with a text "
        "fallback when voice fails."
    )
    rewritten = (
        "Eleven done, nothing blocked. We built a voice channel for the "
        "loop that speaks status updates over Telegram using Fish Audio, "
        "with a text fallback if the voice send ever fails."
    )
    assert voice._meets_briefing_bar(original, rewritten) is True


def test_meets_briefing_bar_dropped_non_zero_count_still_fails_past_ten():
    # The cliff fix must not loosen the "a dropped non-zero count fails"
    # guarantee for counts above ten.
    original = "11 done, 3 parked, 0 gated, 0 proposed. Built the voice channel."
    rewritten = "Eleven done, nothing gated, nothing proposed. Built the voice channel."
    assert voice._meets_briefing_bar(original, rewritten) is False


def test_rewrite_for_speech_prints_stderr_diagnostic_on_rejection(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(
            returncode=0, stdout="We finished building the voice thing.\n",
        ),
    )

    fish_cfg = voice.load_fish_config(fish_path)
    message = "loop-voice-channel run-end: 9 done, 0 parked, 0 gated, 0 proposed."
    result = voice.rewrite_for_speech(message, fish_cfg)

    assert result == message
    err = capsys.readouterr().err
    assert "loop_voice:" in err
    assert "briefing rewrite rejected" in err
    assert "missing count(s) 9" in err


def test_rewrite_for_speech_prints_nothing_to_stderr_on_acceptance(tmp_path, monkeypatch, capsys):
    fish_path = write_fish_config(tmp_path)
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/claude")
    rewritten = "Nine done, nothing parked. Built the voice channel for the loop."
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout=rewritten + "\n"),
    )

    fish_cfg = voice.load_fish_config(fish_path)
    message = "loop-voice-channel run-end: 9 done, 0 parked, 0 gated, 0 proposed."
    result = voice.rewrite_for_speech(message, fish_cfg)

    assert result == rewritten
    assert capsys.readouterr().err == ""
