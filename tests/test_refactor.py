"""Regression checks for planner equivalence, private storage, and orchestration."""

import asyncio
import io
import itertools
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from platform_checks import assert_private, symlink_or_skip

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_registration import cli, core, files

EXAMPLE = Path(core.__file__).with_name("preferences.example.json")


def exhaustive_plan(rows, config):
  """Small, intentionally exhaustive oracle for the optimized search."""
  existing = {row.code: row for row in rows if row.checked}
  options = []
  for spec in config["courses"]:
    if spec["code"] in existing:
      options.append([(existing[spec["code"]], 0, 0)])
      continue
    options.append(
      [
        (row, rank, int(group.get("last_resort", False)))
        for rank, group in enumerate(spec["groups"])
        for row in rows
        if row.key == spec["code"] + "/" + group["group"] and row.enabled and row.remaining > 0
      ]
      + [(None, 999, 0)]
    )
  best = None
  for bundle in itertools.product(*options):
    selected = [choice[0] for choice in bundle if choice[0]]
    if any(core.clashes(a, b) for a, b in itertools.combinations(selected, 2)):
      continue
    score = (-len(selected), sum(choice[2] for choice in bundle), tuple(choice[1] for choice in bundle))
    if best is None or score < best[0]:
      best = score, [row.key for row in selected if row.code not in existing]
  return best[1]


