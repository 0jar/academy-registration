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
    with (
      patch.object(browsers, "installed_browsers", return_value=[("Chrome", chrome), ("Brave", brave)]),
      patch.object(browsers, "download_chromium") as download,
    ):
      self.assertIs(await browsers.launch_browser(chromium, headless=True), bundled)
    download.assert_not_awaited()
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
      patch.object(browsers, "download_chromium") as download,
      self.assertRaises(Error),
    ):
      await browsers.launch_browser(chromium, headless=False, executable="/custom/browser")
    detect.assert_not_called()
    download.assert_not_awaited()
    chromium.launch.assert_awaited_once_with(headless=False, executable_path="/custom/browser")

  async def test_missing_bundled_browser_downloads_only_when_detection_is_empty(self):
    from playwright.async_api import Error

    missing = Error("BrowserType.launch: Executable doesn't exist at /cache/chromium")
    bundled = object()
    chromium = SimpleNamespace(launch=AsyncMock(side_effect=[missing, bundled]))
    with (
      patch.object(browsers, "installed_browsers", return_value=[]),
      patch.object(browsers, "download_chromium") as download,
    ):
      self.assertIs(await browsers.launch_browser(chromium, headless=True), bundled)
    download.assert_awaited_once_with(headless=True)
    self.assertEqual(chromium.launch.await_args_list, [call(headless=True), call(headless=True)])

  async def test_cached_browser_skips_download(self):
    bundled = object()
    chromium = SimpleNamespace(launch=AsyncMock(return_value=bundled))
    with (
      patch.object(browsers, "installed_browsers", return_value=[]),
      patch.object(browsers, "download_chromium") as download,
    ):
      self.assertIs(await browsers.launch_browser(chromium, headless=False), bundled)
    download.assert_not_awaited()

  async def test_launch_failure_does_not_download_when_browser_was_detected_or_cache_exists(self):
    from playwright.async_api import Error

    scenarios = (
      ([("Chrome", Path("chrome"))], "Executable doesn't exist at /cache/chromium"),
      ([], "Chromium closed during launch"),
    )
    for installed, reason in scenarios:
      chromium = SimpleNamespace(launch=AsyncMock(side_effect=Error(reason)))
      with (
        self.subTest(reason=reason),
        patch.object(browsers, "installed_browsers", return_value=installed),
        patch.object(browsers, "download_chromium") as download,
        self.assertRaisesRegex(browsers.StopRun, "Couldn't start a browser"),
      ):
        await browsers.launch_browser(chromium, headless=False)
      download.assert_not_awaited()

  async def test_downloader_requests_only_the_browser_variant_needed(self):
    process = SimpleNamespace(wait=AsyncMock(return_value=0))
    for headless, variant in ((True, "--only-shell"), (False, "--no-shell")):
      with (
        self.subTest(headless=headless),
        patch.dict(os.environ, {"ACADEMY_PASSWORD": "fixture"}),
        patch.object(browsers.asyncio, "create_subprocess_exec", return_value=process) as launch,
      ):
        await browsers.download_chromium(headless=headless)
      self.assertEqual(launch.call_args.args[1:], ("-m", "playwright", "install", "chromium", variant))
      self.assertNotIn("ACADEMY_PASSWORD", launch.call_args.kwargs["env"])
