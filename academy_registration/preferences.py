"""Read text settings and ask the setup questions."""

import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from .core import StopRun, identifier, option_slots, validate_config, validate_group
from .files import console, create_config

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
DAY_NUMBERS = {
  alias: day
  for day, name in enumerate(DAYS, 2)
  for alias in (name.lower(), name[:3].lower(), str(day), f"thứ {day}")
}
FIELDS = {
  "student": "username",
  "semester": "semester",
  "opens": "opening",
  "refresh": "refresh_seconds",
  "window": "window_minutes",
}
HANOI_OFFSET = timezone(timedelta(hours=7))


def parse_slots(text):
  slots = []
  for part in text.split(","):
    match = re.fullmatch(r"\s*(.+?)\s+(\d+)\s*[-–→]\s*(\d+)\s*", part)
    if not match or match[1].lower() not in DAY_NUMBERS:
      raise StopRun("Enter a day and its periods, like Mon 7-9, Tue 7-9.")
    slots.append({"day": DAY_NUMBERS[match[1].lower()], "periods": [int(match[2]), int(match[3])]})
  return slots


def parse_opening(text):
  text = re.sub(r"\s+(Hanoi|Vietnam)$", "+07:00", text, flags=re.I)
  try:
    return datetime.fromisoformat(text).isoformat()
  except ValueError:
    raise StopRun(
      "Enter the opening time like 2027-01-01 08:00 Hanoi. An ISO time with an offset works too."
    ) from None


def parse_date(value):
  try:
    if re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
      return datetime.strptime(value, "%d/%m/%Y").date()
  except ValueError:
    pass
  raise StopRun("Enter a valid date in DD/MM/YYYY format, like 05/01/2027.")


