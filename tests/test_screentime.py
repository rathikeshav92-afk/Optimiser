import datetime as dt
from contextlib import closing

import pytest

from pcoptimizer import screentime as st


@pytest.fixture
def db(tmp_path):
    return tmp_path / "st.db"


def add(db, day, hour, app, seconds, name=None):
    with closing(st.connect(db)) as conn:
        st.record(conn, {(day.isoformat(), hour, app): seconds}, {app: name or st.pretty_name(app)})


def test_period_ranges():
    wed = dt.date(2026, 10, 21)
    assert st.period_range("week", wed)[:2] == (dt.date(2026, 10, 19), dt.date(2026, 10, 25))
    assert st.period_range("month", wed)[:2] == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert st.period_range("month", dt.date(2024, 2, 10))[1] == dt.date(2024, 2, 29)
    assert st.shift("month", dt.date(2026, 1, 15), -1) == dt.date(2025, 12, 1)
    assert st.shift("week", wed, -1) == dt.date(2026, 10, 14)
    assert st.period_range("day", dt.date.today())[2] == "Today"


def test_report_aggregates_and_compares(db):
    today = dt.date.today()
    add(db, today, 9, "chrome.exe", 1800)
    add(db, today, 9, "chrome.exe", 600)          # same bucket accumulates
    add(db, today, 14, "code.exe", 3600, "Visual Studio Code")
    add(db, today - dt.timedelta(1), 10, "chrome.exe", 1200)
    rep = st.report("day", today, db)
    assert rep.total == 6000
    assert [(a.name, a.seconds) for a in rep.apps] == [("Visual Studio Code", 3600), ("Google Chrome", 2400)]
    assert rep.series[9] == ("09", 2400) and len(rep.series) == 24
    assert rep.previous_total == 1200

    month = st.report("month", today, db)
    assert len(month.series) == st.period_range("month", today)[1].day
    assert month.days_with_data == (2 if today.day > 1 else 1)


def test_tracker_counts_only_active_foreground_time(db):
    now = [dt.datetime(2026, 10, 20, 10, 0, 0).timestamp()]
    app = [("chrome.exe", r"C:\\chrome.exe")]
    idle = [0.0]
    with closing(st.connect(db)) as conn:
        tr = st.Tracker(conn, probe=lambda: app[0], idle=lambda: idle[0], clock=lambda: now[0],
                        watching=lambda: False)
        for _ in range(12):           # 60 s of Chrome
            now[0] += 5
            tr.tick()
        app[0] = ("LockApp.exe", "x")  # lock screen is ignored
        for _ in range(4):
            now[0] += 5
            tr.tick()
        app[0] = ("discord.exe", "x")
        idle[0] = 600                  # away from keyboard: not counted
        for _ in range(4):
            now[0] += 5
            tr.tick()
        now[0] += 3600                 # PC slept: the gap is not counted
        idle[0] = 0
        tr.tick()
        tr.flush()
    rep = st.report("day", dt.date(2026, 10, 20), db)
    assert {a.app: a.seconds for a in rep.apps} == {"chrome.exe": 60}


def test_watching_video_counts_while_idle(db):
    now = [dt.datetime(2026, 10, 20, 21, 0).timestamp()]
    with closing(st.connect(db)) as conn:
        tr = st.Tracker(conn, probe=lambda: ("vlc.exe", "x"), idle=lambda: 900, clock=lambda: now[0],
                        watching=lambda: True)
        for _ in range(6):
            now[0] += 5
            tr.tick()
        tr.flush()
    assert st.report("day", dt.date(2026, 10, 20), db).total == 30


def test_clear_and_format(db):
    add(db, dt.date.today(), 8, "x.exe", 100)
    st.clear(db)
    assert st.report("day", None, db).total == 0
    assert st.fmt_duration(0) == "0m"
    assert st.fmt_duration(45) == "45s"
    assert st.fmt_duration(3 * 3600 + 5 * 60) == "3h 05m"
    assert st.pretty_name("my_cool-game.exe") == "My Cool Game"
