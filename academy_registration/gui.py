"""Desktop planner and registration controls."""

import os
import sys
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

try:
  import tkinter as tk
  from tkinter import filedialog, messagebox, ttk
except ImportError:
  tk = None

from .cli import hanoi_timezone
from .core import StopRun, identifier, load_config, now
from .files import atomic_write
from .gui_support import BrowserJob, proposal_preferences, subject_codes, week_grid
from .preferences import course_lines, dump_text, parse_text
from .startup import ensure_requirements
from .suggestions import WEEKDAYS

SETTINGS = (
  ("Student", "Student ID", ""),
  ("Semester", "Semester", ""),
  ("Opens", "Opening (Hanoi time)", ""),
  ("Refresh", "Refresh seconds", "15"),
  ("Window", "Window minutes", "20"),
)


def scrollable_tree(parent, columns, widths):
  frame = ttk.Frame(parent)
  tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
  bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
  tree.configure(yscrollcommand=bar.set)
  for column, width in zip(columns, widths):
    tree.heading(column, text=column)
    tree.column(column, width=width, minwidth=min(width, 44), stretch=True)
  tree.pack(side="left", fill="both", expand=True)
  bar.pack(side="right", fill="y")
  frame.pack(fill="both", expand=True)
  return tree


class App:
  def __init__(self, root):
    self.root = root
    self.events = Queue()
    self.job = None
    self.close_after_run = False
    self.weeks = {}
    self.suggestion = None
    self.settings_path = None
    self.state_dir = Path(".academy-run").resolve()
    root.title("Academy Registration")
    root.geometry("1140x740")
    root.minsize(820, 600)
    root.protocol("WM_DELETE_WINDOW", self.close)

    layout = ttk.Panedwindow(root, orient="horizontal")
    layout.pack(fill="both", expand=True, padx=12, pady=(12, 6))
    settings, results = ttk.Frame(layout, padding=8), ttk.Frame(layout, padding=8)
    layout.add(settings, weight=1)
    layout.add(results, weight=3)
    self.build_settings(settings)
    self.build_results(results)
    self.status = tk.StringVar(
      value="Enter your student ID and password to suggest a schedule, or open a settings file."
    )
    ttk.Label(root, textvariable=self.status, anchor="w").pack(fill="x", padx=16, pady=(0, 10))

    configs = [path for path in (Path("preferences.txt"), Path("preferences.json")) if path.is_file()]
    if len(configs) == 1:
      self.load(configs[0])
    root.after(80, self.drain_events)

  def build_settings(self, frame):
    bar = ttk.Frame(frame)
    bar.pack(fill="x", pady=(0, 8))
    self.open_button = ttk.Button(bar, text="Open settings…", command=self.load)
    self.open_button.pack(side="left")
    self.save_button = ttk.Button(bar, text="Save settings…", command=self.save)
    self.save_button.pack(side="left", padx=6)

    form = ttk.Frame(frame)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)
    self.values = {}
    self.inputs = []
    fields = (*SETTINGS[:1], (None, "Password", ""), *SETTINGS[1:])
    for index, (key, label, default) in enumerate(fields):
      value = tk.StringVar(value=default)
      ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=3)
      entry = ttk.Entry(form, textvariable=value, width=24)
      if key is None:
        entry.configure(show="•")
        self.password, self.password_entry = value, entry
      else:
        self.values[key] = value
      entry.grid(row=index, column=1, sticky="ew", padx=(8, 0), pady=3)
      self.inputs.append(entry)
    ttk.Label(frame, text="Example opening: 2027-01-05 08:00 Hanoi").pack(anchor="w", pady=(2, 8))

    ttk.Label(frame, text="Courses to register").pack(anchor="w")
    ttk.Label(frame, text="Course: IE.014.02\nGroup: 2 | Fri 10-12", justify="left").pack(anchor="w")
    self.courses = tk.Text(frame, height=8, width=33, undo=True, wrap="word")
    self.courses.pack(fill="both", expand=True, pady=(4, 10))
    self.inputs.append(self.courses)

    filters = ttk.LabelFrame(frame, text="Schedule filters", padding=8)
    filters.pack(fill="x")
    filters.columnconfigure(1, weight=1)
    self.objective = tk.StringVar(value="days")
    objectives = ttk.Frame(filters)
    objectives.grid(row=0, column=0, columnspan=2, sticky="w")
    for label, value in (("Fewest days", "days"), ("Least fatigue", "fatigue")):
      button = ttk.Radiobutton(objectives, text=label, variable=self.objective, value=value)
      button.pack(side="left", padx=(0, 8))
      self.inputs.append(button)
    self.include, self.exclude = tk.StringVar(), tk.StringVar()
    for index, (label, value) in enumerate(
      (("Include codes", self.include), ("Exclude codes", self.exclude)), 1
    ):
      ttk.Label(filters, text=label).grid(row=index, column=0, sticky="w", pady=3)
      entry = ttk.Entry(filters, textvariable=value)
      entry.grid(row=index, column=1, sticky="ew", padx=(8, 0))
      self.inputs.append(entry)
    ttk.Label(filters, text="Separate subject codes with spaces or commas.").grid(
      row=3, column=0, columnspan=2, sticky="w"
    )

    self.headless = tk.BooleanVar(value=False)
    hidden = ttk.Checkbutton(frame, text="Hide the school browser", variable=self.headless)
    hidden.pack(anchor="w", pady=6)
    self.inputs.append(hidden)
    actions = ttk.Frame(frame)
    actions.pack(fill="x")
    self.preview_button = ttk.Button(
      actions, text="Suggest schedule", command=lambda: self.start(False)
    )
    self.preview_button.pack(side="left")
    self.register_button = ttk.Button(
      actions, text="Schedule registration…", command=lambda: self.start(True)
    )
    self.register_button.pack(side="left", padx=6)
    self.cancel_button = ttk.Button(actions, text="Cancel run", command=self.cancel, state="disabled")
    self.cancel_button.pack(side="left")

  def build_results(self, parent):
    self.metrics = tk.StringVar(value="Suggest a schedule to see the timetable.")
    ttk.Label(parent, textvariable=self.metrics, wraplength=610, justify="left").pack(
      anchor="w", pady=(0, 6)
    )
    tabs = ttk.Notebook(parent)
    tabs.pack(fill="both", expand=True)
    timetable, courses, log = (ttk.Frame(tabs, padding=6) for _ in range(3))
    for frame, title in ((timetable, "Timetable"), (courses, "Results"), (log, "Run log")):
      tabs.add(frame, text=title)

    selector = ttk.Frame(timetable)
    selector.pack(fill="x", pady=(0, 6))
    ttk.Label(selector, text="Week of").pack(side="left")
    self.week = ttk.Combobox(selector, width=15, state="readonly")
    self.week.pack(side="left", padx=6)
    self.week.bind("<<ComboboxSelected>>", self.draw_week)
    self.grid = scrollable_tree(timetable, ("Period", *WEEKDAYS), (55, *([96] * 6)))
    self.apply_button = ttk.Button(
      courses, text="Use suggested groups in settings", state="disabled", command=self.use_suggestion
    )
    self.apply_button.pack(anchor="w", pady=(0, 6))
    self.results = scrollable_tree(courses, ("Subject / group", "Name", "Status"), (105, 320, 100))
    self.log = tk.Text(log, state="disabled", wrap="word", width=45, height=15)
    scrollbar = ttk.Scrollbar(log, command=self.log.yview)
    self.log.configure(yscrollcommand=scrollbar.set)
    self.log.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

  def config(self, *, require_courses=False):
    header = "\n".join(f"{key}: {value.get().strip()}" for key, value in self.values.items())
    return parse_text(
      header + "\n\n" + self.courses.get("1.0", "end-1c"), allow_empty_courses=not require_courses
    )

  def show_config(self, config):
    body = dump_text(config, allow_empty_courses=True).partition("\n\n")[2]
    for key, name in (
      ("Student", "username"),
      ("Semester", "semester"),
      ("Opens", "opening"),
      ("Refresh", "refresh_seconds"),
      ("Window", "window_minutes"),
    ):
      self.values[key].set(config[name])
    self.set_course_text(body)

  def set_course_text(self, body):
    self.courses.delete("1.0", "end")
    self.courses.insert("1.0", body)

  def load(self, path=None):
    path = path or filedialog.askopenfilename(
      parent=self.root, filetypes=[("Settings files", "*.txt *.json"), ("All files", "*")]
    )
    if not path:
      return
    try:
      config = load_config(path, allow_empty_courses=True)
      self.show_config(config)
    except (StopRun, OSError) as error:
      messagebox.showerror("Couldn't open settings", str(error), parent=self.root)
      return
    self.settings_path = Path(path)
    self.password.set("")
    self.status.set(f"Opened {self.settings_path.name}.")

  def save(self):
    try:
      config = self.config()
      text = dump_text(config, allow_empty_courses=True)
    except StopRun as error:
      messagebox.showerror("Check your settings", str(error), parent=self.root)
      return
    current = self.settings_path or Path("preferences.txt")
    filename = filedialog.asksaveasfilename(
      parent=self.root,
      defaultextension=".txt",
      filetypes=[("Text settings", "*.txt")],
      initialdir=str(current.parent.resolve()),
      initialfile=current.with_suffix(".txt").name,
    )
    if not filename:
      return
    path = Path(filename)
    if path.suffix.lower() != ".txt":
      messagebox.showerror("Couldn't save settings", "Choose a .txt filename.", parent=self.root)
      return
    try:
      atomic_write(path, text, private_parent=False)
    except Exception as error:
      messagebox.showerror(
        "Couldn't save settings", f"Saving failed ({type(error).__name__}).", parent=self.root
      )
      return
    self.settings_path = path
    self.status.set(f"Saved {path.name}.")

  def set_running(self, running):
    for control in (
      self.open_button,
      self.save_button,
      self.preview_button,
      self.register_button,
      *self.inputs,
    ):
      control.configure(state="disabled" if running else "normal")
    self.cancel_button.configure(state="normal" if running else "disabled")
    self.apply_button.configure(state="normal" if not running and self.suggestion else "disabled")

  def start(self, submit):
    if self.job is not None:
      return
    try:
      if submit:
        config = self.config(require_courses=True)
      else:
        student = self.values["Student"].get().strip()
        if not identifier(student):
          raise StopRun("Enter your student ID to fetch the course list.")
        config = {"username": student, "semester": self.values["Semester"].get().strip(), "courses": []}
    except StopRun as error:
      messagebox.showerror("Check your settings", str(error), parent=self.root)
      return
    password = self.password.get() or os.environ.get("ACADEMY_PASSWORD")
    if not password:
      self.status.set("Enter your school password to start a run.")
      self.password_entry.focus_set()
      return
    if submit:
      opening = datetime.fromisoformat(config["opening"])
      if now() >= opening + timedelta(minutes=config["window_minutes"]):
        messagebox.showerror(
          "Registration window ended", "Check the opening time in your settings.", parent=self.root
        )
        return
      when = opening.astimezone(hanoi_timezone()).strftime("%Y-%m-%d %H:%M:%S")
      targets = ", ".join(course["code"] for course in config["courses"])
      text = f"Register {targets} when the window opens at {when} Hanoi time?"
      if opening <= now():
        text += (
          "\n\nThe window is already open according to your settings. The tool may submit right away."
        )
      if not messagebox.askyesno(
        "Schedule registration",
        text + "\n\nKeep this window open and your computer awake.",
        parent=self.root,
      ):
        return
    self.password.set("")
    self.suggestion = None
    self.weeks = {}
    self.week.configure(values=())
    self.week.set("")
    self.draw_week()
    self.results.delete(*self.results.get_children())
    self.metrics.set("Signing in to fetch the course list…")
    self.log.configure(state="normal")
    self.log.delete("1.0", "end")
    self.log.configure(state="disabled")
    args = Namespace(
      catalog=False,
      submit=submit,
      suggest=None if submit else self.objective.get(),
      include=subject_codes(self.include.get()),
      exclude=subject_codes(self.exclude.get()),
      headless=self.headless.get(),
      browser_executable=None,
      evidence_dir=None,
      state_dir=self.state_dir,
      password=password,
      on_result=lambda *result: self.events.put(("result", result)),
    )
    self.job = BrowserJob(self.events)
    self.set_running(True)
    self.status.set("Opening the portal…")
    self.job.start(args, config)

  def cancel(self):
    if self.job is not None:
      self.cancel_button.configure(state="disabled")
      self.status.set("Cancelling and closing the browser…")
      self.job.cancel()

  def close(self):
    if self.job is None:
      self.root.destroy()
    elif messagebox.askyesno(
      "Close Academy Registration", "Cancel the active run and close this window?", parent=self.root
    ):
      self.close_after_run = True
      self.cancel()

  def use_suggestion(self):
    if self.job is not None or not self.suggestion:
      return
    body = "\n".join(course_lines(self.suggestion["courses"]))
    current = self.courses.get("1.0", "end-1c").strip()
    has_choices = any(
      line and not line.startswith("#") for line in map(str.strip, current.splitlines())
    )
    if current != body and has_choices:
      if not messagebox.askyesno(
        "Replace course choices",
        "Replace the course choices in the editor with the suggested groups?",
        parent=self.root,
      ):
        return
    self.set_course_text(body)
    if not self.values["Semester"].get().strip() and self.suggestion["semester"]:
      self.values["Semester"].set(self.suggestion["semester"])
    self.courses.focus_set()
    if self.values["Opens"].get().strip():
      self.status.set("Suggested groups copied. Review them before scheduling registration.")
    else:
      self.status.set("Suggested groups copied. Enter the opening time before scheduling registration.")

  def drain_events(self):
    try:
      while True:
        kind, payload = self.events.get_nowait()
        if kind == "result":
          self.show_results(*payload)
        else:
          self.status.set(payload)
          self.log.configure(state="normal")
          self.log.insert("end", payload + "\n")
          self.log.see("end")
          self.log.configure(state="disabled")
          if kind == "finished":
            self.job = None
            self.set_running(False)
            if self.close_after_run:
              self.root.destroy()
              return
    except Empty:
      pass
    self.root.after(80, self.drain_events)

  def show_results(self, snapshot, planned, suggestion):
    courses, registered, raw = snapshot
    existing = set(registered)
    names = {(r["code"], r["group"]): r.get("name", "") for r in [*raw["open"], *raw["registered"]]}
    if suggestion is not None:
      fixed = suggestion.existing
      proposed = suggestion.additions
      self.suggestion = (
        {"courses": proposal_preferences(proposed, raw), "semester": raw.get("semester")}
        if proposed
        else None
      )
      summary = (
        f"{suggestion.days} school days · {suggestion.skipped_sessions} skipped sessions · "
        f"Longest stretch: {suggestion.longest_stretch} periods · "
        f"{suggestion.long_stretches} blocks of three"
      )
      if raw.get("semester"):
        summary = raw["semester"] + "\n" + summary
      if not suggestion.complete:
        summary += "\nSearch limit reached. Showing the best schedule found."
      if suggestion.unreadable:
        summary += "\nCouldn't read: " + ", ".join(suggestion.unreadable)
    else:
      fixed = [c for c in courses if c.checked or (c.code, c.group) in existing]
      proposed = planned
      summary = f"{len(existing)} registered · {len(planned)} planned"
    self.metrics.set(summary)
    self.results.delete(*self.results.get_children())
    for course in (*fixed, *proposed):
      identity = course.code, course.group
      if identity in existing:
        status = "Registered"
      elif course in fixed:
        status = "Already checked"
      else:
        status = "Suggested" if suggestion is not None else "Planned"
      self.results.insert("", "end", values=(course.key, names.get(identity, ""), status))
    self.weeks = week_grid((*fixed, *proposed))
    self.week.configure(values=[week.isoformat() for week in self.weeks])
    if self.weeks:
      self.week.current(0)
    else:
      self.week.set("")
    self.draw_week()

  def draw_week(self, event=None):
    self.grid.delete(*self.grid.get_children())
    if not self.week.get():
      return
    week = datetime.fromisoformat(self.week.get()).date()
    periods = self.weeks[week]
    for period in range(1, max(periods, default=0) + 1):
      cells = periods.get(period, [()] * 6)
      self.grid.insert("", "end", values=(period, *(", ".join(cell) for cell in cells)))


