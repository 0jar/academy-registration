"""Install this app's Python requirements on first launch."""

import re
import site
import subprocess
import sys
from importlib import invalidate_caches, metadata, resources
from pathlib import Path

from .core import StopRun

REQUIREMENT = re.compile(r'([\w-]+)(?:==(\d+(?:\.\d+)*)|>=(\d+))?(?:; sys_platform == "([^"]+)")?')
PIP_STAGES = (
  (("Collecting ", "Obtaining ", "Getting requirements "), "Finding packages..."),
  (("Downloading ",), "Downloading packages..."),
  (("Using cached ",), "Reading cached packages..."),
  (("Preparing metadata", "Building wheel"), "Preparing packages..."),
  (("Installing build dependencies", "Installing collected packages"), "Installing packages..."),
)


def missing_requirements(contents):
  missing = []
  for line in contents.splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
      continue
    match = REQUIREMENT.fullmatch(line)
    if match is None:
      raise StopRun("Couldn't read the bundled requirements. Get a fresh copy of the app.")
    name, exact, minimum, platform = match.groups()
    if platform and platform != sys.platform:
      continue
    try:
      installed = metadata.version(name)
    except metadata.PackageNotFoundError:
      missing.append(name)
      continue
    if exact and installed != exact:
      missing.append(name)
    if minimum and (not installed.isdecimal() or int(installed) < int(minimum)):
      missing.append(name)
  return missing


def install_with_progress(command, on_progress, **options):
  options.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
  command = [*command, "--progress-bar", "off"]
  with subprocess.Popen(command, text=True, errors="replace", **options) as pip:
    current = None
    for line in pip.stdout:
      line = line.lstrip()
      for prefixes, stage in PIP_STAGES:
        if line.startswith(prefixes):
          if stage != current:
            current = stage
            on_progress(stage)
          break
    if code := pip.wait():
      raise subprocess.CalledProcessError(code, command)


def console_python():
  python = Path(sys.executable)
  if sys.platform == "win32" and python.name.lower() == "pythonw.exe":
    return str(python.with_name("python.exe")), {"creationflags": subprocess.CREATE_NO_WINDOW}
  return str(python), {}


def ensure_requirements(on_install, *, on_progress=None):
  bundled = resources.files(__package__).joinpath("requirements.txt")
  contents = bundled.read_text(encoding="utf-8")
  missing = missing_requirements(contents)
  if not missing:
    return
  on_install("Installing required Python packages. This may take a moment.")
  python, options = console_python()
  if options:
    options.update(stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
  try:
    with resources.as_file(bundled) as requirements:
      command = [python, "-m", "pip", "install", "--no-input", "-r", str(requirements)]
      options["cwd"] = requirements.parent
      if on_progress is None:
        subprocess.run(command, check=True, **options)
      else:
        install_with_progress(command, on_progress, **options)
  except (OSError, subprocess.CalledProcessError):
    raise StopRun(
      "Couldn't install the required Python packages. Check your connection and pip setup, "
      "or launch the app from a virtual environment."
    ) from None

  invalidate_caches()
  user_site = site.getusersitepackages() if site.ENABLE_USER_SITE else None
  added_user_site = user_site and user_site not in sys.path and Path(user_site).is_dir()
  if added_user_site:
    site.addsitedir(user_site)
  if still_missing := missing_requirements(contents):
    raise StopRun("Python still can't find these required packages: " + ", ".join(still_missing))
  if "pywin32" in missing:
    installed_at = str(metadata.distribution("pywin32").locate_file(""))
    if not added_user_site or installed_at != user_site:
      site.addsitedir(installed_at)
