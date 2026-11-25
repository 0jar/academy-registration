"""Check settings and plan courses offline."""

import itertools
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import cache
from pathlib import Path

LOG = logging.getLogger("academy")


class StopRun(Exception):
  """Stop here and let the user check what happened."""


class NoSeat(StopRun):
  pass


def now():
  return datetime.now(timezone.utc)


def normalize(value):
  return " ".join(str(value or "").split())


@dataclass(frozen=True, slots=True)
class Meeting:
  day: int
  first: int
  last: int
  since: date
  until: date


@dataclass(frozen=True, slots=True)
class Course:
  code: str
  group: str
  meetings: tuple[Meeting, ...]
  remaining: int = 0
  enabled: bool = False
  checked: bool = False

  @property
  def key(self):
    return self.code + "/" + self.group


SCHEDULE_BLOCK = re.compile(r"Thứ\s*(\d)\s*,?\s*tiết\s*(\d+)\s*(?:→|->|–|-)\s*(\d+)", re.I)
TEACHING_DATES = re.compile(r"(\d{2}/\d{2}/(?:\d{4}|\d{2}))\s*đến\s*(\d{2}/\d{2}/(?:\d{4}|\d{2}))")


def parse_day(value):
  try:
    return datetime.strptime(value, "%d/%m/%Y" if len(value) == 10 else "%d/%m/%y").date()
  except ValueError:
    raise StopRun("That timetable date doesn't look right.") from None


def parse_schedule(text):
  """Keep each teaching block so breaks don't count as class time."""
  starts = list(SCHEDULE_BLOCK.finditer(text))
  if (
    not starts
    or len(starts) != len(re.findall(r"Thứ\s*\d", text, re.I))
    or re.search(r"Chủ\s*nhật", text, re.I)
  ):
    raise StopRun("Couldn't read the timetable. Check the schedule on the portal.")
  meetings = []
  for i, match in enumerate(starts):
    segment = text[match.end() : starts[i + 1].start() if i + 1 < len(starts) else len(text)]
    dates = TEACHING_DATES.search(segment)
    if not dates:
      raise StopRun("The timetable is missing dates. Check the schedule on the portal.")
    day, first, last = map(int, match.groups())
    since, until = map(parse_day, dates.groups())
    if day not in range(2, 8) or first < 1 or last < first or since > until:
      raise StopRun("One of the timetable blocks doesn't look right.")
    meetings.append(Meeting(day, first, last, since, until))
  return tuple(meetings)


def clashes(left, right):
  for a, b in itertools.product(left.meetings, right.meetings):
    if a.day != b.day or a.last < b.first or b.last < a.first:
      continue
    start, end = max(a.since, b.since), min(a.until, b.until)
    first_occurrence = start + timedelta(days=(a.day - 2 - start.weekday()) % 7)
    if first_occurrence <= end:
      return True
  return False


def require(condition, message):
  if not condition:
    raise StopRun("Check your settings. " + message)


def identifier(value):
  return (
    isinstance(value, str)
    and 0 < len(value) <= 128
    and value == value.strip()
    and value.isprintable()
    and "/" not in value
  )


def validate_group(group):
  require(
    identifier(group["group"]),
    "Enter a group without slashes or control characters.",
  )
  require(type(group.get("last_resort", False)) is bool, "last_resort must be true or false.")
  if "slots" in group:
    require(
      not {"days", "periods"} & group.keys(),
      "Pick one schedule format for this group, either slots or days with periods.",
    )
    slots = group["slots"]
  else:
    days = group["days"]
    require(
      isinstance(days, list) and len(days) == len(set(days)), "days must be a list of unique weekdays."
    )
    slots = [{"day": day, "periods": group["periods"]} for day in days]
  require(isinstance(slots, list) and slots, "Add at least one class time for each group.")
  seen = set()
  for slot in slots:
    day, periods = slot["day"], slot["periods"]
    require(type(day) is int and day in range(2, 8), "Use weekdays 2 (Monday) through 7 (Saturday).")
    require(
      isinstance(periods, list)
      and len(periods) == 2
      and all(type(n) is int for n in periods)
      and 1 <= periods[0] <= periods[1],
      "Enter the first and last period in order, starting at 1 or higher.",
    )
    key = (day, *periods)
    require(key not in seen, "That class time is listed twice.")
    seen.add(key)


