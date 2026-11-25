"""Both input formats and the setup wizard must retain the same safeguards."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from platform_checks import assert_private

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_registration import cli, core, preferences

EXAMPLE = Path(core.__file__).with_name("preferences.example.json")
TEXT = """Student: fixture
Semester: Example semester
Opens: 2027-01-01 08:00 Hanoi

Course: IE.022.03 | Thương mại điện tử
Group: 1 | Wed 10-12, Thu 7-9
"""


@contextmanager
def working_directory(path):
  previous = Path.cwd()
  os.chdir(path)
  try:
    yield
  finally:
    os.chdir(previous)


class TextPreferencesTests(unittest.TestCase):
  def test_examples_and_round_trip_have_identical_planning_behavior(self):
    original = core.load_config(EXAMPLE)
    parsed = core.load_config(EXAMPLE.with_suffix(".txt"))
    self.assertEqual(preferences.text_shape(original), parsed)
    self.assertEqual(preferences.parse_text(preferences.dump_text(original)), parsed)
    expected = [r.key for r in core.choose(core.demo_courses(original), [], original)]
    self.assertEqual([r.key for r in core.choose(core.demo_courses(parsed), [], parsed)], expected)

  def test_optional_defaults_and_hanoi_opening(self):
    config = preferences.parse_text(TEXT)
    self.assertEqual(config["opening"], "2027-01-01T08:00:00+07:00")
    self.assertEqual(config["refresh_seconds"], 15)
    self.assertEqual(config["window_minutes"], 20)
    self.assertEqual(
      config["courses"][0]["groups"][0]["slots"],
      [{"day": 4, "periods": [10, 12]}, {"day": 5, "periods": [7, 9]}],
    )

  def test_split_opening_round_trips_explicit_offset_and_seconds(self):
    text = TEXT.replace(
      "Opens: 2027-01-01 08:00 Hanoi", "Date: 25/11/2027\nTime: 03:30:15.123456+02:00"
    )
    config = preferences.parse_text(text)
    self.assertEqual(config["opening"], "2027-11-25T03:30:15.123456+02:00")
    self.assertIn("Date: 25/11/2027\nTime: 03:30:15.123456+02:00", preferences.dump_text(config))
    self.assertEqual(preferences.parse_text(preferences.dump_text(config)), config)
    self.assertIn("Date: 01/01/2027\nTime: 08:00", preferences.dump_text(preferences.parse_text(TEXT)))

  def test_split_opening_rejects_invalid_or_ambiguous_fields(self):
    for opening, message in (
      ("Date: 2027-11-25\nTime: 08:00", "DD/MM/YYYY"),
      ("Date: 31/02/2027\nTime: 08:00", "DD/MM/YYYY"),
      ("Date: 25/11/2027\nTime: 24:00", "24-hour"),
      ("Date: 25/11/2027", "both Date and Time"),
      ("Date: 25/11/2027\nTime: 08:00\nOpens: 2027-01-01 08:00 Hanoi", "older Opens"),
    ):
      text = TEXT.replace("Opens: 2027-01-01 08:00 Hanoi", opening)
      with self.subTest(opening=opening), self.assertRaisesRegex(core.StopRun, message):
        preferences.parse_text(text)

  def test_weekday_names_abbreviations_and_portal_numbers(self):
    for label in ("Monday", "MON", "Thứ 2", "2"):
      self.assertEqual(preferences.parse_slots(label + " 7-9"), [{"day": 2, "periods": [7, 9]}])

  def test_malformed_lines_have_line_numbers_without_echoing_values(self):
    bad_lines = (
      "Student: duplicate",
      "Password: fixture-secret",
      "Group: 1 | Sun 7-9",
      "Group: 1 | Wed 7-9",
      "Refresh: fifteen",
      "unexpected fixture-secret",
    )
    for bad in bad_lines:
      with self.subTest(bad=bad), self.assertRaises(core.StopRun) as error:
        preferences.parse_text(TEXT + bad + "\n")
      self.assertNotIn("fixture-secret", str(error.exception))
      if bad != "Group: 1 | Wed 7-9":
        self.assertIn("line", str(error.exception))

  def test_group_must_follow_course(self):
    with self.assertRaisesRegex(core.StopRun, "line 1"):
      preferences.parse_text("Group: 1 | Mon 7-9")

  def test_unicode_bom_and_json_backwards_compatibility(self):
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      (folder / "plan.txt").write_text("\ufeff" + TEXT, encoding="utf-8")
      config = core.load_config(folder / "plan.txt")
      (folder / "plan.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8-sig")
      self.assertEqual(core.load_config(folder / "plan.json"), config)

  def test_serializer_rejects_injected_settings(self):
    config = core.load_config(EXAMPLE)
    config["semester"] += "\nCourse: INJECTED"
    with self.assertRaises(core.StopRun):
      preferences.dump_text(config)

  def test_default_discovery_refuses_to_guess_between_files(self):
    with (
      tempfile.TemporaryDirectory() as folder,
      working_directory(folder),
      patch.object(cli, "run_browser") as browser,
    ):
      Path("preferences.txt").write_text(TEXT, encoding="utf-8")
      Path("preferences.json").write_text(json.dumps(preferences.parse_text(TEXT)))
      with self.assertLogs("academy", level="ERROR"):
        self.assertEqual(cli.main(["--check-config"]), 2)
      with patch("sys.stdout", new=io.StringIO()) as output:
        self.assertEqual(cli.main(["--check-config", "--config", "preferences.txt"]), 0)
      self.assertIn("Opens 01/01/2027 08:00 Hanoi time", output.getvalue())
      browser.assert_not_called()

  def test_new_template_defaults_to_text(self):
    with (
      tempfile.TemporaryDirectory() as folder,
      working_directory(folder),
      patch("sys.stdout", new=io.StringIO()),
    ):
      self.assertEqual(cli.main(["--init-config"]), 0)
      self.assertTrue(Path("preferences.txt").exists())
      assert_private(self, Path("preferences.txt"), 0o600)
      self.assertEqual(cli.main(["--check-config"]), 0)


class WizardTests(unittest.TestCase):
  def answers(self, save="y"):
    return [
      "fixture",
      "Example semester",
      "01/01/2027",
      "",
      "IE.022.03",
      "1",
      "Wed 10-12, Thu 7-9",
      "",
      "",
      save,
    ]

  def test_wizard_creates_text_and_json_offline_after_review(self):
    for suffix in (".txt", ".json"):
      with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / ("plan" + suffix)
        with (
          patch("builtins.input", side_effect=self.answers()),
          patch("sys.stdout", new=io.StringIO()) as output,
        ):
          with patch.object(cli, "run_browser") as browser:
            self.assertEqual(cli.main(["--configure", "--config", str(path)]), 0)
            browser.assert_not_called()
        config = core.load_config(path)
        self.assertEqual(preferences.parse_opening(config["opening"]), "2027-01-01T08:00:00+07:00")
        self.assertEqual(config["courses"][0]["groups"][0]["slots"][1]["day"], 5)
        assert_private(self, path, 0o600)
        self.assertIn("Here's your plan", output.getvalue())

  def test_invalid_schedule_and_duplicate_group_are_reprompted(self):
    answers = [
      "fixture",
      "Example",
      "2027-01-01",
      "01/01/2027",
      "08:00",
      "A",
      "1",
      "Sun 9-7",
      "Mon 7-9",
      "1",
      "",
      "",
      "y",
    ]
    with tempfile.TemporaryDirectory() as folder:
      path = Path(folder) / "plan.txt"
      with (
        patch("builtins.input", side_effect=answers),
        patch("sys.stdout", new=io.StringIO()) as output,
      ):
        preferences.configure(path)
      self.assertIn("try again", output.getvalue())
      self.assertEqual(len(core.load_config(path)["courses"][0]["groups"]), 1)

  def test_cancellation_eof_and_existing_file_never_overwrite(self):
    with tempfile.TemporaryDirectory() as folder, patch("sys.stdout", new=io.StringIO()):
      path = Path(folder) / "plan.txt"
      with patch("builtins.input", side_effect=self.answers("n")):
        preferences.configure(path)
      self.assertFalse(path.exists())
      with patch("builtins.input", side_effect=EOFError), self.assertRaises(core.StopRun):
        preferences.configure(path)
      self.assertFalse(path.exists())
      path.write_text("keep")
      with patch("builtins.input") as prompt, self.assertRaises(core.StopRun):
        preferences.configure(path)
      prompt.assert_not_called()
      self.assertEqual(path.read_text(encoding="utf-8"), "keep")
