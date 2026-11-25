"""Read text settings and ask the setup questions."""

import re
from datetime import datetime, timedelta
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


def parse_text(text, *, allow_empty_courses=False):
  config = {"refresh_seconds": 15, "window_minutes": 20, "courses": []}
  seen, course = set(), None
  for number, line in enumerate(text.splitlines(), 1):
    line = line.strip()
    if not line or line.startswith("#"):
      continue
    label, separator, value = line.partition(":")
    label, value = label.strip().lower(), value.strip()
    try:
      if not separator or not value:
        raise StopRun("Put the value after the label and colon. Blank lines and # comments are fine.")
      if label in FIELDS:
        if label in seen:
          raise StopRun("This setting is listed twice.")
        seen.add(label)
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
          "Unknown label. Use Student, Semester, Opens, Refresh, Window, Course, Group, Fallback, or Last resort."
        )
    except StopRun as error:
      raise StopRun(f"Preferences line {number}: {error}") from None
  return validate_config(config, allow_empty_courses=allow_empty_courses)


def dump_text(config, *, allow_empty_courses=False):
  config = validate_config(config, allow_empty_courses=allow_empty_courses)
  opening = datetime.fromisoformat(config["opening"])
  readable = (
    opening.strftime("%Y-%m-%d %H:%M Hanoi")
    if (opening.utcoffset() == timedelta(hours=7) and not opening.second and not opening.microsecond)
    else opening.isoformat()
  )
  lines = [
    "# Put your groups in order of preference. Leave your password out of this file.",
    f"Student: {config['username']}",
    f"Semester: {config['semester']}",
    f"Opens: {readable}",
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
    day = ask(
      "What date does registration open? (YYYY-MM-DD)",
      check=lambda value: datetime.strptime(value, "%Y-%m-%d"),
    )
    hour = ask(
      "What time does it open in Hanoi? (HH:MM)",
      "08:00",
      lambda value: datetime.strptime(value, "%H:%M"),
    )
    day = datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d")
    hour = datetime.strptime(hour, "%H:%M").strftime("%H:%M")
    config.update(opening=f"{day}T{hour}+07:00", refresh_seconds=15, window_minutes=20, courses=[])
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
