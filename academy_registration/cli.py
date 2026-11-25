"""Check your courses or register with --submit."""

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from .browsers import launch_browser
from .core import StopRun, choose, demo_courses, load_config, now
from .files import (
  Journal,
  PlainFormatter,
  atomic_write,
  console,
  create_config,
  print_catalog,
  print_plan,
  print_status,
  private_directory,
  save_report,
  write_json,
)
from .portal import URL, Site
from .preferences import configure
from .suggestions import describe_schedule, suggest

LOG = logging.getLogger("academy")
HANOI = ZoneInfo("Asia/Ho_Chi_Minh")


async def capture(folder, site, result, snapshot=None, catalog=False):
  """Keep the result even if saving the evidence fails."""
  if folder is None:
    return
  try:
    data = {"result": result, "time_utc": now().isoformat(), "stage": site.stage}
    if snapshot:
      courses, registered, raw = snapshot
      save_report(folder, courses, registered, raw, site.config, catalog=catalog)
      data.update(registered=registered, course_rows=len(courses), registered_courses=len(registered))
    write_json(folder / "result.json", data)
    if urlsplit(site.page.url).path.startswith("/course-registration"):
      name = result.replace("_", "-") if result != "stopped" else "login-stopped"
      atomic_write(folder / (name + ".png"), await site.page.screenshot(full_page=True))
  except Exception:
    LOG.warning("Couldn't save everything. Check the final registration status.")


async def register(site, journal, opening):
  """Check on schedule and stop if a submission is still up in the air."""
  config = site.config
  end = opening + timedelta(minutes=config["window_minutes"])
  targets = {spec["code"] for spec in config["courses"]}
  checked_session, last_refresh = False, None
  LOG.info("Ready to go at %s (Hanoi time).", opening.astimezone(HANOI).isoformat())
  while now() < end:
    current = now()
    check_at = opening - timedelta(minutes=5 if not checked_session else 1)
    if current < check_at:
      await asyncio.sleep(min(60, (check_at - current).total_seconds()))
      continue
    if not checked_session:
      await site.refresh("open")
      checked_session, last_refresh = True, now()
      LOG.info("Session checked. Ready for registration.")
      continue
    if (
      last_refresh is None
      or (current - last_refresh).total_seconds() >= config["refresh_seconds"]
      or last_refresh < opening <= current
    ):
      snapshot = await site.refresh("open")
      last_refresh = now()
    else:
      snapshot = await site.snapshot()
    if now() < opening:
      await asyncio.sleep(min(config["refresh_seconds"], (opening - now()).total_seconds()))
      continue
    courses, registered, _ = snapshot
    choices = choose(courses, registered, config, set(journal.data.get("blocked", [])))
    if targets <= {code for code, _ in registered}:
      snapshot = await site.refresh("registered")
      courses, registered, _ = snapshot
      choose(courses, registered, config)
      if targets <= {code for code, _ in registered}:
        LOG.info("Done. All %d courses are on the refreshed registration list.", len(targets))
        return snapshot
    elif choices:
      await site.submit(choices[0], journal, opening)
      continue
    next_refresh = last_refresh + timedelta(seconds=config["refresh_seconds"])
    await asyncio.sleep(max(0.1, (min(end, next_refresh) - now()).total_seconds()))
  raise StopRun("The registration window ended before I could confirm every course.")


async def run_browser(args, config):
  """Give each run its own folder, even if startup fails."""
  args = argparse.Namespace(**vars(args))
  name = now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
  parent = args.evidence_dir or private_directory(Path("reports"))
  args.evidence_dir = private_directory(parent / name)
  LOG.info("Saved run folder at %s", args.evidence_dir.resolve())
  write_json(args.evidence_dir / "result.json", {"result": "starting", "time_utc": now().isoformat()})
  try:
    await _run_browser(args, config)
  except (Exception, KeyboardInterrupt, asyncio.CancelledError) as error:
    try:
      failure = {
        "result": "interrupted"
        if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError))
        else "stopped",
        "time_utc": now().isoformat(),
        "error_type": type(error).__name__,
      }
      write_json(args.evidence_dir / "failure.json", failure)
      result = args.evidence_dir / "result.json"
      if json.loads(result.read_text(encoding="utf-8")).get("result") in ("starting", "login_verified"):
        write_json(result, failure)
    except Exception:
      LOG.warning("Couldn't save the failure report.")
    raise


def publish_result(args, snapshot, *, planned=(), suggestion=None):
  callback = getattr(args, "on_result", None)
  if callback is not None:
    callback(snapshot, planned, suggestion)


