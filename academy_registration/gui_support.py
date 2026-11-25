"""Run Playwright off the UI thread and prepare rows for the timetable."""

import asyncio
import logging
import re
from datetime import date
from threading import Lock, Thread

from .cli import run_browser
from .core import StopRun
from .files import plain_text
from .suggestions import calendar


def subject_codes(text):
  return [code for code in re.split(r"[\s,]+", text.strip()) if code]


def week_grid(courses, names):
  """Map each Monday to rows of three-period sessions."""
  weeks = {}
  for course in courses:
    name = names.get((course.code, course.group))
    label = f"{name} ({course.code})" if name else course.code
    for ordinal, mask in calendar(course).items():
      weekday = date.fromordinal(ordinal).weekday()
      sessions = weeks.setdefault(date.fromordinal(ordinal - weekday), {})
      for offset in range(0, mask.bit_length(), 3):
        if (mask >> offset) & 7:
          cells = sessions.setdefault(offset // 3 + 1, [[] for _ in range(6)])
          if label not in cells[weekday]:
            cells[weekday].append(label)
  return dict(sorted(weeks.items()))


def proposal_preferences(courses, raw):
  """Turn suggested additions into exact groups for the user to review."""
  names = {(row["code"], row["group"]): row.get("name", "") for row in raw["open"]}
  return [
    {
      "code": course.code,
      "name": names.get((course.code, course.group), ""),
      "groups": [
        {
          "group": course.group,
          "slots": [
            {"day": day, "periods": [first, last]}
            for day, first, last in sorted({(m.day, m.first, m.last) for m in course.meetings})
          ],
        }
      ],
    }
    for course in courses
  ]


class EventLog(logging.Handler):
  def __init__(self, events):
    super().__init__(logging.INFO)
    self.events = events

  def emit(self, record):
    self.events.put(("status", plain_text(record.getMessage())))


class BrowserJob:
  """Own one browser event loop and cancel it at most once."""

  def __init__(self, events):
    self.events = events
    self.lock = Lock()
    self.cancel_requested = False
    self.loop = None
    self.task = None

  def start(self, args, config):
    Thread(target=self._run, args=(args, config), daemon=False).start()

  def cancel(self):
    with self.lock:
      if self.cancel_requested:
        return
      self.cancel_requested = True
      if self.loop is not None:
        self.loop.call_soon_threadsafe(self.task.cancel)

  async def _execute(self, args, config):
    with self.lock:
      if self.cancel_requested:
        raise asyncio.CancelledError
      self.loop = asyncio.get_running_loop()
      self.task = asyncio.current_task()
    try:
      await run_browser(args, config)
    finally:
      with self.lock:
        self.loop = self.task = None

  def _run(self, args, config):
    logger = logging.getLogger("academy")
    handler = EventLog(self.events)
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
      asyncio.run(self._execute(args, config))
      result = ("finished", "Registration verified." if args.submit else "Schedule ready.")
    except asyncio.CancelledError:
      text = "Cancelled."
      if args.submit:
        text += " If a submission was in progress, check the portal before retrying."
      result = ("finished", text)
    except StopRun as error:
      result = ("finished", f"Stopped. {plain_text(error)}")
    except Exception as error:
      result = ("finished", f"Stopped after {type(error).__name__}. Check the run folder for details.")
    finally:
      logger.removeHandler(handler)
      logger.setLevel(level)
      args.password = None
    self.events.put(result)
