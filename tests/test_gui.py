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
from academy_registration import cli, gui_support
from academy_registration.core import Course, Meeting
from academy_registration.files import Journal, write_json
from academy_registration.preferences import dump_text, parse_text

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
  def test_grid_groups_periods_into_sessions_in_actual_teaching_weeks(self):
    weeks = gui_support.week_grid([COURSE], {("A", "1"): "Subject A"})
    self.assertEqual(list(weeks), [date(2027, 1, 4), date(2027, 1, 18)])
    self.assertEqual(weeks[date(2027, 1, 4)][1][0], ["Subject A (A)"])
    self.assertEqual(weeks[date(2027, 1, 18)][2][1], ["Subject A (A)"])
    self.assertNotIn(1, weeks[date(2027, 1, 18)])

  def test_grid_keeps_subjects_that_share_or_span_sessions(self):
    day = date(2027, 1, 4)
    other = Course("B", "2", (Meeting(2, 3, 5, day, day),))
    sessions = gui_support.week_grid([COURSE, other], {("A", "1"): "Subject A"})[day]
    self.assertEqual(sessions[1][0], ["Subject A (A)", "B"])
    self.assertEqual(sessions[2][0], ["B"])

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

    self.app.values["Semester"].set("")
    self.app.values["Date"].set("")
    self.app.values["Time"].set("")
    raw = {
      "open": [{"code": "A", "group": "1", "name": "Subject A"}],
      "registered": [],
      "semester": "Portal semester",
    }
    suggestion = Schedule((), (COURSE,), 2, 0, 1, 3, True, ())
    self.app.set_running(True)
    self.app.events.put(("result", (([], [], raw), (), suggestion)))
    self.app.events.put(("finished", "Schedule ready."))
    self.app.drain_events()
    self.root.deiconify()
    self.root.update_idletasks()
    timetable = self.app.timetable
    self.assertEqual(timetable.grid.grid_slaves(row=1, column=0)[0]["text"], "1\nPeriods 1–3")
    self.assertEqual(timetable.grid.grid_slaves(row=1, column=1)[0]["text"], "Subject A (A)")
    result = self.app.results.item(self.app.results.get_children()[0], "values")
    self.assertEqual(result, ("A/1", "Subject A", "Suggested"))
    self.app.week.current(1)
    self.app.draw_week()
    self.root.update_idletasks()
    self.assertEqual(timetable.grid.grid_slaves(row=2, column=2)[0]["text"], "Subject A (A)")
    self.assertEqual(timetable.grid.grid_slaves(row=1, column=1)[0]["text"], "")
    self.assertEqual(timetable.grid.grid_bbox(0, 1)[3], timetable.session_height)
    self.assertEqual(timetable.grid.grid_bbox(0, 2)[3], timetable.session_height)
    self.assertTrue(self.app.apply_button.instate(["!disabled"]))
    self.app.apply_button.invoke()
    self.assertIn("Enter the opening date and time", self.app.status.get())
    self.assertEqual(self.app.values["Semester"].get(), "Portal semester")
    self.assertEqual(self.app.values["Date"].get(), "")
    self.assertEqual(self.app.values["Time"].get(), "")
    self.app.values["Date"].set("05/01/2099")
    self.app.values["Time"].set("08:00")
    config = self.app.config(require_courses=True)
    self.assertEqual(config["opening"], SETTINGS["opening"])
    self.assertEqual(config["courses"], gui_support.proposal_preferences([COURSE], raw))

  def test_copy_requires_confirmation_to_replace_an_unfinished_course_draft(self):
    from academy_registration.suggestions import Schedule

    draft = "Course: unfinished\nGroup:"
    self.app.set_course_text(draft)
    raw = {"open": [], "registered": [], "semester": "Portal semester"}
    self.app.show_results(([], [], raw), (), Schedule((), (COURSE,), 2, 0, 1, 3, True, ()))
    self.app.set_running(False)
    with patch.object(self.gui.messagebox, "askyesno", side_effect=[False, True]) as confirm:
      self.app.apply_button.invoke()
      self.assertEqual(self.app.courses.get("1.0", "end-1c"), draft)
      self.app.apply_button.invoke()
    self.assertEqual(confirm.call_count, 2)
    self.assertEqual(self.app.config(require_courses=True)["courses"][0]["code"], "A")
    self.assertEqual(self.app.values["Semester"].get(), SETTINGS["semester"])

  def test_language_switch_updates_visible_data_and_preserves_editor(self):
    from academy_registration.suggestions import Schedule

    self.app.set_course_text("Course: draft")
    self.app.password.set("fixture-secret")
    raw = {"open": [{"code": "A", "group": "1", "name": "Học phần A"}], "registered": []}
    self.app.show_results(([], [], raw), (), Schedule((), (COURSE,), 2, 0, 1, 3, True, ()))
    self.app.week.current(1)
    self.app.draw_week()
    self.app.events.put(("status", "Ready to go at 08:00 (Hanoi time)."))
    self.app.drain_events()
    self.root.deiconify()
    self.root.update_idletasks()
    self.app.language_picker.current(1)
    self.app.language_picker.event_generate("<<ComboboxSelected>>")
    self.assertEqual(self.root.title(), "Đăng ký học phần")
    self.assertEqual(self.app.preview_button["text"], "Gợi ý lịch học")
    self.assertIn("2 ngày đi học", self.app.metrics.get())
    self.assertIn("Sẵn sàng đăng ký lúc 08:00", self.app.log.get("1.0", "end"))
    self.assertEqual(self.app.results.heading("Status", "text"), "Trạng thái")
    item = self.app.results.get_children()[0]
    self.assertEqual(self.app.results.set(item, "Status"), "Được gợi ý")
    timetable = self.app.timetable.grid
    self.assertEqual(timetable.grid_slaves(row=0, column=2)[0]["text"], "Thứ Ba")
    self.assertEqual(timetable.grid_slaves(row=2, column=0)[0]["text"], "2\nTiết 4–6")
    self.assertEqual(timetable.grid_slaves(row=2, column=2)[0]["text"], "Học phần A (A)")
    self.assertEqual(self.app.week.current(), 1)
    self.assertEqual(self.app.courses.get("1.0", "end-1c"), "Course: draft")
    self.assertEqual(self.app.password.get(), "fixture-secret")
    self.app.language_picker.current(0)
    self.app.language_picker.event_generate("<<ComboboxSelected>>")
    self.assertEqual(self.app.preview_button["text"], "Suggest schedule")
    self.assertIn("Ready to go at 08:00", self.app.log.get("1.0", "end"))

  def test_action_buttons_fit_at_minimum_window_size_in_both_languages(self):
    self.root.geometry("820x600")
    self.root.deiconify()
    buttons = (self.app.preview_button, self.app.register_button, self.app.cancel_button)
    for language in (0, 1):
      self.app.language_picker.current(language)
      self.app.language_picker.event_generate("<<ComboboxSelected>>")
      self.root.update()
      for button in buttons:
        with self.subTest(language=language, button=button["text"]):
          self.assertTrue(button.winfo_viewable())
          self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
          self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())

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
    self.app.values["Date"].set("")
    self.app.values["Time"].set("")
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
