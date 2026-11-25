"""Find installed browsers and launch them through Playwright."""

import asyncio
import logging
import os
import shutil
import signal
import sys
from contextlib import suppress
from pathlib import Path

from .core import StopRun
from .startup import console_python

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


async def download_chromium(*, headless):
  LOG.info("No supported browser detected. Downloading Playwright Chromium…")
  python, options = console_python()
  variant = "--only-shell" if headless else "--no-shell"
  env = os.environ.copy()
  env.pop("ACADEMY_PASSWORD", None)
  # Playwright uses INIT_CWD to resolve a relative browser cache path.
  env.setdefault("INIT_CWD", str(Path.cwd()))
  if os.name != "nt":
    options["start_new_session"] = True
  try:
    process = await asyncio.create_subprocess_exec(
      python,
      "-m",
      "playwright",
      "install",
      "chromium",
      variant,
      cwd=Path(__file__).resolve().parent,
      env=env,
      stdout=asyncio.subprocess.DEVNULL,
      stderr=asyncio.subprocess.DEVNULL,
      **options,
    )
    try:
      if await process.wait() == 0:
        return
    except asyncio.CancelledError:
      with suppress(ProcessLookupError):
        if os.name == "nt":
          process.terminate()
        else:
          os.killpg(process.pid, signal.SIGTERM)
      await process.wait()
      raise
  except OSError:
    pass
  raise StopRun(
    "Couldn't download Playwright Chromium. Check your connection or pick an installed browser."
  )


async def launch_browser(chromium, *, headless, executable=None):
  from playwright.async_api import Error

  if executable:
    LOG.info("Starting the browser from --browser-executable.")
    return await chromium.launch(headless=headless, executable_path=executable)

  detected = False
  for name, path in installed_browsers():
    detected = True
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
  except Error as error:
    if detected or "Executable doesn't exist at " not in str(error):
      raise StopRun("Couldn't start a browser. Pick another one with --browser-executable.") from None

  await download_chromium(headless=headless)
  try:
    return await chromium.launch(headless=headless)
  except Error:
    raise StopRun("Downloaded Playwright Chromium but couldn't start it.") from None
