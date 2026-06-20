# SPDX-License-Identifier: Apache-2.0
import sys

import pytest

from cairn import schedule


def test_parse_interval_units():
    assert schedule.parse_interval("30m") == 30
    assert schedule.parse_interval("45") == 45
    assert schedule.parse_interval("1h") == 60
    assert schedule.parse_interval("2h") == 120


def test_parse_interval_floor():
    with pytest.raises(ValueError):
        schedule.parse_interval("3m")


def test_render_plist_has_label_args_interval():
    p = schedule.render_plist("/usr/local/bin/cairn", "/Users/x/vault", 30, "/tmp/s.log")
    assert "<string>dev.agentcairn.sweep</string>" in p
    assert "<string>/usr/local/bin/cairn</string>" in p
    assert "<string>sweep</string>" in p and "<string>--vault</string>" in p
    assert "<integer>1800</integer>" in p  # 30m -> 1800s


def test_render_plist_escapes_xml():
    p = schedule.render_plist("/bin/cairn", "/v/a&b", 30, "/l.log")
    assert "a&amp;b" in p and "a&b" not in p


def test_render_cron_subhourly_and_hourly():
    assert schedule.render_cron_line("/c", "/v", 30, "/l").startswith("*/30 * * * * ")
    assert schedule.render_cron_line("/c", "/v", 120, "/l").startswith("0 */2 * * * ")
    assert schedule.render_cron_line("/c", "/v", 30, "/l").endswith("# agentcairn-sweep")
    assert ">> /l 2>&1" in schedule.render_cron_line("/c", "/v", 30, "/l")


def test_parse_interval_fractional_minutes():
    assert schedule.parse_interval("90.0m") == 90


def test_render_cron_quotes_paths_and_rejects_bad_interval():
    line = schedule.render_cron_line("/c", "/Users/x/my vault", 30, "/l")
    assert "'/Users/x/my vault'" in line
    with pytest.raises(ValueError):
        schedule.render_cron_line("/c", "/v", 90, "/l")  # 90 not expressible in cron


def _fake_run(records):
    def run(cmd, stdin=None):
        records.append((cmd, stdin))

        class R:
            returncode = 1 if cmd[:2] == ["crontab", "-l"] else 0
            stdout = ""

        return R()

    return run


def test_install_linux_writes_marked_cron(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    written = {}

    def run(cmd, stdin=None):
        class R:
            returncode = 1 if cmd[:2] == ["crontab", "-l"] else 0
            stdout = ""

        if cmd == ["crontab", "-"]:
            written["text"] = stdin
        return R()

    monkeypatch.setattr(schedule, "_run", run)
    schedule.install(30, tmp_path / "vault")
    assert "# agentcairn-sweep" in written["text"]
    assert "*/30 * * * *" in written["text"]


def test_install_linux_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    state = {"crontab": ""}

    def run(cmd, stdin=None):
        class R:
            returncode = 0 if state["crontab"] else 1
            stdout = state["crontab"]

        if cmd == ["crontab", "-"]:
            state["crontab"] = stdin
        return R()

    monkeypatch.setattr(schedule, "_run", run)
    schedule.install(30, tmp_path / "v")
    schedule.install(15, tmp_path / "v")
    assert state["crontab"].count("# agentcairn-sweep") == 1
    assert "*/15" in state["crontab"]


def test_uninstall_absent_is_false(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(schedule, "_run", _fake_run([]))
    assert schedule.uninstall() is False


def test_install_macos_writes_plist(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(schedule, "_run", _fake_run([]))
    schedule.install(30, tmp_path / "vault")
    plist = tmp_path / "Library" / "LaunchAgents" / "dev.agentcairn.sweep.plist"
    assert plist.exists() and "<integer>1800</integer>" in plist.read_text()
    assert schedule.status()["interval_min"] == 30


def test_unsupported_platform_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError):
        schedule.install(30, tmp_path / "v")
