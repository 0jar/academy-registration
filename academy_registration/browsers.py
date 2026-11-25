"""Find installed browsers and launch them through Playwright."""

import logging
import os
import shutil
import sys
from pathlib import Path

from .core import StopRun

LOG = logging.getLogger("academy")

WINDOWS_BROWSERS = (
  ("Chrome", "Google/Chrome/Application/chrome.exe"),
  ("Edge", "Microsoft/Edge/Application/msedge.exe"),
  ("Brave", "BraveSoftware/Brave-Browser/Application/brave.exe"),
)
LINUX_BROWSERS = (
  ("Chrome", ("google-chrome", "google-chrome-stable")),
  ("Ungoogled Chromium", ("ungoogled-chromium",)),
  ("Chromium", ("chromium", "chromium-browser")),
  ("Brave", ("brave-browser", "brave-browser-stable", "brave")),
)


def windows_app_paths(executable):
  import winreg

  key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
  for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
      try:
        with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as entry:
          value, kind = winreg.QueryValueEx(entry, "")
      except OSError:
        continue
      if isinstance(value, str) and kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        path = Path(os.path.expandvars(value.strip().strip('"')))
        if path.is_absolute() and path.name.lower() == executable.lower():
          yield path


def candidates(platform):
  if platform == "win32":
    for name, relative in WINDOWS_BROWSERS:
      yield from ((name, path) for path in windows_app_paths(Path(relative).name))
      for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(variable)
        if base and Path(base).is_absolute():
          yield name, Path(base) / relative
  elif platform.startswith("linux"):
    for name, commands in LINUX_BROWSERS:
      for command in commands:
        executable = shutil.which(command)
        if executable:
          yield name, Path(executable).absolute()
  elif platform == "darwin":
    for directory in (Path("/Applications"), Path.home() / "Applications"):
      yield "Chrome", directory / "Google Chrome.app/Contents/MacOS/Google Chrome"


def installed_browsers():
  seen = set()
  for name, path in candidates(sys.platform):
    if path not in seen and path.is_file() and os.access(path, os.X_OK):
      seen.add(path)
      yield name, path


async def launch_browser(chromium, *, headless, executable=None):
  from playwright.async_api import Error

  if executable:
    LOG.info("Starting the browser from --browser-executable.")
    return await chromium.launch(headless=headless, executable_path=executable)

  for name, path in installed_browsers():
    try:
      browser = await chromium.launch(headless=headless, executable_path=path)
    except Error:
      LOG.warning("Couldn't start %s. Trying the next browser.", name)
    else:
      LOG.info("Using %s.", name)
      return browser

  LOG.info("Using Playwright Chromium.")
  try:
    return await chromium.launch(headless=headless)
  except Error:
    raise StopRun(
      "Couldn't start a browser. Run python -m playwright install chromium or pass --browser-executable."
    ) from None