class RefactorTests(unittest.TestCase):
  def test_pruned_planner_matches_exhaustive_search(self):
    generator = random.Random(42)
    with patch.object(core.LOG, "warning"):
      for _ in range(150):
        specs = [
          {
            "code": f"COURSE.{code}",
            "groups": [
              {
                "group": str(group),
                "days": [generator.randint(2, 6)],
                "periods": [start, start + 2],
                "last_resort": generator.choice([False, True]),
              }
              for group in range(3)
              for start in [generator.choice([1, 4, 7])]
            ],
          }
          for code in range(4)
        ]
        config = {"courses": specs}
        rows = [
          replace(row, enabled=generator.choice([True, False])) for row in core.demo_courses(config)
        ]
        if generator.choice([True, False]):
          rows[0] = replace(rows[0], checked=True)
        self.assertEqual(
          [row.key for row in core.choose(rows, [], config)], exhaustive_plan(rows, config)
        )

  def test_initialization_is_offline_private_and_never_overwrites(self):
    with tempfile.TemporaryDirectory() as folder, patch.object(cli, "run_browser") as browser:
      path = Path(folder) / "preferences.json"
      with patch("sys.stdout", new=io.StringIO()):
        self.assertEqual(cli.main(["--init-config", "--config", str(path)]), 0)
      content = path.read_bytes()
      assert_private(self, path, 0o600)
      self.assertEqual(core.load_config(path)["username"], "YOUR_STUDENT_ID")
      with self.assertLogs("academy", level="ERROR"):
        self.assertEqual(cli.main(["--init-config", "--config", str(path)]), 2)
      self.assertEqual(path.read_bytes(), content)
      browser.assert_not_called()

  def test_atomic_write_does_not_touch_link_targets(self):
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      target, report = folder / "target", folder / "report.json"
      target.write_text("keep")
      symlink_or_skip(self, report, target)
      if os.name == "nt":
        with self.assertRaisesRegex(OSError, "reparse point"):
          files.atomic_write(report, "new")
        self.assertTrue(report.is_symlink())
        report.unlink()
        files.atomic_write(report, "old")
      files.atomic_write(report, "new")
      self.assertEqual(target.read_text(encoding="utf-8"), "keep")
      self.assertEqual(report.read_text(encoding="utf-8"), "new")
      self.assertFalse(report.is_symlink())
      assert_private(self, report, 0o600)
      assert_private(self, folder, 0o700)
      with patch.object(files, "replace_file", side_effect=OSError("fixture failure")):
        with self.assertRaises(OSError):
          files.atomic_write(report, "incomplete")
      self.assertEqual(report.read_text(encoding="utf-8"), "new")
      self.assertEqual(sorted(p.name for p in folder.iterdir()), ["report.json", "target"])

  def test_symlinked_state_directory_and_lock_are_rejected(self):
    config = core.load_config(EXAMPLE)
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      real = folder / "real"
      real.mkdir()
      link = folder / "link"
      symlink_or_skip(self, link, real, target_is_directory=True)
      with self.assertRaises(core.StopRun):
        files.Journal(link, "fixture", config["opening"])
      target = folder / "target"
      target.write_text("keep")
      symlink_or_skip(self, real / "fixture.lock", target)
      with self.assertRaises(OSError):
        files.Journal(real, "fixture", config["opening"])
      self.assertEqual(target.read_text(encoding="utf-8"), "keep")

  def test_account_lock_and_pending_entry_work_across_processes(self):
    opening = core.load_config(EXAMPLE)["opening"]
    source = Path(__file__).resolve().parents[1]
    script = """
import sys
from pathlib import Path
from academy_registration.core import StopRun
from academy_registration.files import Journal
try:
  with Journal(Path(sys.argv[1]), 'fixture', sys.argv[2]) as journal:
    assert journal.data['pending'] == 'IE.014.02/2'
    print('pending entry found')
except StopRun as error:
  print(str(error))
  sys.exit(2)
"""
    with tempfile.TemporaryDirectory() as folder:
      command = [sys.executable, "-c", script, folder, opening]
      with files.Journal(Path(folder), "fixture", opening) as journal:
        journal.data["pending"] = "IE.014.02/2"
        journal.save()
        locked = subprocess.run(command, cwd=source, capture_output=True, text=True, timeout=15)
        self.assertEqual(locked.returncode, 2, locked.stderr)
        self.assertIn("Another run is already using this account", locked.stdout)
      reopened = subprocess.run(command, cwd=source, capture_output=True, text=True, timeout=15)
      self.assertEqual(reopened.returncode, 0, reopened.stderr)
      self.assertIn("pending entry found", reopened.stdout)

  def test_corrupt_journal_stops_and_releases_account_lock(self):
    config = core.load_config(EXAMPLE)
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      with files.Journal(folder, "fixture", config["opening"]) as journal:
        path = journal.path
      for data in ([], {"blocked": "bad"}, {"pending": False}, {"pending": "bad"}):
        path.write_text(json.dumps(data))
        with self.assertRaises(core.StopRun):
          files.Journal(folder, "fixture", config["opening"])
      path.write_text("{}")
      with files.Journal(folder, "fixture", config["opening"]) as journal:
        self.assertEqual(journal.data, {})

  def test_exports_escape_spreadsheet_formulas_and_terminal_controls(self):
    for value in ("=1+1", " \t=1+1", "\n@SUM(1)", "+1", "-1", "\tplain"):
      self.assertTrue(files.csv_cell(value).startswith("'"))
    self.assertEqual(files.csv_cell("Quản trị"), "Quản trị")
    self.assertEqual(files.csv_cell(3), 3)
    with patch("sys.stdout", new=io.StringIO()) as output:
      files.console("hello\x1b[31m\x00world")
      self.assertNotIn("\x1b", output.getvalue())
      self.assertNotIn("\x00", output.getvalue())

  def test_log_formatter_strips_portal_control_sequences(self):
    record = logging.LogRecord(
      "academy", logging.WARNING, "fixture", 1, "Course %s", ("IE.001\x1b[31m",), None
    )
    self.assertNotIn("\x1b", files.PlainFormatter("%(message)s").format(record))

  def test_origin_is_checked_before_reading_login_controls(self):
    from academy_registration.portal import Site

    config = core.load_config(EXAMPLE)
    page = SimpleNamespace(
      url="https://dangkymonhoc.dav.edu.vn.example.org/login", on=lambda *args: None
    )
    with self.assertRaisesRegex(core.StopRun, "went somewhere unexpected"):
      Site(page, config).check_origin()

  def test_invalid_config_errors_do_not_echo_field_values(self):
    with tempfile.TemporaryDirectory() as folder:
      path = Path(folder) / "config.json"
      config = core.load_config(EXAMPLE)
      config["opening"] = "fixture-secret-do-not-log"
      path.write_text(json.dumps(config))
      with self.assertRaises(core.StopRun) as error:
        core.load_config(path)
      self.assertNotIn(config["opening"], str(error.exception))


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
  async def test_no_seats_poll_only_at_refresh_interval(self):
    opening = datetime(2027, 1, 1, tzinfo=timezone.utc)
    clock = [opening]
    config = core.load_config(EXAMPLE)
    config.update(window_minutes=1)
    snapshot = ([], [], {})
    site = SimpleNamespace(
      config=config, refresh=AsyncMock(return_value=snapshot), snapshot=AsyncMock(return_value=snapshot)
    )
    sleeps = []

    async def sleep(seconds):
      sleeps.append(seconds)
      clock[0] += timedelta(seconds=seconds)

    with (
      patch.object(cli, "now", side_effect=lambda: clock[0]),
      patch.object(asyncio, "sleep", side_effect=sleep),
    ):
      with self.assertRaisesRegex(core.StopRun, "window ended"):
        await cli.register(site, SimpleNamespace(data={}), opening)
    self.assertEqual(sleeps, [15, 15, 15, 15])
    self.assertEqual(site.refresh.await_count, 4)
    self.assertEqual(site.snapshot.await_count, 1)

  async def test_already_registered_still_requires_server_refresh(self):
    config = core.load_config(EXAMPLE)
    rows = core.demo_courses(config)
    registered = [(row.code, row.group) for row in core.choose(rows, [], config)]
    snapshot = rows, registered, {}
    site = SimpleNamespace(
      config=config, refresh=AsyncMock(return_value=snapshot), snapshot=AsyncMock(return_value=snapshot)
    )
    opening = core.now() - timedelta(seconds=1)
    result = await cli.register(site, SimpleNamespace(data={}), opening)
    self.assertEqual(result, snapshot)
    self.assertEqual([call.args[0] for call in site.refresh.await_args_list], ["open", "registered"])

  async def test_evidence_failure_does_not_undo_verified_success_or_leak_errors(self):
    config = core.load_config(EXAMPLE)
    page = SimpleNamespace(
      url=cli.URL, screenshot=AsyncMock(side_effect=RuntimeError("fixture-secret"))
    )
    site = SimpleNamespace(config=config, page=page, stage="verified")
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      with self.assertLogs("academy", level="WARNING") as messages:
        await cli.capture(folder, site, "registration_verified")
      self.assertNotIn("fixture-secret", " ".join(messages.output))
      self.assertEqual(
        json.loads((folder / "result.json").read_text(encoding="utf-8"))["result"],
        "registration_verified",
      )
