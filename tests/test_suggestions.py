import importlib.util
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_registration import cli  # noqa: E402
from academy_registration.core import StopRun, load_config  # noqa: E402
from academy_registration.suggestions import day_fatigue, suggest  # noqa: E402

EXAMPLE = Path(cli.__file__).with_name("preferences.example.json")


def row(code, group, weekday, periods, *, enabled=True, checked=False, seats="5", dates=None):
  first, last = periods
  dates = dates or "06/04/26 đến 11/04/26"
  return {
    "code": code,
    "group": group,
    "enabled": enabled,
    "checked": checked,
    "remaining": seats,
    "name": code,
    "schedule": f"Thứ {weekday}, tiết {first} → {last}, {dates}",
  }


def raw(rows, *registered):
  return {
    "open": rows,
    "registered": [{"code": code, "group": group} for code, group in registered],
    "semester": "Fixture",
  }


def keys(schedule):
  return [course.key for course in schedule.additions]


class SuggestionTests(unittest.TestCase):
  def test_session_gaps_match_the_examples_and_periods_cross_session_boundaries(self):
    for sessions in ((1, 2, 4), (1, 3, 4)):
      mask = sum(7 << ((session - 1) * 3) for session in sessions)
      self.assertEqual(day_fatigue(mask), (1, 5, 6))
    self.assertEqual(day_fatigue(0b11100), (0, 1, 3))

  def test_days_packs_classes_and_fatigue_spreads_them(self):
    rows = raw(
      [
        row("A", "1", 2, (1, 3)),
        row("B", "1", 2, (7, 9)),
        row("B", "2", 3, (7, 9)),
      ]
    )
    days, fatigue = suggest(rows, "days"), suggest(rows, "fatigue")
    self.assertEqual(keys(days), ["A/1", "B/1"])
    self.assertEqual((days.days, days.skipped_sessions), (1, 1))
    self.assertEqual(keys(fatigue), ["A/1", "B/2"])
    self.assertEqual((fatigue.days, fatigue.skipped_sessions), (2, 0))

  def test_fatigue_prefer_shorter_stretches_even_without_gaps(self):
    rows = raw([row("A", "1", 2, (1, 3)), row("B", "1", 2, (4, 6)), row("B", "2", 3, (4, 6))])
    fatigue = suggest(rows, "fatigue")
    self.assertEqual(keys(fatigue), ["A/1", "B/2"])
    self.assertEqual(fatigue.longest_stretch, 3)

  def test_existing_groups_stay_fixed_and_unavailable_or_unreadable_groups_are_excluded(self):
    invalid = dict(row("UNKNOWN", "1", 3, (1, 3)), schedule="-")
    rows = raw(
      [
        row("A", "1", 2, (1, 3), enabled=False, seats="0"),
        row("A", "2", 2, (7, 9)),
        row("B", "1", 2, (1, 3)),
        row("B", "2", 3, (1, 3)),
        row("C", "1", 2, (7, 9), enabled=False),
        row("D", "1", 2, (7, 9), seats="0"),
        row("E", "1", 3, (4, 6), checked=True),
        invalid,
      ],
      ("A", "1"),
    )
    result = suggest(rows, "days")
    self.assertEqual(keys(result), ["B/2"])
    self.assertEqual([course.key for course in result.existing], ["A/1", "E/1"])
    self.assertEqual(result.unreadable, ("UNKNOWN/1",))
    with self.assertRaisesRegex(StopRun, "missing the timetable"):
      suggest(raw([], ("A", "1")), "days")

  def test_dates_prevent_false_conflicts_and_holiday_gaps(self):
    rows = raw(
      [
        row("A", "1", 2, (1, 3), dates="06/04/26 đến 06/04/26"),
        row("B", "1", 2, (1, 3), dates="20/04/26 đến 20/04/26"),
        row("C", "1", 2, (7, 9), dates="13/04/26 đến 13/04/26"),
      ]
    )
    result = suggest(rows, "fatigue")
    self.assertEqual(keys(result), ["A/1", "B/1", "C/1"])
    self.assertEqual((result.days, result.skipped_sessions), (3, 0))

  def test_include_and_exclude_apply_to_subject_codes_and_keep_existing_groups(self):
    rows = raw(
      [
        row("A", "1", 2, (1, 3), checked=True),
        row("B", "1", 2, (4, 6)),
        row("B", "2", 3, (1, 3)),
        row("C", "1", 2, (7, 9)),
        row("D", "1", 2, (10, 12)),
      ]
    )
    for objective in ("days", "fatigue"):
      result = suggest(rows, objective, include=["b", "C"], exclude=["a", "c"])
      self.assertTrue(keys(result)[0].startswith("B/"))
      self.assertEqual(len(result.additions), 1)
      self.assertEqual([course.key for course in result.existing], ["A/1"])
      self.assertEqual(keys(suggest(rows, objective, exclude=["B", "C"])), ["D/1"])

  def test_returns_best_found_and_marks_the_limit(self):
    rows = raw([row("A", "1", 2, (1, 3)), row("A", "2", 3, (1, 3))])
    result = suggest(rows, "days", limit=2)
    self.assertEqual(keys(result), ["A/1"])
    self.assertFalse(result.complete)

  def test_suggest_can_use_text_or_json_without_any_course_preferences(self):
    with tempfile.TemporaryDirectory() as directory:
      for suffix, content in (
        ("txt", "Student: fixture\nSemester: Fixture\nOpens: 2026-04-06 08:00 Hanoi\n"),
        (
          "json",
          '{"username":"fixture","semester":"Fixture","opening":"2026-04-06T08:00:00+07:00",'
          '"refresh_seconds":15,"window_minutes":20}',
        ),
      ):
        path = Path(directory) / f"settings.{suffix}"
        path.write_text(content, encoding="utf-8")
        self.assertEqual(load_config(path, allow_empty_courses=True)["courses"], [])
        with self.assertRaises(StopRun):
          load_config(path)


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Requires Playwright")
class SuggestionCommandTests(unittest.IsolatedAsyncioTestCase):
  async def test_browser_command_saves_a_proposal_without_submitting(self):
    snapshot = (
      [],
      [],
      raw(
        [
          row("UNLISTED", "1", 4, (7, 9)),
          row("EXCLUDED", "1", 4, (10, 12)),
        ]
      ),
    )
    site = SimpleNamespace(
      ready=AsyncMock(),
      snapshot=AsyncMock(return_value=snapshot),
      refresh=AsyncMock(return_value=snapshot),
    )
    page = SimpleNamespace(set_default_timeout=lambda _: None, goto=AsyncMock())
    browser = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    config = load_config(EXAMPLE)
    with tempfile.TemporaryDirectory() as directory:
      folder = Path(directory)
      args = Namespace(
        submit=False,
        suggest="days",
        include=["UNLISTED", "EXCLUDED"],
        exclude=["EXCLUDED"],
        catalog=False,
        state_dir=folder / "state",
        evidence_dir=folder,
        headless=True,
        browser_executable=None,
      )
      with (
        patch.object(cli, "Site", return_value=site) as site_factory,
        patch.object(cli, "launch_browser", return_value=browser),
        patch.object(cli, "capture") as capture,
      ):
        await cli._run_browser(args, config)
      site_factory.assert_called_once_with(page, {**config, "courses": []}, True)
      text = (folder / "suggested-schedule.txt").read_text(encoding="utf-8")
      self.assertIn("UNLISTED/1", text)
      self.assertNotIn("EXCLUDED/1", text)
      site.refresh.assert_any_await("open")
      capture.assert_awaited_once_with(folder, site, "schedule_suggested", snapshot, catalog=True)
      browser.close.assert_awaited_once()
