"""Automatic run evidence must remain private and never retry a submission."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from platform_checks import assert_private

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_registration import cli, files


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
  async def test_unique_default_folders_and_no_mutation_of_options(self):
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as folder:
      os.chdir(folder)
      try:
        args = argparse.Namespace(evidence_dir=None)

        async def finish(options, config):
          files.write_json(options.evidence_dir / "result.json", {"result": "login_verified"})

        with patch.object(cli, "_run_browser", side_effect=finish) as browser:
          await cli.run_browser(args, {})
          await cli.run_browser(args, {})
        self.assertIsNone(args.evidence_dir)
        self.assertEqual(browser.await_count, 2)
        folders = list(Path("reports").iterdir())
        self.assertEqual(len(folders), 2)
        for directory in folders:
          assert_private(self, directory, 0o700)
          assert_private(self, (directory / "result.json"), 0o600)
      finally:
        os.chdir(previous)

  async def test_startup_failure_has_evidence_without_secret_error_text(self):
    with tempfile.TemporaryDirectory() as folder:
      args = argparse.Namespace(evidence_dir=Path(folder))
      with patch.object(cli, "_run_browser", side_effect=RuntimeError("fixture-secret")):
        with self.assertRaises(RuntimeError):
          await cli.run_browser(args, {})
      directory = next(Path(folder).iterdir())
      failure = json.loads((directory / "failure.json").read_text(encoding="utf-8"))
      self.assertEqual(failure["error_type"], "RuntimeError")
      self.assertEqual(
        json.loads((directory / "result.json").read_text(encoding="utf-8"))["result"], "stopped"
      )
      self.assertNotIn("fixture-secret", (directory / "failure.json").read_text(encoding="utf-8"))

  async def test_shutdown_failure_does_not_overwrite_verified_registration(self):
    with tempfile.TemporaryDirectory() as folder:
      args = argparse.Namespace(evidence_dir=Path(folder))

      async def verified_then_failed(options, config):
        files.write_json(options.evidence_dir / "result.json", {"result": "registration_verified"})
        raise RuntimeError("fixture shutdown")

      with (
        patch.object(cli, "_run_browser", side_effect=verified_then_failed),
        self.assertRaises(RuntimeError),
      ):
        await cli.run_browser(args, {})
      directory = next(Path(folder).iterdir())
      self.assertEqual(
        json.loads((directory / "result.json").read_text(encoding="utf-8"))["result"],
        "registration_verified",
      )
      self.assertTrue((directory / "failure.json").exists())

  async def test_interruption_is_recorded_without_swallowing_cancellation(self):
    with tempfile.TemporaryDirectory() as folder:
      with (
        patch.object(cli, "_run_browser", side_effect=asyncio.CancelledError),
        self.assertRaises(asyncio.CancelledError),
      ):
        await cli.run_browser(argparse.Namespace(evidence_dir=Path(folder)), {})
      result = next(Path(folder).glob("*/result.json"))
      self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["result"], "interrupted")

  async def test_capture_saves_screenshot_and_result(self):
    page = argparse.Namespace(url=cli.URL, screenshot=AsyncMock(return_value=b"fixture image"))
    site = argparse.Namespace(page=page, stage="verified")
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      await cli.capture(folder, site, "registration_verified")
      self.assertEqual((folder / "registration-verified.png").read_bytes(), b"fixture image")
      assert_private(self, (folder / "registration-verified.png"), 0o600)
      self.assertEqual(
        json.loads((folder / "result.json").read_text(encoding="utf-8"))["result"],
        "registration_verified",
      )
