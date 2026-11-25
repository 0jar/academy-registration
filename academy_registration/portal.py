"""Read the academy’s portal and register through its usual controls."""

import asyncio
import getpass
import logging
import os
import re
import sys
import time
from datetime import timedelta
from urllib.parse import urlsplit

from .core import Course, NoSeat, StopRun, choose, normalize, now, parse_schedule

ORIGIN = "https://dangkymonhoc.dav.edu.vn"
URL = ORIGIN + "/course-registration/"
LOG = logging.getLogger("academy")


LOADING_UI = r"""() => {
  const visible = el => el.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden' && Number(getComputedStyle(el).opacity) > 0;
  return [...document.querySelectorAll('.mantine-LoadingOverlay-root,button[data-loading]')].some(visible) ||
    [...document.querySelectorAll('table tbody')].some(t => /Đang tải danh sách/i.test(t.textContent));
}"""

SNAPSHOT = r"""() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const body = document.body.innerText;
  const count = body.match(/Đã đăng ký:\s*(\d+)\s*môn\s*\(\s*(\d+)\s*TC/i);
  const advisorHeading = [...document.querySelectorAll('h2,h3,h4')].find(h=>/KẾT QUẢ DUYỆT CỦA CỐ VẤN/i.test(h.textContent));
  let advisor = advisorHeading;
  while(advisor && !/LỜI NHẮN|GHI CHÚ/i.test(advisor.textContent)) advisor=advisor.parentElement;
  const result = {semester:clean(document.querySelector('h2')?.textContent),open:[],registered:[],found:[],
    totalCourses:count?Number(count[1]):null,totalCredits:count?Number(count[2]):null,
    registrationWindow:/NGOÀI THỜI GIAN ĐĂNG KÝ|CHƯA ĐẾN THỜI GIAN ĐĂNG KÝ|HẾT THỜI GIAN ĐĂNG KÝ/i.test(body)?'closed':
      /TRONG THỜI GIAN ĐĂNG KÝ/i.test(body)?'open':'unknown',
    advisor:advisor?clean(advisor.innerText):'',activeFilters:[]};
  for (const [tableIndex,table] of [...document.querySelectorAll('table')].entries()) {
    const headers=[...table.querySelectorAll('thead tr:first-child th,thead tr:first-child td')].map(c=>clean(c.textContent));
    const kind=headers.includes('Còn lại')&&headers.includes('Chọn')?'open':headers.includes('Ngày đăng ký')&&headers.includes('Nhóm tổ')?'registered':null;
    if(!kind) continue;
    result.found.push(kind);
    for(const input of table.querySelectorAll('thead input')) {
      if(clean(input.value)) result.activeFilters.push({table:kind,value:clean(input.value)});
    }
    [...table.querySelectorAll('tbody tr')].forEach((row,rowIndex)=>{
      const cells=[...row.cells];
      const val=name=>clean(cells[headers.indexOf(name)]?.innerText);
      if(!val('Mã MH')) return;
      const checkbox=row.querySelector('input[type=checkbox]');
      result[kind].push({code:val('Mã MH'),group:val(kind==='open'?'Nhóm':'Nhóm tổ'),groupColumn:headers.indexOf(kind==='open'?'Nhóm':'Nhóm tổ'),name:val('Tên môn học'),credits:val('Số TC'),registeredAt:val('Ngày đăng ký'),status:val('Trạng thái'),schedule:val('Thời khóa biểu'),remaining:val('Còn lại'),enabled:!!checkbox&&!checkbox.disabled,checked:!!checkbox?.checked,tableIndex,rowIndex});
    });
  }
  return result;
}"""


