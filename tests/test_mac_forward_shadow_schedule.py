from pathlib import Path
import plistlib


PLIST_PATH = Path(
    "docker/macos/com.arts1996.market-signal-lab-forward-shadow.plist"
)


def test_mac_forward_shadow_schedule_is_live_daily_and_weekday_only():
    with PLIST_PATH.open("rb") as handle:
        schedule = plistlib.load(handle)

    arguments = schedule["ProgramArguments"]
    assert arguments[0] == "/usr/local/bin/docker"
    assert arguments[-1] == "--daily"
    assert "--demo" not in arguments
    assert schedule["WorkingDirectory"].endswith("/market-signal-lab")
    intervals = schedule["StartCalendarInterval"]
    assert {interval["Weekday"] for interval in intervals} == {2, 3, 4, 5, 6}
    assert {(interval["Hour"], interval["Minute"]) for interval in intervals} == {
        (18, 30)
    }