def validate_config(config):
  """Check the plan the same way no matter how it was entered."""
  try:
    require(isinstance(config, dict), "Put your JSON settings inside a single object.")
    require(
      "password" not in config,
      "Leave your password out of the JSON file. Use the hidden prompt or ACADEMY_PASSWORD.",
    )
    require(
      identifier(config["username"]),
      "Enter your student ID without spaces around it or any slashes or control characters.",
    )
    require(
      isinstance(config["semester"], str) and config["semester"].strip(),
      "Enter the semester shown on the portal.",
    )
    opening = datetime.fromisoformat(config["opening"])
    require(opening.tzinfo is not None, "Opening time must include its UTC offset.")
    for field, minimum in (("refresh_seconds", 15), ("window_minutes", 1)):
      require(
        type(config[field]) is int and config[field] >= minimum,
        f"{field} must be an integer of at least {minimum}.",
      )
    require(isinstance(config["courses"], list) and config["courses"], "Add at least one course.")
    codes = []
    for item in config["courses"]:
      require(identifier(item["code"]), "Enter course codes without slashes or control characters.")
      codes.append(item["code"])
      groups = item["groups"]
      require(
        isinstance(groups, list) and groups,
        "List at least one group for each course, in the order you want them.",
      )
      for group in groups:
        validate_group(group)
      names = [group["group"] for group in groups]
      require(len(names) == len(set(names)), "A course has the same group listed twice.")
    require(len(codes) == len(set(codes)), "The same course code is listed twice.")
    return config
  except (ValueError, TypeError, KeyError, AttributeError):
    raise StopRun(
      "Check your settings. Look for missing fields, invalid values, or an opening date that doesn't parse."
    ) from None


def load_config(path):
  """Read text or JSON settings and keep private values out of errors."""
  path = Path(path)
  try:
    text = path.read_text(encoding="utf-8-sig")
  except (OSError, UnicodeError):
    raise StopRun(
      "Couldn't open your settings. Run --configure or point --config at a UTF-8 file."
    ) from None
  if path.suffix.lower() == ".json":
    try:
      config = json.loads(text)
    except ValueError:
      raise StopRun(
        "Check your settings. Fix the JSON syntax or use --configure to make a text file."
      ) from None
  elif path.suffix.lower() == ".txt":
    from .preferences import parse_text

    config = parse_text(text)
  else:
    raise StopRun("Use a settings file ending in .txt or .json.")
  return validate_config(config)


def option_slots(option):
  if "slots" in option:
    return {(slot["day"], *slot["periods"]) for slot in option["slots"]}
  return {(day, *option["periods"]) for day in option["days"]}


def allowed(course, option):
  return bool(course.meetings) and {(m.day, m.first, m.last) for m in course.meetings} == option_slots(
    option
  )


def choose(courses, registered, config, blocked=frozenset()):
  """Fit as many courses as possible, then favor the groups the user wants.

  Existing selections are fixed. Prune conflicting prefixes instead of building
  every Cartesian product; cache pairwise timetable comparisons within a plan.
  """
  by_key = {course.key: course for course in courses}

  @cache
  def conflict(left, right):
    return clashes(by_key[left], by_key[right])

  existing = {}
  protected = set(registered) | {(row.code, row.group) for row in courses if row.checked}
  for code, group in sorted(protected):
    key = code + "/" + group
    if code in existing:
      raise StopRun(
        f"Multiple registered/pre-checked groups for {code}. Check the portal before continuing."
      )
    if key not in by_key or not by_key[key].meetings:
      raise StopRun("Couldn't check the timetable for " + key)
    existing[code] = by_key[key]
  conflicts = [
    f"{a.key} conflicts with {b.key}"
    for a, b in itertools.combinations(existing.values(), 2)
    if conflict(a.key, b.key)
  ]
  if conflicts:
    raise StopRun(
      "Existing registrations/pre-checked selections overlap: "
      + "; ".join(conflicts)
      + ". Check the portal before continuing."
    )
  options = []
  for spec in config["courses"]:
    if spec["code"] in existing:
      continue
    choices = []
    for rank, option in enumerate(spec["groups"]):
      row = by_key.get(spec["code"] + "/" + option["group"])
      if row is None:
        continue
      if not allowed(row, option):
        if row.enabled and row.remaining > 0:
          raise StopRun("Timetable changed for " + row.key)
        continue
      if not row.enabled or row.remaining <= 0 or row.key in blocked:
        continue
      blockers = [fixed.key for fixed in existing.values() if conflict(row.key, fixed.key)]
      if blockers:
        LOG.warning(
          "%s overlaps with %s, which is already selected.",
          row.key,
          ", ".join(blockers),
        )
      else:
        choices.append((row, rank, int(option.get("last_resort", False))))
    options.append(choices + [(None, 999, 0)])
  best_score, best_rows = None, []

  def search(index, selected, last_resorts, ranks):
    nonlocal best_score, best_rows
    possible = len(selected) + len(options) - index
    if best_score and (
      possible < -best_score[0] or (possible == -best_score[0] and last_resorts > best_score[1])
    ):
      return
    if index == len(options):
      score = (-len(selected), last_resorts, ranks)
      if best_score is None or score < best_score:
        best_score, best_rows = score, selected.copy()
      return
    for row, rank, last_resort in options[index]:
      if row and any(conflict(row.key, other.key) for other in selected):
        continue
      search(
        index + 1, selected + [row] if row else selected, last_resorts + last_resort, ranks + (rank,)
      )

  search(0, [], 0, ())
  return best_rows


def demo_courses(config):
  """Make up seats for the offline demo using the planned days and periods."""
  rows = []
  for spec in config["courses"]:
    for option in spec["groups"]:
      meetings = tuple(
        Meeting(day, first, last, date(2027, 1, 4), date(2027, 4, 2))
        for day, first, last in sorted(option_slots(option))
      )
      rows.append(Course(spec["code"], option["group"], meetings, remaining=5, enabled=True))
  return rows
