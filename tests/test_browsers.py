"""Browser detection and fallback order without launching real browsers."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_registration import browsers


class DetectionTests(unittest.TestCase):
  def test_windows_checks_each_browser_registry_and_standard_paths(self):
    root = Path.cwd() / "Program Files"
    with (
      patch.dict(os.environ, {"ProgramFiles": str(root)}, clear=True),
      patch.object(browsers, "windows_app_paths", return_value=iter(())) as registry,
    ):
      self.assertEqual(
        list(browsers.candidates("win32")),
        [(name, root / relative) for name, relative in browsers.WINDOWS_BROWSERS],
      )
    self.assertEqual(
      registry.call_args_list, [call("chrome.exe"), call("msedge.exe"), call("brave.exe")]
    )

  def test_linux_checks_chrome_then_chromium_then_brave(self):
    names = {"google-chrome-stable", "ungoogled-chromium", "chromium", "brave-browser"}
    with patch.object(
      browsers.shutil, "which", side_effect=lambda name: name if name in names else None
    ):
      self.assertEqual(
        [name for name, _ in browsers.candidates("linux")],
        ["Chrome", "Ungoogled Chromium", "Chromium", "Brave"],
      )

  def test_macos_checks_system_and_user_applications(self):
    home = Path.cwd() / "User"
    with patch.object(Path, "home", return_value=home):
      result = list(browsers.candidates("darwin"))
    self.assertEqual([name for name, _ in result], ["Chrome", "Chrome"])
    self.assertEqual(result[1][1], home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Requires Playwright")
class LaunchTests(unittest.IsolatedAsyncioTestCase):
  async def test_tries_detected_browsers_then_bundled_chromium(self):
    from playwright.async_api import Error

    bundled = object()
    chromium = SimpleNamespace(
      launch=AsyncMock(side_effect=[Error("unavailable"), Error("unavailable"), bundled])
    )
    chrome, brave = Path("chrome"), Path("brave")
    with patch.object(
      browsers, "installed_browsers", return_value=[("Chrome", chrome), ("Brave", brave)]
    ):
      self.assertIs(await browsers.launch_browser(chromium, headless=True), bundled)
    self.assertEqual(
      chromium.launch.await_args_list,
      [
        call(headless=True, executable_path=chrome),
        call(headless=True, executable_path=brave),
        call(headless=True),
      ],
    )

  async def test_explicit_browser_skips_detection_and_fallback(self):
    from playwright.async_api import Error

    chromium = SimpleNamespace(launch=AsyncMock(side_effect=Error("unavailable")))
    with (
      patch.object(browsers, "installed_browsers") as detect,
      self.assertRaises(Error),
    ):
      await browsers.launch_browser(chromium, headless=False, executable="/custom/browser")
    detect.assert_not_called()
    chromium.launch.assert_awaited_once_with(headless=False, executable_path="/custom/browser")

  async def test_missing_bundled_browser_gives_install_command(self):
    from playwright.async_api import Error

    chromium = SimpleNamespace(launch=AsyncMock(side_effect=Error("unavailable")))
    with (
      patch.object(browsers, "installed_browsers", return_value=[]),
      self.assertRaisesRegex(browsers.StopRun, "playwright install chromium"),
    ):
      await browsers.launch_browser(chromium, headless=True)
