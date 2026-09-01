from pathlib import Path
import plistlib


PLIST_PATH = Path(
    "docker/macos/com.arts1996.market-signal-lab-forward-shadow.plist"
)


def test_mac_forward_shadow_schedule_is_live_daily_and_weekday_only():
    with PLIST_PATH.open("rb") as handle:
        schedule = plistlib.load(handle)

    arguments = schedule["ProgramArguments"]
    assert arguments[0] == "/bin/zsh"
    assert arguments[1].endswith("docker/macos/run-forward-shadow.zsh")
    assert schedule["WorkingDirectory"].endswith("/market-signal-lab")
    intervals = schedule["StartCalendarInterval"]
    assert {interval["Weekday"] for interval in intervals} == {2, 3, 4, 5, 6}
    assert {(interval["Hour"], interval["Minute"]) for interval in intervals} == {
        (18, 30),
        (20, 30),
        (22, 30),
    }
    assert schedule["RunAtLoad"] is True


def test_mac_forward_shadow_wrapper_classifies_host_failures():
    source = Path("docker/macos/run-forward-shadow.zsh").read_text(encoding="utf-8")

    assert "docker_unavailable" in source
    assert "database_unavailable" in source
    assert "jobs/run_forward_shadow.py --daily --not-before-jst 18:30" in source
    assert 'record_attempt "skipped" "concurrent_run" "$STATUS"' in source
