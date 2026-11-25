import asyncio
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path
from queue import Queue
from threading import Event
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_registration import cli, gui_support  # noqa: E402
from academy_registration.core import Course, Meeting  # noqa: E402
from academy_registration.files import Journal, write_json  # noqa: E402
from academy_registration.preferences import dump_text, parse_text  # noqa: E402

COURSE = Course(
  "A",
  "1",
  (
    Meeting(2, 1, 2, date(2027, 1, 4), date(2027, 1, 4)),
    Meeting(3, 4, 6, date(2027, 1, 19), date(2027, 1, 19)),
  ),
)
SETTINGS = {
  "username": "fixture",
  "semester": "Fixture",
  "opening": "2099-01-05T08:00:00+07:00",
  "refresh_seconds": 15,
  "window_minutes": 20,
  "courses": [],
}


class GuiSupportTests(unittest.TestCase):
  def test_grid_shows_actual_teaching_weeks_and_periods(self):
    weeks = gui_support.week_grid([COURSE])
    self.assertEqual(list(weeks), [date(2027, 1, 4), date(2027, 1, 18)])
    self.assertEqual(weeks[date(2027, 1, 4)][1][0], ["A/1"])
    self.assertEqual(weeks[date(2027, 1, 18)][6][1], ["A/1"])
    self.assertNotIn(3, weeks[date(2027, 1, 18)])

  def test_suggested_group_is_copied_as_an_exact_selection(self):
    self.assertEqual(gui_support.subject_codes("A, B C\nD"), ["A", "B", "C", "D"])
    raw = {"open": [{"code": "A", "group": "1", "name": "Subject A"}]}
    courses = gui_support.proposal_preferences([COURSE], raw)
    config = {**SETTINGS, "courses": courses}
    self.assertEqual(
      parse_text(dump_text(config))["courses"],
      [
        {
          "code": "A",
          "name": "Subject A",
          "groups": [
            {"group": "1", "slots": [{"day": 2, "periods": [1, 2]}, {"day": 3, "periods": [4, 6]}]}
          ],
        }
      ],
    )
    self.assertEqual(
      parse_text(dump_text(SETTINGS, allow_empty_courses=True), allow_empty_courses=True)["courses"], []
    )

  def test_worker_cancellation_keeps_pending_submission_and_updates_the_report(self):
    events, entered, cleaned = Queue(), Event(), Event()
    with tempfile.TemporaryDirectory() as directory:
      folder = Path(directory)
      args = Namespace(submit=True, evidence_dir=folder / "reports", password="fixture-secret")

      async def in_flight(options, config):
        with Journal(folder / "state", "fixture", SETTINGS["opening"]) as journal:
          journal.data["pending"] = "A/1"
          journal.save()
          write_json(options.evidence_dir / "result.json", {"result": "login_verified"})
          entered.set()
          try:
            await asyncio.Event().wait()
          finally:
            cleaned.set()

      with patch.object(cli, "_run_browser", side_effect=in_flight):
        job = gui_support.BrowserJob(events)
        job.start(args, SETTINGS)
        try:
          self.assertTrue(entered.wait(5))
        finally:
          job.cancel()
          job.cancel()
        while True:
          kind, message = events.get(timeout=5)
          if kind == "finished":
            break
      self.assertIn("check the portal before retrying", message)
      self.assertTrue(cleaned.is_set())
      self.assertIsNone(args.password)
      with Journal(folder / "state", "fixture", SETTINGS["opening"]) as journal:
        self.assertEqual(journal.data["pending"], "A/1")
      report = next((folder / "reports").glob("*/result.json"))
      self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["result"], "interrupted")


@unittest.skipUnless(os.environ.get("ACADEMY_GUI_TESTS") == "1", "Requires a graphical test session")
class DesktopGuiTests(unittest.TestCase):
  def setUp(self):
    from academy_registration import gui

    self.gui = gui
    self.root = gui.tk.Tk()
    self.root.withdraw()
    self.addCleanup(self.root.destroy)
    self.app = gui.App(self.root)
    self.app.show_config(SETTINGS)
    self.root.update_idletasks()

  def test_preview_draws_the_grid_and_results_and_can_copy_the_group(self):
    from academy_registration.suggestions import Schedule

    raw = {"open": [{"code": "A", "group": "1", "name": "Subject A"}], "registered": []}
    suggestion = Schedule((), (COURSE,), 2, 0, 1, 3, True, ())
    self.app.show_results(([], [], raw), (), suggestion)
    cells = self.app.grid.item(self.app.grid.get_children()[0], "values")
    self.assertEqual(cells[:2], ("1", "A/1"))
    result = self.app.results.item(self.app.results.get_children()[0], "values")
    self.assertEqual(result, ("A/1", "Subject A", "Suggested"))
    self.app.week.current(1)
    self.app.draw_week()
    self.assertEqual(self.app.grid.item(self.app.grid.get_children()[3], "values")[2], "A/1")
    self.app.use_suggestion()
    self.assertEqual(self.app.config(require_courses=True)["courses"][0]["groups"][0]["group"], "1")

  def test_password_field_is_masked_used_and_never_saved(self):
    self.assertEqual(self.app.password_entry.grid_info()["row"], 1)
    self.assertTrue(self.app.password_entry.cget("show"))
    self.app.password.set(" fixture-secret ")
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "preferences.txt"
      with patch.object(self.gui.filedialog, "asksaveasfilename", return_value=str(path)):
        self.app.save()
      self.assertNotIn("fixture-secret", path.read_text(encoding="utf-8"))
    self.app.values["Semester"].set("")
    self.app.values["Opens"].set("")
    with (
      patch.dict(os.environ, {"ACADEMY_PASSWORD": "fallback"}),
      patch.object(self.gui, "BrowserJob") as worker,
    ):
      self.app.start(False)
      args, config = worker.return_value.start.call_args.args
      self.assertEqual(config, {"username": "fixture", "semester": "", "courses": []})
      self.assertEqual(args.password, " fixture-secret ")
      self.assertEqual(self.app.password.get(), "")

  def test_schedule_confirms_before_starting_and_cancel_signals_the_worker(self):
    courses = gui_support.proposal_preferences([COURSE], {"open": []})
    self.app.show_config({**SETTINGS, "courses": courses})
    with (
      patch.object(self.gui.messagebox, "askyesno", return_value=True) as confirm,
      patch.dict(os.environ, {"ACADEMY_PASSWORD": "fixture-secret"}),
      patch.object(self.gui, "BrowserJob") as worker,
    ):
      self.app.start(True)
      args, config = worker.return_value.start.call_args.args
      self.assertTrue(args.submit)
      self.assertIsNone(args.suggest)
      self.assertEqual(config["courses"][0]["code"], "A")
      self.assertIn("Hanoi time", confirm.call_args.args[1])
      self.app.cancel()
      worker.return_value.cancel.assert_called_once()
