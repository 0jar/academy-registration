"""Save run files and print the results."""

import csv
import io
import json
import logging
import os
import re
import stat
import tempfile
import time
from datetime import datetime
from importlib import resources
from pathlib import Path
from uuid import uuid4

from .core import StopRun, allowed, choose, clashes, now

LOG = logging.getLogger("academy")

if os.name == "nt":
  from . import windows_files
else:
  import fcntl


def create_config(path, config=None):
  """Save a private settings file if the path is free."""
  path = Path(path)
  if path.suffix.lower() not in {".json", ".txt"}:
    raise StopRun("Pick a filename ending in .txt or .json.")
  if config is None:
    name = "preferences.example" + path.suffix.lower()
    content = resources.files("academy_registration").joinpath(name).read_bytes()
  elif path.suffix.lower() == ".json":
    content = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
  else:
    from .preferences import dump_text

    content = dump_text(config).encode("utf-8")
  try:
    if os.name == "nt":
      windows_files.create_private_file(path, content)
      return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  except FileExistsError:
    raise StopRun("That file already exists. Edit it or pick another with --config.") from None
  with os.fdopen(descriptor, "wb") as handle:
    handle.write(content)


def private_directory(folder):
  """Keep reports and run state private to the current user."""
  if os.name == "nt":
    return windows_files.private_directory(folder)
  folder = Path(folder)
  folder.mkdir(parents=True, exist_ok=True, mode=0o700)
  if folder.is_symlink() or folder.stat().st_uid != os.getuid():
    raise StopRun("Pick a private folder you own. Use the folder's path directly.")
  folder.chmod(0o700)
  return folder


def atomic_write(path, content):
  """Write a private replacement before moving it into place."""
  path = Path(path)
  private_directory(path.parent)
  data = content.encode("utf-8") if isinstance(content, str) else content
  if os.name == "nt":
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    windows_files.create_private_file(temporary, data)
  else:
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
      with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    except BaseException:
      temporary.unlink(missing_ok=True)
      raise
  try:
    replace_file(temporary, path)
  finally:
    temporary.unlink(missing_ok=True)


def replace_file(temporary, path):
  if os.name == "nt":
    windows_files.replace_file(temporary, path)
  else:
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(directory)
    finally:
      os.close(directory)


def write_json(path, data):
  atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_state(path):
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = [data["pending"]] if "pending" in data else []
    blocked = data.get("blocked", [])
    if not isinstance(data, dict) or not isinstance(blocked, list):
      raise ValueError
    if any(
      not isinstance(key, str) or key.count("/") != 1 or not all(key.split("/"))
      for key in keys + blocked
    ):
      raise ValueError
    return data
  except (OSError, ValueError, TypeError, AttributeError):
    raise StopRun(
      "Couldn't read the run journal. Check it and the registered list on the portal."
    ) from None