def main():
  if tk is None:
    print(
      "Tkinter is needed to launch the desktop window. Install Tk support for your Python.",
      file=sys.stderr,
    )
    return 2
  try:
    root = tk.Tk()
  except tk.TclError:
    print("Couldn't open a desktop window. Run this from a graphical session.", file=sys.stderr)
    return 2

  root.withdraw()
  root.title("Academy Registration")
  splash = ttk.Frame(root, padding=20)
  notice = ttk.Label(splash, width=55, anchor="w")
  notice.pack(fill="x")
  progress = ttk.Progressbar(splash, mode="indeterminate")
  progress.pack(fill="x", pady=(10, 0))
  events = Queue()
  result = 0

  def install():
    try:
      ensure_requirements(events.put, on_progress=events.put)
    except Exception as error:
      events.put(error)
    else:
      events.put(None)

  def poll_install():
    nonlocal result
    while True:
      try:
        event = events.get_nowait()
      except Empty:
        root.after(40, poll_install)
        return
      if isinstance(event, str):
        notice.configure(text=event)
        if not splash.winfo_manager():
          splash.pack(fill="both", expand=True)
          root.deiconify()
          progress.start()
        continue
      progress.stop()
      splash.destroy()
      if event is None:
        App(root)
        root.deiconify()
      else:
        detail = str(event) if isinstance(event, StopRun) else "Couldn't check the Python packages."
        result = 2
        messagebox.showerror("Academy Registration", detail, parent=root)
        root.destroy()
      return

  Thread(target=install).start()
  root.after(0, poll_install)
  root.mainloop()
  return result


if __name__ == "__main__":
  raise SystemExit(main())
