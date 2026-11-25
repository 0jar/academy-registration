"""Find a timetable across all groups with seats in the current portal list."""

from dataclasses import dataclass
from datetime import timedelta
from functools import cache

from .core import Course, StopRun, parse_schedule

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
SEARCH_LIMIT = 100_000


@dataclass(frozen=True)
class Schedule:
  existing: tuple[Course, ...]
  additions: tuple[Course, ...]
  days: int
  skipped_sessions: int
  long_stretches: int
  longest_stretch: int
  complete: bool
  unreadable: tuple[str, ...]


def calendar(course):
  """Encode each teaching date as a bit mask of occupied periods."""
  dates = {}
  for meeting in course.meetings:
    start = meeting.since + timedelta(days=(meeting.day - 2 - meeting.since.weekday()) % 7)
    mask = ((1 << (meeting.last - meeting.first + 1)) - 1) << (meeting.first - 1)
    for day in range(start.toordinal(), meeting.until.toordinal() + 1, 7):
      dates[day] = dates.get(day, 0) | mask
  return dates


@cache
def day_fatigue(mask):
  """Count interior empty sessions and overlapping windows of three class periods."""
  sessions = [index for index in range((mask.bit_length() + 2) // 3) if (mask >> (3 * index)) & 7]
  gaps = sessions[-1] - sessions[0] + 1 - len(sessions)
  long_stretches = (mask & (mask >> 1) & (mask >> 2)).bit_count()
  longest = 0
  while mask:
    longest += 1
    mask &= mask >> 1
  return gaps, long_stretches, longest


def metrics(dates):
  skipped = consecutive = longest = 0
  for mask in dates.values():
    gaps, runs, length = day_fatigue(mask)
    skipped += gaps
    consecutive += runs
    longest = max(longest, length)
  return len(dates), skipped, consecutive, longest


def suggest(raw, objective, *, include=(), exclude=(), limit=SEARCH_LIMIT):
  included = {code.strip().casefold() for code in include}
  excluded = {code.strip().casefold() for code in exclude}
  registered = {(row["code"], row["group"]) for row in raw["registered"]}
  fixed = registered | {(row["code"], row["group"]) for row in raw["open"] if row["checked"]}
  chosen_codes = {code for code, _ in fixed}
  if len(chosen_codes) != len(fixed):
    raise StopRun("More than one group is already selected for a subject. Check the portal first.")

  existing, unreadable, occupied, groups = [], [], {}, {}
  seen = set()
  for row in raw["open"]:
    key = (row["code"], row["group"])
    pinned = key in fixed
    code = key[0].casefold()
    if not pinned and (
      key[0] in chosen_codes
      or not row["enabled"]
      or (included and code not in included)
      or code in excluded
    ):
      continue
    label = "/".join(key)
    try:
      seats = 0 if pinned else int(row["remaining"])
      if not pinned and seats <= 0:
        continue
      course = Course(*key, parse_schedule(row["schedule"]), seats, row["enabled"], row["checked"])
      dates = calendar(course)
      if not dates:
        raise ValueError
    except (StopRun, ValueError):
      if pinned:
        raise StopRun(f"Couldn't read the timetable for existing selection {label}.") from None
      unreadable.append(label)
      continue
    if key in seen:
      raise StopRun(f"The portal shows more than one row for {label}. Check the course list.")
    seen.add(key)
    if pinned:
      if any(occupied.get(day, 0) & mask for day, mask in dates.items()):
        raise StopRun(f"Existing selections overlap with {label}. Check the portal first.")
      occupied.update({day: occupied.get(day, 0) | mask for day, mask in dates.items()})
      existing.append(course)
    else:
      groups.setdefault(key[0], []).append((course, dates))

  missing = fixed - seen
  if missing:
    labels = ", ".join("/".join(key) for key in sorted(missing))
    raise StopRun(f"The current course list is missing the timetable for {labels}.")

  # Check the most constrained subjects first to find a good plan early.
  options = [
    sorted(
      (item for item in group if not any(occupied.get(d, 0) & m for d, m in item[1].items())),
      key=lambda item: item[0].group,
    )
    for group in groups.values()
  ]
  options = sorted(
    (group for group in options if group), key=lambda group: (len(group), group[0][0].code)
  )
  picked, best = [], ()
  best_score = None
  best_metrics = metrics(occupied)
  visited = 0
  complete = True

  def search(index):
    nonlocal best_score, best, best_metrics, visited, complete
    possible = len(picked) + len(options) - index
    if best_score is not None and (
      possible < len(best)
      or (objective == "days" and possible == len(best) and len(occupied) > best_metrics[0])
    ):
      return
    if visited >= limit:
      complete = False
      return
    visited += 1
    if index == len(options):
      days, gaps, runs, longest = metrics(occupied)
      fatigue = (gaps + runs, gaps, longest)
      priority = (days, *fatigue) if objective == "days" else (*fatigue, days)
      score = (-len(picked), *priority)
      if best_score is None or score < best_score:
        best_score, best = score, tuple(picked)
        best_metrics = days, gaps, runs, longest
      return
    for course, dates in options[index]:
      if any(occupied.get(day, 0) & mask for day, mask in dates.items()):
        continue
      previous = {day: occupied.get(day, 0) for day in dates}
      occupied.update({day: previous[day] | mask for day, mask in dates.items()})
      picked.append(course)
      search(index + 1)
      picked.pop()
      for day, mask in previous.items():
        if mask:
          occupied[day] = mask
        else:
          del occupied[day]
      if not complete:
        return
    search(index + 1)

  search(0)
  return Schedule(
    tuple(sorted(existing, key=lambda course: course.key)),
    tuple(sorted(best, key=lambda course: course.key)),
    *best_metrics,
    complete,
    tuple(sorted(unreadable)),
  )


def describe_schedule(schedule, raw, objective):
  labels = {"days": "fewest days at school", "fatigue": "least fatigue"}
  names = {(row["code"], row["group"]): row.get("name", "") for row in raw["open"]}
  lines = [
    f"Suggested schedule for {labels[objective]}",
    f"New subjects: {len(schedule.additions)}. Already selected: {len(schedule.existing)}.",
    f"School days across the term: {schedule.days}.",
    f"Skipped sessions (nhảy ca học): {schedule.skipped_sessions}.",
    f"Blocks of three consecutive periods, counting overlaps: {schedule.long_stretches}.",
    f"Longest stretch: {schedule.longest_stretch} consecutive periods.",
  ]
  if not schedule.complete:
    lines.append("Search limit reached. This is the best schedule found so far.")
  if schedule.unreadable:
    lines.append("Couldn't read these groups: " + ", ".join(schedule.unreadable))

  for label, courses in (
    ("Already selected", schedule.existing),
    ("Suggested additions", schedule.additions),
  ):
    lines.extend(["", label])
    if not courses:
      lines.append("None")
    for course in courses:
      lines.append(f"{course.key} {names.get((course.code, course.group), '')}".rstrip())
      for meeting in course.meetings:
        lines.append(
          f"  {WEEKDAYS[meeting.day - 2]} {meeting.first}–{meeting.last} "
          f"({meeting.since:%d/%m/%Y} to {meeting.until:%d/%m/%Y})"
        )
  return "\n".join(lines) + "\n"