async def _run_browser(args, config):
  try:
    from playwright.async_api import async_playwright
  except ImportError:
    raise StopRun("Run python -m pip install -r requirements.txt first.") from None
  opening = datetime.fromisoformat(config["opening"])
  if args.submit and now() >= opening + timedelta(minutes=config["window_minutes"]):
    raise StopRun("This registration window has ended. Check the opening time in your settings.")
  with Journal(args.state_dir, config["username"], config["opening"]) as journal:
    async with async_playwright() as playwright:
      browser = await launch_browser(
        playwright.chromium, headless=args.headless, executable=args.browser_executable
      )
      site = None
      try:
        page = await browser.new_page()
        page.set_default_timeout(10000)
        # Suggestions use the whole list, independent of configured course IDs.
        site_config = {**config, "courses": []} if args.suggest else config
        site = Site(page, site_config, args.headless)
        password = getattr(args, "password", None)
        if password is not None:
          site.password = password
        site.stage = "opening the registration page on the portal"
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await site.ready()
        await site.snapshot()
        snapshot = await site.refresh("registered")
        courses, registered, raw = snapshot
        if args.suggest:
          snapshot = await site.refresh("open")
          raw = snapshot[2]
          schedule = suggest(raw, args.suggest, include=args.include, exclude=args.exclude)
          text = describe_schedule(schedule, raw, args.suggest)
          publish_result(args, snapshot, suggestion=schedule)
          console(text.rstrip())
          atomic_write(args.evidence_dir / "suggested-schedule.txt", text)
          await capture(args.evidence_dir, site, "schedule_suggested", snapshot, catalog=True)
          return
        pending = journal.data.get("pending")
        if pending:
          if pending not in {code + "/" + group for code, group in registered}:
            raise StopRun(
              "The last submission is still unconfirmed. Check the portal before clearing the journal."
            )
          journal.data.pop("pending")
          journal.save()
        if args.catalog:
          print_catalog(raw)
          await capture(args.evidence_dir, site, "catalog_verified", snapshot, catalog=True)
          return
        choices = choose(courses, registered, config, set(journal.data.get("blocked", [])))
        publish_result(args, snapshot, planned=choices)
        print_plan(choices)
        print_status(courses, registered, raw, config, choices, journal.data.get("blocked", []))
        await capture(args.evidence_dir, site, "login_verified", snapshot)
        if not args.submit:
          LOG.info("Dry run done. No courses were selected or submitted.")
          return
        snapshot = await register(site, journal, opening)
        publish_result(args, snapshot)
        await capture(args.evidence_dir, site, "registration_verified", snapshot)
      except Exception:
        if site:
          LOG.error("Last thing the browser was doing: %s", site.stage)
          await capture(args.evidence_dir, site, "stopped")
        raise
      finally:
        await browser.close()


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--config", type=Path, help="Use this .txt or .json settings file.")
  modes = parser.add_mutually_exclusive_group()
  for flag, help_text in (
    ("init-config", "Make a private starter file. Keeps any file you already have."),
    ("configure", "Set up your plan step by step, then review it before saving."),
    ("demo", "Try a plan with sample seats. Runs offline."),
    ("catalog", "Show every row in the current course list on the portal."),
    ("check-config", "Check your settings offline."),
    ("submit", "Wait for registration to open, then submit your plan."),
  ):
    modes.add_argument("--" + flag, action="store_true", help=help_text)
  modes.add_argument(
    "--suggest",
    choices=("days", "fatigue"),
    help="Suggest a timetable from every available subject without submitting anything.",
  )
  parser.add_argument(
    "--include",
    action="extend",
    nargs="+",
    default=[],
    metavar="CODE",
    help="Only suggest these subject codes. Use with --suggest.",
  )
  parser.add_argument(
    "--exclude",
    action="extend",
    nargs="+",
    default=[],
    metavar="CODE",
    help="Never suggest these subject codes. Takes precedence over --include.",
  )
  parser.add_argument(
    "--headless",
    action="store_true",
    help="Run with the browser hidden. Set ACADEMY_PASSWORD to sign in.",
  )
  parser.add_argument("--browser-executable", help="Use this Chromium-based browser executable.")
  parser.add_argument("--state-dir", type=Path, default=Path(".academy-run"))
  parser.add_argument(
    "--evidence-dir",
    type=Path,
    help="Save each run's private evidence here. Defaults to reports.",
  )
  args = parser.parse_args(argv)
  if (args.include or args.exclude) and not args.suggest:
    parser.error("--include and --exclude require --suggest")
  handler = logging.StreamHandler()
  handler.setFormatter(
    PlainFormatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
  )
  logging.basicConfig(level=logging.INFO, handlers=[handler])
  try:
    if args.config is None:
      candidates = [
        path for path in (Path("preferences.txt"), Path("preferences.json")) if path.exists()
      ]
      if len(candidates) > 1 and not (args.configure or args.init_config):
        raise StopRun("Found both preferences.txt and preferences.json. Pick one with --config.")
      args.config = (
        Path("preferences.txt")
        if args.configure or args.init_config or not candidates
        else candidates[0]
      )
    if args.configure:
      configure(args.config)
      return 0
    if args.init_config:
      create_config(args.config)
      console("Starter file saved. Fill in your student ID, semester, opening time, and courses.")
      return 0
    config = load_config(args.config, allow_empty_courses=bool(args.suggest))
    if args.check_config:
      console(f"Settings look good. {len(config['courses'])} courses. Opens {config['opening']}.")
    elif args.demo:
      console("Demo with sample seats. This runs offline.")
      print_plan(choose(demo_courses(config), [], config))
    else:
      asyncio.run(run_browser(args, config))
    return 0
  except StopRun as error:
    LOG.error("Stopped. %s", error)
    return 2
  except KeyboardInterrupt:
    LOG.info("Stopped. Check the journal for any submission that's still unconfirmed.")
    return 130
  except Exception as error:
    LOG.error("Stopped after %s. Check the portal before trying again.", type(error).__name__)
    return 2