def parse_time(value):
  try:
    if re.match(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
      clock = time.fromisoformat(value)
      return clock if clock.tzinfo is not None else clock.replace(tzinfo=HANOI_OFFSET)
  except ValueError:
    pass
  raise StopRun("Enter the time in 24-hour format, like 08:00 (Hanoi time).")


def opening_fields(value):
  opening = datetime.fromisoformat(value)
  date = f"{opening.day:02d}/{opening.month:02d}/{opening.year:04d}"
  clock = opening.timetz()
  if clock.utcoffset() == HANOI_OFFSET.utcoffset(None):
    clock = clock.replace(tzinfo=None)
  precision = "auto" if clock.microsecond else "seconds" if clock.second else "minutes"
  return date, clock.isoformat(timespec=precision)


def parse_text(text, *, allow_empty_courses=False):
  config = {"refresh_seconds": 15, "window_minutes": 20, "courses": []}
  opening_parts = {}
  seen, course = set(), None
  for number, line in enumerate(text.splitlines(), 1):
    line = line.strip()
    if not line or line.startswith("#"):
      continue
    label, separator, value = line.partition(":")
    label, value = label.strip().lower(), value.strip()
    try:
      if not separator or (not value and label not in {"date", "time"}):
        raise StopRun("Put the value after the label and colon. Blank lines and # comments are fine.")
      if label in FIELDS or label in {"date", "time"}:
        if label in seen:
          raise StopRun("This setting is listed twice.")
        seen.add(label)
        if "opens" in seen and ({"date", "time"} & seen):
          raise StopRun("Use either Date and Time or the older Opens field.")
        if label in {"date", "time"}:
          opening_parts[label] = (parse_date if label == "date" else parse_time)(value)
          continue
        if label in {"refresh", "window"}:
          unit = "seconds?" if label == "refresh" else "minutes?"
          match = re.fullmatch(r"(\d+)(?:\s+" + unit + r")?", value, re.I)
          if not match:
            raise StopRun("Use whole seconds for Refresh and whole minutes for Window.")
          value = int(match[1])
        config[FIELDS[label]] = parse_opening(value) if label == "opens" else value
      elif label == "course":
        code, _, name = value.partition("|")
        course = {"code": code.strip(), "groups": []}
        if name.strip():
          course["name"] = name.strip()
        config["courses"].append(course)
      elif label in {"group", "fallback", "last resort"}:
        if course is None:
          raise StopRun("Add a Course line before listing its groups.")
        group, separator, schedule = value.partition("|")
        if not separator:
          raise StopRun("Use Group: 2 | Friday 10-12.")
        option = {"group": group.strip(), "slots": parse_slots(schedule)}
        if label == "last resort":
          option["last_resort"] = True
        validate_group(option)
        course["groups"].append(option)
      else:
        raise StopRun(
          "Unknown label. Use Student, Semester, Date, Time, Refresh, Window, Course, Group, Fallback, or Last resort."
        )
    except StopRun as error:
      raise StopRun(f"Preferences line {number}: {error}") from None
  if opening_parts:
    if len(opening_parts) != 2:
      raise StopRun("Enter both Date and Time for registration to open.")
    config["opening"] = datetime.combine(opening_parts["date"], opening_parts["time"]).isoformat()
  return validate_config(config, allow_empty_courses=allow_empty_courses)


def dump_text(config, *, allow_empty_courses=False):
  config = validate_config(config, allow_empty_courses=allow_empty_courses)
  date, clock = opening_fields(config["opening"])
  lines = [
    "# Put your groups in order of preference. Leave your password out of this file.",
    f"Student: {config['username']}",
    f"Semester: {config['semester']}",
    f"Date: {date}",
    f"Time: {clock}",
    f"Refresh: {config['refresh_seconds']} seconds",
    f"Window: {config['window_minutes']} minutes",
  ]
  if config["courses"]:
    lines.extend(["", *course_lines(config["courses"])])
  result = "\n".join(lines) + "\n"
  if parse_text(result, allow_empty_courses=allow_empty_courses) != text_shape(config):
    raise StopRun("One of these settings doesn't fit the text format. Save this plan as JSON.")
  return result


def course_lines(courses):
  lines = []
  for course in courses:
    if lines:
      lines.append("")
    name = " | " + course["name"] if course.get("name") else ""
    lines.append("Course: " + course["code"] + name)
    for index, group in enumerate(course["groups"]):
      label = "Last resort" if group.get("last_resort") else "Group" if index == 0 else "Fallback"
      schedule = ", ".join(
        f"{DAYS[day - 2]} {first}-{last}" for day, first, last in sorted(option_slots(group))
      )
      lines.append(f"{label}: {group['group']} | {schedule}")
  return lines


def text_shape(config):
  """Put the settings in the same shape the text reader returns."""
  result = {key: config[key] for key in (*FIELDS.values(), "courses")}
  result["opening"] = datetime.fromisoformat(config["opening"]).isoformat()
  result["courses"] = [
    {
      **{key: value for key, value in course.items() if key != "name" or value},
      "groups": [
        {
          "group": group["group"],
          "slots": [
            {"day": day, "periods": [first, last]} for day, first, last in sorted(option_slots(group))
          ],
          **({"last_resort": True} if group.get("last_resort") else {}),
        }
        for group in course["groups"]
      ],
    }
    for course in config["courses"]
  ]
  return result


def ask(prompt, default=None, check=lambda value: bool(value.strip())):
  while True:
    value = input(prompt + (f" [{default}]" if default is not None else "") + ": ").strip()
    value = value or default
    try:
      if value is not None and check(value):
        return value
      console("That doesn't look right. Try again.")
    except (StopRun, ValueError):
      console("Check the format and try again.")


def yes(prompt):
  return ask(
    prompt + " (y/n)", "n", lambda value: value.lower() in {"y", "yes", "n", "no"}
  ).lower() in {"y", "yes"}


def configure(path):
  """Ask for a plan, show it, and save it if the user says yes."""
  path = Path(path)
  if path.suffix.lower() not in {".txt", ".json"}:
    raise StopRun("Pick a filename ending in .txt or .json.")
  if path.exists() or path.is_symlink():
    raise StopRun("That file already exists. Edit it or pick another file with --config.")
  console("Let's set up your course plan. This step just saves your settings.")
  console("Use Hanoi time for opening and class periods for the schedule.")
  try:
    config = {
      "username": ask("Student ID", check=identifier),
      "semester": ask("Semester (copy it from the portal)"),
    }
    day = parse_date(ask("What date does registration open? (DD/MM/YYYY)", check=parse_date))
    hour = parse_time(ask("What time does registration open? (HH:MM, Hanoi time)", "08:00", parse_time))
    config.update(
      opening=datetime.combine(day, hour).isoformat(), refresh_seconds=15, window_minutes=20, courses=[]
    )
    codes = set()
    while True:
      code = ask(
        "Course code (Enter to finish)",
        "",
        lambda value: not value or (identifier(value) and value not in codes),
      )
      if not code:
        if config["courses"]:
          break
        console("Add at least one course first.")
        continue
      course = {"code": code, "groups": []}
      groups = set()
      while True:
        first = not course["groups"]
        group = ask(
          "Which group do you want?"
          if first
          else "Fallback group (Enter if you're done with this course)",
          "1" if first else "",
          lambda value: (not value and not first) or (identifier(value) and value not in groups),
        )
        if not group:
          break

        def valid_schedule(value):
          validate_group({"group": group, "slots": parse_slots(value)})
          return True

        schedule = ask("Schedule (example: Mon 7-9, Tue 7-9)", check=valid_schedule)
        option = {"group": group, "slots": parse_slots(schedule)}
        if not first and yes("Is this fallback a last resort?"):
          option["last_resort"] = True
        course["groups"].append(option)
        groups.add(group)
      config["courses"].append(course)
      codes.add(code)
    console("\nHere's your plan.\n" + dump_text(config))
    if not yes(f"Save to {path}?"):
      console("Cancelled. Nothing saved.")
      return
    create_config(path, config)
    console("Saved. Use --check-config to check the file or run without --submit to preview your plan.")
  except EOFError:
    raise StopRun("Setup ended before you saved. Run --configure in a terminal to try again.") from None