def open_account_lock(path):
  if os.name == "nt":
    return windows_files.open_account_lock(path)
  descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
  lock = os.fdopen(descriptor, "a")
  try:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
      raise StopRun("The account lock needs to be a regular file you own.")
    os.fchmod(descriptor, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  except BaseException:
    lock.close()
    raise
  return lock


class Journal:
  """Keep only one run per account and remember which submission is pending."""

  def __init__(self, folder, username, opening):
    folder = private_directory(folder)
    label = re.sub(r"[^A-Za-z0-9_-]", "_", username)
    if os.name == "nt" and re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", label, re.I):
      label = "account-" + label
    self.lock = None
    try:
      self.lock = open_account_lock(folder / (label + ".lock"))
      self.path = folder / (label + "-" + datetime.fromisoformat(opening).strftime("%Y%m%d") + ".json")
      for prior in folder.glob(label + "-*.json"):
        if prior != self.path and read_state(prior).get("pending"):
          raise StopRun(
            "There's a pending submission in "
            + prior.name
            + ". Check the portal before continuing. Changing the opening date won't clear it."
          )
      self.data = read_state(self.path) if self.path.exists() else {}
    except BlockingIOError:
      self.close()
      raise StopRun("Another run is already using this account.") from None
    except Exception:
      self.close()
      raise

  def save(self):
    write_json(self.path, self.data)

  def close(self):
    if self.lock is not None:
      self.lock.close()
      self.lock = None

  def __enter__(self):
    return self

  def __exit__(self, *error):
    self.close()


def plain_text(text):
  return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", str(text))


class PlainFormatter(logging.Formatter):
  """Keep logs in UTC and strip control characters from the portal text."""

  converter = time.gmtime

  def format(self, record):
    return plain_text(super().format(record))


def console(text):
  """Print text that works in files and screen readers."""
  print(plain_text(text))


def csv_cell(value):
  if isinstance(value, str) and (
    value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
  ):
    return "'" + value
  return value


def print_plan(rows):
  if not rows:
    console("No courses to add right now. See the status below for details.")
  for row in rows:
    slots = sorted({(m.day, m.first, m.last) for m in row.meetings})
    console(row.key + "  " + "; ".join(f"Thứ {d}, tiết {first}–{last}" for d, first, last in slots))


def plan_details(courses, registered, config, choices, blocked=()):
  by_key = {c.key: c for c in courses}
  confirmed = {code + "/" + group for code, group in registered}
  protected = confirmed | {c.key for c in courses if c.checked}
  planned = {c.key for c in choices}
  details = []
  for spec in config["courses"]:
    fixed = sorted(key for key in protected if key.split("/")[0] == spec["code"])
    if fixed:
      details.append(
        {
          "code": spec["code"],
          "state": "registered" if any(k in confirmed for k in fixed) else "pre-checked, unverified",
          "groups": fixed,
        }
      )
      continue
    groups = []
    for option in spec["groups"]:
      key = spec["code"] + "/" + option["group"]
      row = by_key.get(key)
      blockers = [
        other for other in sorted(protected) if row and other in by_key and clashes(row, by_key[other])
      ]
      if row is None:
        reason = "missing from this course list"
      elif not allowed(row, option):
        reason = "the timetable changed"
      elif blockers:
        reason = "overlaps with an existing selection: " + ", ".join(blockers)
      elif key in blocked:
        reason = "The portal said there was no seat on the last try"
      elif row.remaining <= 0:
        reason = "no seats left"
      elif not row.enabled:
        reason = "The portal disabled this checkbox"
      elif key in planned:
        reason = "planned"
      else:
        reason = "another group fit better or this one clashes with the plan"
      groups.append({"key": key, "reason": reason})
    details.append(
      {
        "code": spec["code"],
        "state": "planned" if any(g["key"] in planned for g in groups) else "not selected",
        "groups": groups,
      }
    )
  return details


def print_status(courses, registered, raw, config, choices, blocked=()):
  console(
    "Registered: "
    + str(len(registered))
    + " courses. Credits: "
    + str(raw.get("totalCredits") if raw.get("totalCredits") is not None else "not reported")
  )
  console("Registration window: " + raw.get("registrationWindow", "unknown"))
  if raw.get("advisor"):
    console("Advisor: " + raw["advisor"])
  for detail in plan_details(courses, registered, config, choices, blocked):
    console(detail["code"] + ": " + detail["state"])
    for group in detail["groups"]:
      if isinstance(group, dict):
        console("  " + group["key"] + ": " + group["reason"])


def print_catalog(raw):
  registered = {(r["code"], r["group"]) for r in raw["registered"]}
  for row in raw["open"]:
    state = (
      "registered"
      if (row["code"], row["group"]) in registered
      else "pre-checked"
      if row["checked"]
      else "available"
      if row["enabled"]
      else "disabled"
    )
    console(
      row["code"]
      + "/"
      + row["group"]
      + "  "
      + row.get("name", "")
      + "  seats="
      + row["remaining"]
      + "  "
      + state
    )
    console("  " + row["schedule"])


def save_report(folder, courses, registered, raw, config, blocked=(), catalog=False):
  folder = private_directory(folder)
  choices = [] if catalog else choose(courses, registered, config, set(blocked))
  report = {
    "time_utc": now().isoformat(),
    "username": config["username"],
    "semester": raw["semester"],
    "registration_window": raw.get("registrationWindow", "unknown"),
    "advisor": raw.get("advisor", ""),
    "total_courses": raw.get("totalCourses"),
    "total_credits": raw.get("totalCredits"),
    "registered": [list(key) for key in registered],
    "catalog": raw["open"],
    "registered_rows": raw["registered"],
    "plan": plan_details(courses, registered, config, choices, blocked),
    "changes": {},
  }
  previous = folder / "report.json"
  if previous.exists():
    try:
      old = json.loads(previous.read_text(encoding="utf-8"))
      if old.get("username") == config["username"] and old.get("semester") == raw["semester"]:
        before, after = {tuple(key) for key in old["registered"]}, set(registered)
        report["changes"] = {"removed": sorted(before - after), "added": sorted(after - before)}
        for code, group in sorted(before - after):
          LOG.warning(
            "%s/%s disappeared from the registered list. Check the portal if you still want it.",
            code,
            group,
          )
    except (OSError, ValueError, KeyError, TypeError):
      LOG.warning("Couldn't compare the old report with this run. Saving the current results.")
  write_json(previous, report)
  by_key = {(r["code"], r["group"]): r for r in raw["open"]}
  confirmed = set(registered)
  output = io.StringIO(newline="")
  fields = ["code", "group", "name", "credits", "remaining", "checked", "registered", "schedule"]
  writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
  writer.writeheader()
  for row in raw["open"]:
    values = {**row, "registered": (row["code"], row["group"]) in confirmed}
    writer.writerow({key: csv_cell(values.get(key, "")) for key in fields})
  atomic_write(folder / "courses.csv", "\ufeff" + output.getvalue())
  lines = [raw["semester"], "Registered classes by day and period"]
  for code, group in registered:
    row = by_key.get((code, group), {})
    lines += [
      code + "/" + group + " — " + row.get("name", ""),
      row.get("schedule") or "This course list doesn't show the timetable.",
    ]
  lines += ["", "Advisor: " + raw.get("advisor", "not reported")]
  atomic_write(folder / "timetable.txt", "\n".join(lines) + "\n")