class Site:
  def __init__(self, page, config, headless=False):
    self.page, self.config, self.headless = page, config, headless
    self.password = os.environ.get("ACADEMY_PASSWORD")
    self.dialog_problem = False
    self.verification_seconds = 120
    self.busy_seconds = 120
    self.quiet_seconds = 1.5
    self.pending_requests = set()
    self.last_request_activity = None
    self.stage = "initializing"
    self.protected = set()
    self.protected_registered = set()
    self.window_status = "unknown"
    page.on("dialog", self.dismiss_dialog)
    page.on("request", self.request_started)
    page.on("requestfinished", self.request_ended)
    page.on("requestfailed", self.request_ended)

  def request_started(self, request):
    path = urlsplit(request.url).path.casefold()
    if (
      request.resource_type in {"xhr", "fetch"} and request.method == "POST" and "/signalr" not in path
    ):
      self.pending_requests.add(request)
      self.last_request_activity = time.monotonic()

  def request_ended(self, request):
    if request in self.pending_requests:
      self.pending_requests.discard(request)
      self.last_request_activity = time.monotonic()

  async def dismiss_dialog(self, dialog):
    self.dialog_problem = True
    await dialog.dismiss()

  def check_origin(self):
    parsed = urlsplit(self.page.url)
    if (parsed.scheme, parsed.netloc) != ("https", "dangkymonhoc.dav.edu.vn"):
      raise StopRun(
        "The browser went somewhere unexpected. Stopped before entering your login details."
      )

  async def verify_student(self):
    identifier = re.compile(r"(?<![\w-])" + re.escape(self.config["username"]) + r"(?![\w-])")
    if not await self.page.get_by_text(identifier).count():
      raise StopRun(
        "I can't see your student ID on the page anymore. Stopped before clicking a course."
      )

  async def guard(self):
    self.check_origin()
    if self.dialog_problem:
      raise StopRun("The browser opened an unexpected dialog. Check what it says.")
    if await self.page.locator(
      'iframe[title*="reCAPTCHA"],iframe[title*="hCaptcha"],input[autocomplete="one-time-code"]'
    ).count():
      raise StopRun("The portal is asking for a CAPTCHA or one-time code. Finish that in the browser.")

  async def loading(self):
    return await self.page.evaluate(LOADING_UI)

  async def wait_idle(self):
    """Wait for real data, including retry gaps and fading table overlays."""
    deadline = time.monotonic() + self.busy_seconds
    while time.monotonic() < deadline:
      await self.guard()
      if "/login" in urlsplit(self.page.url).path:
        return
      if await self.page.get_by_role("dialog").filter(visible=True).count():
        raise StopRun("The portal has a message open. Check it before continuing.")
      quiet = (
        self.last_request_activity is None
        or time.monotonic() - self.last_request_activity >= self.quiet_seconds
      )
      if not self.pending_requests and quiet and not await self.loading():
        return
      await self.page.wait_for_timeout(100)
    raise StopRun("The portal is still loading or processing. Stopped here.")

  async def ready(self):
    self.stage = "waiting for the portal to show the login or registration page"
    await self.guard()
    await self.page.wait_for_function(
      """() => !!document.querySelector('input[type="password"]') ||
      [...document.querySelectorAll('table')].some(t => t.textContent.includes('Còn lại'))""",
      timeout=30000,
    )
    await self.guard()
    if "/login" in urlsplit(self.page.url).path:
      self.stage = "checking login controls"
      if not self.password:
        if self.headless or not sys.stdin.isatty():
          raise StopRun("Set ACADEMY_PASSWORD or run in a terminal so you can enter your password.")
        self.password = getpass.getpass("School password (typing is hidden): ")
      username = self.page.get_by_label(
        re.compile(r"Tên đăng nhập|Mã sinh viên|Student ID|Username", re.I)
      )
      password = self.page.locator('input[type="password"]')
      if await username.count() != 1 or await password.count() != 1:
        raise StopRun("the portal's login form looks different. Check it before signing in.")
      await username.fill(self.config["username"])
      await self.guard()
      await password.fill(self.password)
      self.stage = "submitting login"
      await self.guard()
      await self.page.get_by_role("button", name=re.compile(r"^Đăng nhập$|^Sign in$", re.I)).click()
      self.stage = "waiting for registration after login"
      try:
        await self.page.wait_for_url(re.compile(r"/course-registration/?(?:\?.*)?$"), timeout=30000)
      except Exception:
        raise StopRun(
          "Couldn't get to the registration page. Check your login details and any messages from the portal."
        ) from None
    await self.guard()
    self.stage = "checking the registration page and your student ID"
    if urlsplit(self.page.url).path.rstrip("/") != "/course-registration":
      raise StopRun("Open the school’s registration page to continue.")
    await self.page.get_by_role("table").first.wait_for(timeout=30000)
    await self.page.get_by_role(
      "heading", name=re.compile(re.escape(self.config["semester"]), re.I)
    ).wait_for(timeout=30000)
    await self.verify_student()
    self.stage = "login and student identifier verified"
    LOG.info("Signed in. Your registration page is open.")

  async def snapshot(self, wait=True):
    await self.guard()
    if wait:
      await self.wait_idle()
    raw = await self.page.evaluate(SNAPSHOT)
    if set(raw["found"]) != {"open", "registered"} or len(raw["found"]) != 2:
      raise StopRun("The course tables haven't loaded or the portal changed their layout.")
    if normalize(self.config["semester"]).casefold() not in raw["semester"].casefold():
      raise StopRun("The semester on the portal doesn't match your settings.")
    if raw["activeFilters"]:
      raise StopRun("Clear the search filters in both tables so I can see every course.")
    if raw["totalCourses"] is not None and raw["totalCourses"] != len(raw["registered"]):
      raise StopRun(
        "The registered list looks incomplete. Its row count doesn't match the portal's total. Check the filters and pages."
      )
    self.window_status = raw["registrationWindow"]
    courses = []
    targets = {item["code"] for item in self.config["courses"]}
    registered = [(r["code"], r["group"]) for r in raw["registered"]]
    needed = (
      targets | {code for code, _ in registered} | {r["code"] for r in raw["open"] if r["checked"]}
    )
    for row in raw["open"]:
      if row["code"] not in needed:
        continue
      try:
        meetings = parse_schedule(row["schedule"])
        remaining = int(row["remaining"])
      except (StopRun, ValueError):
        raise StopRun(
          "Couldn't read the schedule or seat count for " + row["code"] + "/" + row["group"]
        ) from None
      courses.append(
        Course(row["code"], row["group"], meetings, remaining, row["enabled"], row["checked"])
      )
    keys = [c.key for c in courses]
    if len(keys) != len(set(keys)):
      raise StopRun("The portal shows the same course and group more than once. Check the list.")
    if wait:
      confirmed = {code + "/" + group for code, group in registered}
      present = confirmed | {c.key for c in courses if c.checked}
      missing = (self.protected - present) | (self.protected_registered - confirmed)
      if missing:
        raise StopRun(
          "Some earlier selections disappeared: "
          + ", ".join(sorted(missing))
          + ". Stopped here so you can check the list."
        )
      self.protected |= present
      self.protected_registered |= confirmed
    return courses, registered, raw

  async def refresh(self, kind="open"):
    """Use the refresh belonging to this table, not an unrelated header button."""
    await self.guard()
    await self.wait_idle()
    if "/login" in urlsplit(self.page.url).path:
      await self.ready()
      return await self.snapshot()
    header = "Còn lại" if kind == "open" else "Ngày đăng ký"
    tables = self.page.get_by_role("table")
    table = None
    for candidate in await tables.all():
      if await candidate.locator("thead").get_by_text(header, exact=True).count():
        table = candidate
        break
    if table is None:
      raise StopRun("Couldn't find the table's refresh button.")
    control = table.locator("xpath=..").locator(
      "xpath=ancestor-or-self::*[.//button[normalize-space(.)='Làm mới' or starts-with(normalize-space(.),'Chờ ')]][1]"
    )
    buttons = control.get_by_role("button", name=re.compile(r"^Làm mới$|^Chờ \d+s$"))
    if await buttons.count() != 1:
      raise StopRun("Couldn't tell which refresh button goes with this table.")
    button = buttons.first
    deadline = asyncio.get_running_loop().time() + self.busy_seconds
    while not await button.is_enabled():
      if asyncio.get_running_loop().time() > deadline:
        raise StopRun("Refresh is still busy. Stopped here.")
      await self.page.wait_for_timeout(200)
    await button.click()
    await self.page.wait_for_timeout(50)
    await self.wait_idle()
    await self.ready()
    return await self.snapshot()

  async def inspect_dialog(self, pending):
    dialogs = self.page.get_by_role("dialog").filter(visible=True)
    if not await dialogs.count():
      return
    dialog = dialogs.last
    title = (
      normalize(await dialog.locator("h2").inner_text()) if await dialog.locator("h2").count() else ""
    )
    text = normalize(await dialog.inner_text())
    if title.startswith("Xác nhận đăng ký môn học") and not re.search(
      r"điều khoản|hợp đồng|cam kết|thanh toán|terms|contract|waiver", text, re.I
    ):
      await dialog.get_by_role("button", name="Đồng ý", exact=True).click()
      return
    if title.startswith("Đăng ký không thành công") and re.search(
      r"hết chỗ|đã đủ|không còn chỗ|sĩ số|số lượng.*(đủ|tối đa|vượt)", text, re.I
    ):
      await dialog.get_by_role("button", name="Đã hiểu", exact=True).click()
      raise NoSeat("No seat for " + pending)
    raise StopRun("The portal showed an unexpected message. Check the browser before continuing.")

  async def submit(self, course, journal, opening):
    if now() < opening:
      raise StopRun("It's too early to register.")
    deadline_time = opening + timedelta(minutes=self.config["window_minutes"])
    if now() >= deadline_time:
      raise StopRun("The registration window in your settings has ended.")
    await self.guard()
    if journal.data.get("pending"):
      raise StopRun("An earlier submission is still unconfirmed. Check the portal first.")
    courses, registered, raw = await self.snapshot()
    await self.verify_student()
    if self.window_status == "closed":
      raise StopRun("The portal says registration is closed.")
    if now() >= deadline_time:
      raise StopRun("The registration window ended while waiting for the portal.")
    choices = choose(courses, registered, self.config, set(journal.data.get("blocked", [])))
    if not choices or choices[0].key != course.key:
      return False
    rows = [r for r in raw["open"] if r["code"] == course.code and r["group"] == course.group]
    if len(rows) != 1 or rows[0]["checked"]:
      raise StopRun(
        "This row is already checked or I can't identify it. Refresh the registered list and check it."
      )
    row = rows[0]
    table = self.page.locator("table").nth(row["tableIndex"])
    target_row = table.locator("tbody tr").filter(
      has=self.page.get_by_role("cell", name=course.code, exact=True)
    )
    target_row = target_row.filter(
      has=self.page.locator("td")
      .nth(row["groupColumn"])
      .filter(has_text=re.compile(r"^\s*" + re.escape(course.group) + r"\s*$"))
    )
    if await target_row.count() != 1:
      raise StopRun("The course or group row changed, or I can't tell which row it is. Stopped here.")
    target = target_row.get_by_role("checkbox")
    if not await target.is_enabled():
      return False
    if await target.is_checked():
      return False
    journal.data["pending"] = course.key
    journal.save()
    LOG.info("Registering %s", course.key)
    try:
      await target.check(timeout=5000)
    except Exception as error:
      if "Clicking the checkbox did not change its state" not in str(error):
        raise
    deadline = asyncio.get_running_loop().time() + self.verification_seconds
    try:
      while asyncio.get_running_loop().time() < deadline:
        await self.guard()
        if "/login" in urlsplit(self.page.url).path:
          await self.ready()
        await self.inspect_dialog(course.key)
        if self.pending_requests or await self.loading():
          await self.page.wait_for_timeout(100)
          continue
        _, registered, _ = await self.snapshot(wait=False)
        if (course.code, course.group) in registered:
          self.protected.add(course.key)
          self.protected_registered.add(course.key)
          journal.data.pop("pending", None)
          journal.save()
          LOG.info("Confirmed %s on the registered list.", course.key)
          return True
        await self.page.wait_for_timeout(250)
    except NoSeat:
      _, registered, _ = await self.refresh("registered")
      if (course.code, course.group) in registered:
        raise StopRun(
          "The portal said it failed, but the course is on the registered list. Check it before trying again."
        ) from None
      journal.data.pop("pending", None)
      journal.data.setdefault("blocked", []).append(course.key)
      journal.save()
      LOG.info("No seat for %s. Checking which fallbacks fit.", course.key)
      return False
    raise StopRun(
      "Couldn't confirm registration for "
      + course.key
      + ". Check the portal before trying again. The journal still marks it as pending."
    )
