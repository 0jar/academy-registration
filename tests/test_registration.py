import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from platform_checks import assert_private

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import academy_registration as academy
from academy_registration.browsers import launch_browser


class PlannerTests(unittest.TestCase):
  def setUp(self):
    self.config = academy.load_config(Path(academy.__file__).with_name("preferences.example.json"))
    self.rows = academy.demo_courses(self.config)

  def keys(self, blocked=(), registered=()):
    return [c.key for c in academy.choose(self.rows, registered, self.config, set(blocked))]

  def test_preferred(self):
    self.assertEqual(self.keys(), ["IE.014.02/2", "IE.068.02/2", "IE.081.03/1", "IE.087.03/1"])

  def test_different_periods_on_each_day_require_exact_slots(self):
    option = {"group": "1", "slots": [{"day": 4, "periods": [10, 12]}, {"day": 5, "periods": [7, 9]}]}
    config = {"courses": [{"code": "IE.022.03", "groups": [option]}]}
    course = academy.demo_courses(config)[0]
    self.assertTrue(academy.allowed(course, option))
    self.assertFalse(academy.allowed(replace(course, meetings=course.meetings[:1]), option))
    wrong = replace(
      course, meetings=(course.meetings[0], replace(course.meetings[1], first=10, last=12))
    )
    self.assertFalse(academy.allowed(wrong, option))
    self.assertEqual(academy.choose([course], [], config), [course])

  def test_both_friday_fallback_triggers(self):
    for lost in ("IE.014.02/2", "IE.068.02/2"):
      with self.subTest(lost=lost):
        self.assertEqual(self.keys([lost])[:2], ["IE.014.02/1", "IE.068.02/4"])

  def test_wednesday_only_after_both_friday_options(self):
    self.assertEqual(self.keys(["IE.068.02/2", "IE.068.02/4"])[:2], ["IE.014.02/2", "IE.068.02/1"])

  def test_keeps_existing_group_and_avoids_clash(self):
    keys = self.keys(["IE.068.02/2"], [("IE.014.02", "2")])
    self.assertNotIn("IE.014.02/2", keys)
    self.assertIn("IE.068.02/1", keys)
    self.assertNotIn("IE.068.02/4", keys)

  def test_partial_plan_when_subject_has_no_seats(self):
    self.rows = [replace(r, remaining=0) if r.code == "IE.014.02" else r for r in self.rows]
    self.assertEqual(self.keys(), ["IE.068.02/2", "IE.081.03/1", "IE.087.03/1"])

  def test_prechecked_target_is_fixed_without_registered_table_proof(self):
    self.rows = [replace(r, checked=True) if r.key == "IE.014.02/1" else r for r in self.rows]
    with self.assertLogs("academy", level="WARNING") as messages:
      keys = self.keys()
    self.assertFalse(any(key.startswith("IE.014.02/") for key in keys))
    self.assertIn("IE.068.02/4", keys)
    self.assertIn("IE.014.02/1", " ".join(messages.output))
    self.assertIn("IE.068.02/2", " ".join(messages.output))

  def test_prechecked_target_outside_preferences_is_preserved(self):
    self.rows.append(
      replace(
        self.rows[0], group="99", checked=True, meetings=(replace(self.rows[0].meetings[0], day=5),)
      )
    )
    self.assertFalse(any(key.startswith("IE.014.02/") for key in self.keys()))

  def test_prechecked_unrelated_course_blocks_and_reports_target_conflict(self):
    finance = next(r for r in self.rows if r.code == "IE.081.03")
    self.rows.append(replace(finance, code="OTHER.001", checked=True))
    with self.assertLogs("academy", level="WARNING") as messages:
      keys = self.keys()
    self.assertNotIn(finance.key, keys)
    self.assertIn("OTHER.001/1", " ".join(messages.output))
    self.assertIn(finance.key, " ".join(messages.output))

  def test_prechecked_conflicts_and_duplicate_groups_stop_with_names(self):
    for keys in ({"IE.014.02/1", "IE.068.02/2"}, {"IE.014.02/1", "IE.014.02/2"}):
      self.rows = [replace(r, checked=r.key in keys) for r in academy.demo_courses(self.config)]
      with self.assertRaisesRegex(academy.StopRun, "Check the portal"):
        self.keys()

  def test_full_or_locked_never_selected(self):
    for changes in ({"remaining": 0}, {"enabled": False}):
      self.rows = [replace(r, **changes) for r in academy.demo_courses(self.config)]
      self.assertEqual(self.keys(), [])

  def test_existing_conflicting_or_unknown_course_stops(self):
    for existing in ([("IE.014.02", "1"), ("IE.068.02", "2")], [("OTHER.001", "1")]):
      with self.assertRaises(academy.StopRun):
        self.keys(registered=existing)

  def test_changed_timetable_stops(self):
    self.rows[0] = replace(self.rows[0], meetings=(replace(self.rows[0].meetings[0], day=5),))
    with self.assertRaises(academy.StopRun):
      self.keys()

  def test_date_ranges_and_real_weekdays(self):
    a = academy.Course("A", "1", (academy.Meeting(6, 7, 9, date(2027, 1, 4), date(2027, 1, 29)),))
    b = replace(
      a, code="B", meetings=(academy.Meeting(6, 9, 12, date(2027, 2, 19), date(2027, 3, 26)),)
    )
    self.assertFalse(academy.clashes(a, b))
    b = replace(b, meetings=(academy.Meeting(6, 9, 12, date(2027, 1, 8), date(2027, 2, 26)),))
    self.assertTrue(academy.clashes(a, b), "period 9 overlaps inclusively")
    b = replace(b, meetings=(academy.Meeting(6, 7, 9, date(2027, 1, 4), date(2027, 1, 7)),))
    self.assertFalse(academy.clashes(a, b), "no Friday in the overlapping dates")

  def test_split_holiday_blocks(self):
    text = (
      "Thứ 6, tiết 7 → 9, GV A, 08/01/27 đến 29/01/27Thứ 6, tiết 7 -> 9, GV A, 19/02/27 đến 26/03/27"
    )
    self.assertEqual(len(academy.parse_schedule(text)), 2)
    for bad in ("-", "Thứ 6, tiết 7 → 9", text + "Chủ nhật", text + "Thứ 4, tiết TBD"):
      with self.assertRaises(academy.StopRun):
        academy.parse_schedule(bad)

  def test_journal_crash_marker_survives_restart_and_lock_excludes_duplicate(self):
    with tempfile.TemporaryDirectory() as folder:
      journal = academy.Journal(Path(folder), "fixture", self.config["opening"])
      journal.data["pending"] = "IE.014.02/2"
      journal.save()
      assert_private(self, journal.path, 0o600)
      with self.assertRaises(academy.StopRun):
        academy.Journal(Path(folder), "fixture", self.config["opening"])
      journal.close()
      reopened = academy.Journal(Path(folder), "fixture", self.config["opening"])
      self.assertEqual(reopened.data["pending"], "IE.014.02/2")
      self.assertNotIn("password", reopened.path.read_text(encoding="utf-8"))
      reopened.close()

  def test_changing_opening_date_cannot_bypass_pending_submission(self):
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      journal = academy.Journal(folder, "fixture", self.config["opening"])
      journal.data["pending"] = "IE.014.02/2"
      journal.save()
      journal.close()
      later = "2026-11-26T08:00:00+07:00"
      with self.assertRaisesRegex(academy.StopRun, "Changing the opening date"):
        academy.Journal(folder, "fixture", later)
      old = academy.Journal(folder, "fixture", self.config["opening"])
      self.assertEqual(old.data["pending"], "IE.014.02/2")
      old.close()


class ConfigAndReportTests(unittest.TestCase):
  def setUp(self):
    self.config = academy.load_config(Path(academy.__file__).with_name("preferences.example.json"))

  def test_bad_preferences_rejected_before_browser_start(self):
    changes = [
      lambda c: c.update(refresh_seconds=True),
      lambda c: c.update(window_minutes=0),
      lambda c: c.update(opening="2026-11-25T08:00:00"),
      lambda c: c["courses"][0]["groups"][0].update(days=[9]),
      lambda c: c["courses"][0]["groups"][0].update(periods=[12, 7]),
      lambda c: c["courses"][0]["groups"][0].update(group=2),
      lambda c: c["courses"][0]["groups"][0].update(slots=[]),
      lambda c: c["courses"][0]["groups"][0].update(last_resort="false"),
    ]
    with tempfile.TemporaryDirectory() as folder:
      path = Path(folder) / "config.json"
      for change in changes:
        config = json.loads(json.dumps(self.config))
        change(config)
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(academy.StopRun, "Check your settings"):
          academy.load_config(path)
      path.write_text("{broken")
      with self.assertRaises(academy.StopRun):
        academy.load_config(path)

  def test_reports_explain_conflicts_export_utf8_and_compare_missing_registrations(self):
    rows = academy.demo_courses(self.config)
    rows.append(replace(rows[-1], code="OTHER.001", checked=True))
    registered = [("OTHER.001", "1")]
    raw = {
      "semester": self.config["semester"],
      "open": [
        {
          "code": r.code,
          "group": r.group,
          "name": "=untrusted" if r.code == "OTHER.001" else "Quản trị",
          "credits": "3",
          "remaining": str(r.remaining),
          "checked": r.checked,
          "schedule": "Thứ 2, tiết 10–12",
        }
        for r in rows
      ],
      "registered": [{"code": "OTHER.001", "group": "1"}],
      "totalCourses": 1,
      "totalCredits": 3,
      "registrationWindow": "open",
      "advisor": "Chưa có kết quả duyệt",
    }
    choices = academy.choose(rows, registered, self.config)
    detail = academy.plan_details(rows, registered, self.config, choices)
    self.assertIn("overlaps with an existing selection: OTHER.001/1", json.dumps(detail))
    with tempfile.TemporaryDirectory() as folder:
      folder = Path(folder)
      academy.save_report(folder, rows, registered, raw, self.config, catalog=True)
      report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
      self.assertEqual(report["total_credits"], 3)
      self.assertIn("'=untrusted", (folder / "courses.csv").read_text(encoding="utf-8-sig"))
      self.assertIn("OTHER.001/1", (folder / "timetable.txt").read_text(encoding="utf-8"))
      with self.assertLogs("academy", level="WARNING") as messages:
        academy.save_report(
          folder, rows, [], {**raw, "registered": [], "totalCourses": 0}, self.config, catalog=True
        )
      self.assertIn("disappeared", " ".join(messages.output))
      self.assertEqual(
        json.loads((folder / "report.json").read_text(encoding="utf-8"))["changes"]["removed"],
        [["OTHER.001", "1"]],
      )


def fixture(config, mode="success"):
  rows = []
  for row in academy.demo_courses(config):
    schedule = "".join(
      f"Thứ {m.day}, tiết {m.first} → {m.last}, GV Fixture, 04/01/27 đến 02/04/27" for m in row.meetings
    )
    rows.append(
      {"code": row.code, "group": row.group, "schedule": schedule, "remaining": 5, "enabled": True}
    )
  payload = json.dumps(
    {"rows": rows, "mode": mode, "username": config["username"], "semester": config["semester"]},
    ensure_ascii=False,
  )
  return """<!doctype html><meta charset="utf-8"><title>Local registration test fixture</title>
<div id="app"></div><script>
const data=PAYLOAD;const registered=[];const clicks={};const clickScrollPositions=[];let failed=false;
let processing=false,pendingKey=null,loadingKind=data.mode==='stuck-loading'?'open':null;
if(data.mode==='scroll'){
 const style=document.createElement('style');
 style.textContent='.fixture-scrollport{height:180px;width:360px;overflow:auto}table{min-width:1400px;border-collapse:collapse}td{height:110px;min-width:60px}thead{position:sticky;top:0;background:white}';
 document.head.append(style);
}
const app=document.getElementById('app');
function render(){
 if(data.mode==='redirect'&&!location.pathname.includes('/login')&&!sessionStorage.getItem('fixture-auth')){
  app.textContent='Loading';setTimeout(()=>location.href='/login/',150);return;
 }
 if(location.pathname.includes('/login')){
  app.innerHTML='<form><label>Tên đăng nhập<input name="username"></label><label>Mật khẩu<input type="password"></label><button>Đăng nhập</button></form>';
  app.querySelector('form').onsubmit=e=>{e.preventDefault();sessionStorage.setItem('fixture-auth','yes');location.href='/course-registration/'};return;
 }
 app.innerHTML='<header>'+data.username+'</header><h2>ĐĂNG KÝ MÔN HỌC-'+data.semester+'</h2>';
 for(const kind of ['open','registered']){
  const section=document.createElement('section');const refresh=document.createElement('button');refresh.textContent='Làm mới';refresh.setAttribute('data-kind',kind);
  refresh.onclick=()=>{
    if(data.mode==='expired-on-refresh'&&!sessionStorage.getItem('fixture-auth')){location.href='/login/';return;}
    if(data.mode==='slow-refresh'||data.mode==='refresh-retry'){
      loadingKind=kind;render();
      const work=async()=>{
        let response=await fetch('/fixture-api/refresh',{method:'POST'});
        if(!response.ok){loadingKind=null;render();await new Promise(r=>setTimeout(r,600));response=await fetch('/fixture-api/refresh',{method:'POST'})}
        if(data.mode==='refresh-retry')data.rows[0].remaining=3;
        // The spinner button stops before the overlay and loading row disappear.
        for(const b of app.querySelectorAll('button[data-loading]')){b.removeAttribute('data-loading');b.disabled=false}
        setTimeout(()=>{loadingKind=null;render()},350);
      };work();return;
    }
    refresh.disabled=true;refresh.setAttribute('data-loading','');setTimeout(()=>{refresh.disabled=false;refresh.removeAttribute('data-loading')},40)
  };
  if(loadingKind===kind){refresh.disabled=true;refresh.setAttribute('data-loading','')}
  section.append(refresh);const table=document.createElement('table');
  const headers=kind==='open'?['Chọn','Mã MH','Tên môn học','Nhóm','Tổ','Số TC','Lớp','Số lượng','Còn lại','Thời khóa biểu']:['Xoá','Mã MH','Tên môn học','Nhóm tổ','Số TC','Lớp','Ngày đăng ký','Trạng thái','TKB'];
  table.innerHTML='<thead><tr>'+headers.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody></tbody>';
  const tbody=table.querySelector('tbody');
  for(const row of loadingKind===kind?[]:kind==='open'?data.rows:registered){
   const tr=document.createElement('tr');const key=row.code+'/'+row.group;
   const values=kind==='open'?['',row.code,'Fixture',row.group,'-','2','*','20',row.remaining,row.schedule]:['',row.code,'Fixture',row.group,'2','*','now','registered','TKB'];
   tr.innerHTML=values.map(v=>'<td>'+v+'</td>').join('');
   if(kind==='open'){
    const check=document.createElement('input');check.type='checkbox';check.disabled=processing||!row.enabled;check.checked=!!row.checked||pendingKey===key||registered.some(r=>r.code===row.code&&r.group===row.group);tr.cells[0].append(check);
    check.onclick=()=>{clicks[key]=(clicks[key]||0)+1;
      const viewport=check.closest('.fixture-scrollport');
      if(viewport)clickScrollPositions.push({key,top:viewport.scrollTop,left:viewport.scrollLeft});
      if(data.mode==='uncertain')return;
      if(['slow-success','slow-failure','slow-confirm'].includes(data.mode)){
        pendingKey=key;processing=true;render();
        const work=async()=>{
          await fetch('/fixture-api/register',{method:'POST'});
          if(data.mode==='slow-failure'){
            row.remaining=0;processing=false;pendingKey=null;render();modal('Đăng ký không thành công','Đã đủ sĩ số lớp','Đã hiểu',()=>{});return;
          }
          if(data.mode==='slow-confirm'){
            processing=false;render();modal('Xác nhận đăng ký môn học','Đăng ký lớp này?','Đồng ý',()=>{pendingKey=null;registered.push(row);render()});return;
          }
          registered.push(row);loadingKind='registered';render();
          setTimeout(()=>{processing=false;pendingKey=null;loadingKind=null;render()},350);
        };work();return;
      }
      if(data.mode==='full'&&!failed&&key==='IE.014.02/2'){failed=true;row.remaining=0;check.checked=false;modal('Đăng ký không thành công','Đã đủ sĩ số lớp','Đã hiểu',()=>{});return;}
      const save=()=>{registered.push(row);render()};
      if(data.mode==='confirm'||data.mode==='agreement')modal('Xác nhận đăng ký môn học',data.mode==='agreement'?'Tôi cam kết điều khoản':'Đăng ký lớp này?','Đồng ý',save);else save();
    };
   }
   tbody.append(tr);
  }
  if(loadingKind===kind){
    tbody.innerHTML='<tr><td colspan="15">Đang tải danh sách '+(kind==='open'?'môn học mở...':'đã đăng ký...')+'</td></tr>';
    const overlay=document.createElement('div');overlay.className='mantine-LoadingOverlay-root';overlay.textContent='•••';section.append(overlay);
  }
  if(data.mode==='scroll'){const viewport=document.createElement('div');viewport.className='fixture-scrollport';viewport.append(table);section.append(viewport)}else section.append(table);
  app.append(section);
 }
}
function modal(title,text,label,action){const el=document.createElement('div');el.setAttribute('role','dialog');el.innerHTML='<h2>'+title+'</h2><p>'+text+'</p><button>'+label+'</button>';el.querySelector('button').onclick=()=>{el.remove();action()};document.body.append(el)}
window.fixture={registered,clicks,data,clickScrollPositions,get processing(){return processing},get loadingKind(){return loadingKind}};render();
</script>""".replace("PAYLOAD", payload)


@unittest.skipUnless(
  os.environ.get("ACADEMY_BROWSER_TESTS") == "1" and importlib.util.find_spec("playwright"),
  "Set ACADEMY_BROWSER_TESTS=1 with Playwright installed for local browser tests",
)
class BrowserTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    from playwright.async_api import async_playwright

    self.config = academy.load_config(Path(academy.__file__).with_name("preferences.example.json"))
    self.manager = await async_playwright().start()
    self.browser = await launch_browser(
      self.manager.chromium, headless=True, executable=os.environ.get("ACADEMY_TEST_BROWSER")
    )
    self.page = await self.browser.new_page()
    self.page.set_default_timeout(2500)
    self.temp = tempfile.TemporaryDirectory()
    self.journal = academy.Journal(Path(self.temp.name), "fixture", self.config["opening"])
    self.opening = datetime.now(timezone.utc) - timedelta(minutes=1)
    self.mode = "success"
    self.external = []
    self.api_calls = []

    async def route(request):
      if "/fixture-api/" in request.request.url:
        self.api_calls.append(request.request.url)
        await asyncio.sleep(0.25)
        status = 503 if self.mode == "refresh-retry" and len(self.api_calls) == 1 else 200
        await request.fulfill(status=status, content_type="application/json", body="{}")
      elif request.request.url.startswith(academy.ORIGIN + "/"):
        await request.fulfill(
          status=200, content_type="text/html", body=fixture(self.config, self.mode)
        )
      else:
        self.external.append(request.request.url)
        await request.abort()

    await self.page.route("**/*", route)
    await self.page.goto(academy.URL)
    self.site = academy.Site(self.page, self.config, headless=True)

  async def asyncTearDown(self):
    self.journal.close()
    self.temp.cleanup()
    await self.browser.close()
    await self.manager.stop()

  async def use_mode(self, mode):
    self.mode = mode
    await self.page.goto(academy.URL)

  async def test_dry_plan_refresh_and_fixture_login(self):
    self.site.password = "fixture-password"
    await self.page.goto(academy.ORIGIN + "/login/")
    await self.site.ready()
    courses, registered, _ = await self.site.refresh()
    self.assertEqual(len(academy.choose(courses, registered, self.config)), 4)
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})
    self.assertEqual(self.external, [])

  async def test_login_after_delayed_client_redirect(self):
    self.site.password = "fixture-password"
    await self.use_mode("redirect")
    await self.site.ready()
    self.assertEqual(self.site.stage, "login and student identifier verified")
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})

  async def test_refresh_recovers_login_redirect_without_selecting_courses(self):
    await self.use_mode("expired-on-refresh")
    self.site.password = "fixture-password"
    courses, registered, _ = await self.site.refresh()
    self.assertEqual(len(courses), 8)
    self.assertEqual(registered, [])
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})

  async def test_four_submissions_and_refreshed_verification(self):
    await self.site.ready()
    for _ in range(4):
      courses, registered, _ = await self.site.snapshot()
      await self.site.submit(
        academy.choose(courses, registered, self.config)[0], self.journal, self.opening
      )
    courses, registered, _ = await self.site.refresh("registered")
    self.assertEqual(len(registered), 4)
    self.assertEqual(academy.choose(courses, registered, self.config), [])
    self.assertNotIn("pending", self.journal.data)
    self.assertTrue(all(n == 1 for n in (await self.page.evaluate("fixture.clicks")).values()))

  async def test_prechecked_target_outside_preferences_never_clicked(self):
    await self.page.evaluate("""() => {
      const row=fixture.data.rows.find(r=>r.code==='IE.014.02');
      row.group='99';row.checked=true;render();
    }""")
    courses, registered, _ = await self.site.snapshot()
    original = next(r for r in courses if r.code == "IE.014.02" and r.group == "99")
    self.assertFalse(await self.site.submit(original, self.journal, self.opening))
    choices = academy.choose(courses, registered, self.config)
    self.assertFalse(any(r.code == original.code for r in choices))
    self.assertTrue(await self.site.submit(choices[0], self.journal, self.opening))
    self.assertTrue(await self.page.evaluate("fixture.data.rows.find(r=>r.group==='99').checked"))
    self.assertNotIn(original.key, await self.page.evaluate("fixture.clicks"))

  async def test_prechecked_unrelated_row_included_and_conflict_reported(self):
    await self.page.evaluate("""() => {
      const row=fixture.data.rows.find(r=>r.code==='IE.081.03');
      fixture.data.rows.push({...row,code:'OTHER.001',checked:true});render();
    }""")
    courses, registered, _ = await self.site.snapshot()
    self.assertTrue(any(r.code == "OTHER.001" and r.checked for r in courses))
    with self.assertLogs("academy", level="WARNING") as messages:
      choices = academy.choose(courses, registered, self.config)
    self.assertFalse(any(r.code == "IE.081.03" for r in choices))
    self.assertIn("OTHER.001/1", " ".join(messages.output))
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})

  async def test_checkbox_checked_after_snapshot_is_never_toggled_off(self):
    courses, registered, _ = await self.site.snapshot()
    choice = academy.choose(courses, registered, self.config)[0]
    snapshot = self.site.snapshot

    async def race_snapshot(*args, **kwargs):
      result = await snapshot(*args, **kwargs)
      await self.page.get_by_role("checkbox").first.evaluate("(el)=>{el.checked=true}")
      return result

    self.site.snapshot = race_snapshot
    self.assertFalse(await self.site.submit(choice, self.journal, self.opening))
    self.assertTrue(await self.page.get_by_role("checkbox").first.is_checked())
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})
    self.assertNotIn("pending", self.journal.data)

  async def test_inner_scroll_keeps_all_rows_and_clicks_offscreen_checkbox(self):
    await self.use_mode("scroll")
    courses, registered, _ = await self.site.snapshot()
    keys_before = {row.key for row in courses}
    viewport = self.page.locator(".fixture-scrollport").first
    await viewport.evaluate("(el)=>{el.scrollTop=el.scrollHeight;el.scrollLeft=el.scrollWidth}")
    after, _, _ = await self.site.snapshot()
    self.assertEqual({row.key for row in after}, keys_before)
    self.assertEqual(len(after), 8, "off-screen rows must be included in the DOM snapshot")
    for _ in range(2):
      courses, registered, _ = await self.site.snapshot()
      await self.site.submit(
        academy.choose(courses, registered, self.config)[0], self.journal, self.opening
      )
    await viewport.evaluate("(el)=>{el.scrollTop=0;el.scrollLeft=el.scrollWidth}")
    courses, registered, raw = await self.site.snapshot()
    finance = academy.choose(courses, registered, self.config)[0]
    self.assertEqual(finance.key, "IE.081.03/1")
    row = next(r for r in raw["open"] if r["code"] == finance.code)
    checkbox = (
      self.page.locator("table")
      .nth(row["tableIndex"])
      .locator("tbody tr")
      .nth(row["rowIndex"])
      .get_by_role("checkbox")
    )
    port_box = await viewport.bounding_box()
    check_box = await checkbox.bounding_box()
    self.assertGreater(
      check_box["y"], port_box["y"] + port_box["height"], "target must start below the inner viewport"
    )
    self.assertLess(
      check_box["x"], port_box["x"], "target must also start outside the horizontal viewport"
    )
    self.assertTrue(await self.site.submit(finance, self.journal, self.opening))
    position = await self.page.evaluate("fixture.clickScrollPositions.at(-1)")
    self.assertEqual(position["key"], finance.key)
    self.assertGreater(position["top"], 0, "Playwright must scroll the inner viewport before clicking")
    self.assertEqual(await self.page.evaluate("fixture.clicks['IE.081.03/1']"), 1)

  async def test_full_class_race_replans_both_friday_groups(self):
    await self.use_mode("full")
    courses, registered, _ = await self.site.snapshot()
    self.assertFalse(
      await self.site.submit(
        academy.choose(courses, registered, self.config)[0], self.journal, self.opening
      )
    )
    courses, registered, _ = await self.site.snapshot()
    self.assertEqual(
      [
        r.key
        for r in academy.choose(courses, registered, self.config, set(self.journal.data["blocked"]))
      ][:2],
      ["IE.014.02/1", "IE.068.02/4"],
    )
    self.assertNotIn("pending", self.journal.data)

  async def test_normal_confirmation_succeeds(self):
    await self.use_mode("confirm")
    courses, registered, _ = await self.site.snapshot()
    self.assertTrue(
      await self.site.submit(
        academy.choose(courses, registered, self.config)[0], self.journal, self.opening
      )
    )

  async def test_optimistic_tick_waits_for_request_and_registered_table_loading(self):
    await self.use_mode("slow-success")
    courses, registered, _ = await self.site.snapshot()
    task = asyncio.create_task(
      self.site.submit(academy.choose(courses, registered, self.config)[0], self.journal, self.opening)
    )
    await self.page.wait_for_function("fixture.processing")
    self.assertTrue(
      await self.page.get_by_role("checkbox").first.is_checked(),
      "optimistic tick must actually be present",
    )
    self.assertTrue(await self.page.get_by_role("checkbox").first.is_disabled())
    self.assertFalse(task.done(), "a tick before the server response is not success")
    self.assertEqual(await self.page.evaluate("fixture.registered.length"), 0)
    self.assertIn("pending", self.journal.data)
    await self.page.wait_for_function("fixture.loadingKind==='registered'")
    self.assertFalse(task.done(), "wait through the registered table loading state")
    self.assertIn("pending", self.journal.data)
    self.assertTrue(await task)
    self.assertEqual(await self.page.evaluate("fixture.clicks['IE.014.02/2']"), 1)
    self.assertNotIn("pending", self.journal.data)

  async def test_delayed_failure_after_optimistic_tick_allows_only_verified_fallback(self):
    await self.use_mode("slow-failure")
    self.site.quiet_seconds = 0.05
    courses, registered, _ = await self.site.snapshot()
    self.assertFalse(
      await self.site.submit(
        academy.choose(courses, registered, self.config)[0], self.journal, self.opening
      )
    )
    self.assertEqual(await self.page.evaluate("fixture.registered.length"), 0)
    self.assertEqual(self.journal.data["blocked"], ["IE.014.02/2"])
    self.assertNotIn("pending", self.journal.data)

  async def test_confirmation_arriving_after_processing_does_not_deadlock(self):
    await self.use_mode("slow-confirm")
    courses, registered, _ = await self.site.snapshot()
    self.assertTrue(
      await self.site.submit(
        academy.choose(courses, registered, self.config)[0], self.journal, self.opening
      )
    )
    self.assertEqual(await self.page.evaluate("fixture.registered.length"), 1)

  async def test_refresh_waits_for_overlay_after_button_stops_spinning(self):
    await self.use_mode("slow-refresh")
    self.site.quiet_seconds = 0.05
    task = asyncio.create_task(self.site.refresh())
    await self.page.wait_for_function(
      "fixture.loadingKind==='open' && !document.querySelector('button[data-kind=open]').hasAttribute('data-loading')"
    )
    self.assertFalse(task.done(), "a stopped button spinner does not mean table data is ready")
    courses, registered, _ = await task
    self.assertEqual(len(courses), 8)
    self.assertEqual(registered, [])
    self.assertEqual(await self.page.evaluate("fixture.loadingKind"), None)

  async def test_refresh_waits_through_transport_retry_gap(self):
    await self.use_mode("refresh-retry")
    courses, _, _ = await self.site.refresh()
    self.assertEqual(len(self.api_calls), 2)
    self.assertEqual(
      courses[0].remaining, 3, "must read the data from the retry, not the stale pre-retry rows"
    )

  async def test_stuck_loading_stops_without_any_click(self):
    await self.use_mode("stuck-loading")
    self.site.busy_seconds = 0.3
    with self.assertRaisesRegex(academy.StopRun, "still loading or processing"):
      await self.site.snapshot()
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})
    self.assertNotIn("pending", self.journal.data)

  async def test_agreement_stops_without_confirming(self):
    await self.use_mode("agreement")
    courses, registered, _ = await self.site.snapshot()
    with self.assertRaises(academy.StopRun):
      await self.site.submit(
        academy.choose(courses, registered, self.config)[0], self.journal, self.opening
      )
    self.assertEqual(await self.page.evaluate("fixture.registered.length"), 0)
    self.assertIn("pending", self.journal.data)

  async def test_unknown_outcome_never_retries(self):
    await self.use_mode("uncertain")
    self.site.verification_seconds = 0.35
    courses, registered, _ = await self.site.snapshot()
    first = academy.choose(courses, registered, self.config)[0]
    with self.assertRaises(academy.StopRun):
      await self.site.submit(first, self.journal, self.opening)
    with self.assertRaises(academy.StopRun):
      await self.site.submit(first, self.journal, self.opening)
    self.assertEqual(await self.page.evaluate("fixture.clicks['IE.014.02/2']"), 1)

  async def test_active_table_search_filter_stops_before_clicking(self):
    await self.page.locator("table").first.evaluate("""table=>{
      const input=document.createElement('input');input.value='hidden rows';table.querySelector('thead').append(input);
    }""")
    with self.assertRaisesRegex(academy.StopRun, "search filters"):
      await self.site.snapshot()
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})

  async def test_registered_count_mismatch_stops_incomplete_table(self):
    await self.page.evaluate("""()=>{
      const p=document.createElement('p');p.textContent='Đã đăng ký: 9 môn (25 TC)';document.body.append(p);
    }""")
    with self.assertRaisesRegex(academy.StopRun, "incomplete"):
      await self.site.snapshot()

  async def test_closed_portal_overrides_enabled_checkbox(self):
    courses, registered, _ = await self.site.snapshot()
    choice = academy.choose(courses, registered, self.config)[0]
    await self.page.evaluate("""()=>{
      const p=document.createElement('p');p.textContent='NGOÀI THỜI GIAN ĐĂNG KÝ';document.body.append(p);
    }""")
    with self.assertRaisesRegex(academy.StopRun, "registration is closed"):
      await self.site.submit(choice, self.journal, self.opening)
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})

  async def test_disappearing_registration_stops_without_restoring_it(self):
    courses, registered, _ = await self.site.snapshot()
    choice = academy.choose(courses, registered, self.config)[0]
    await self.site.submit(choice, self.journal, self.opening)
    await self.page.evaluate("()=>{fixture.registered.splice(0);render()}")
    with self.assertRaisesRegex(academy.StopRun, "selections disappeared"):
      await self.site.snapshot()
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {choice.key: 1})

  async def test_live_row_reorder_still_checks_the_exact_course(self):
    courses, registered, _ = await self.site.snapshot()
    choice = academy.choose(courses, registered, self.config)[0]
    snapshot = self.site.snapshot

    async def reordered_snapshot(*args, **kwargs):
      result = await snapshot(*args, **kwargs)
      await self.page.evaluate("()=>{fixture.data.rows.reverse();render()}")
      return result

    self.site.snapshot = reordered_snapshot
    self.assertTrue(await self.site.submit(choice, self.journal, self.opening))
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {choice.key: 1})

  async def test_catalog_and_advisor_fields_are_read_without_selecting(self):
    await self.page.evaluate("""()=>{
      const section=document.createElement('section');
      section.innerHTML='<h4>KẾT QUẢ DUYỆT CỦA CỐ VẤN HỌC TẬP</h4><p>CHƯA CÓ KẾT QUẢ DUYỆT</p><p>LỜI NHẮN: chưa có</p>';
      document.body.append(section);
    }""")
    courses, registered, raw = await self.site.snapshot()
    self.assertIn("CHƯA CÓ KẾT QUẢ DUYỆT", raw["advisor"])
    self.assertEqual(raw["open"][0]["credits"], "2")
    academy.save_report(Path(self.temp.name), courses, registered, raw, self.config, catalog=True)
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})
    self.assertTrue((Path(self.temp.name) / "courses.csv").exists())

  async def test_changed_student_stops_before_submission(self):
    courses, registered, _ = await self.site.snapshot()
    choice = academy.choose(courses, registered, self.config)[0]
    await self.page.locator("header").evaluate("el=>{el.textContent='OTHER-STUDENT'}")
    with self.assertRaisesRegex(academy.StopRun, "student ID on the page anymore"):
      await self.site.submit(choice, self.journal, self.opening)
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})

  async def test_student_identifier_must_match_a_complete_token(self):
    await self.page.locator("header").evaluate(
      '(el,username)=>{el.textContent=username+"-OTHER"}', self.config["username"]
    )
    with self.assertRaisesRegex(academy.StopRun, "student ID on the page anymore"):
      await self.site.verify_student()

  async def test_cannot_submit_after_configured_window(self):
    courses, registered, _ = await self.site.snapshot()
    with self.assertRaisesRegex(academy.StopRun, "settings has ended"):
      await self.site.submit(
        courses[0], self.journal, datetime.now(timezone.utc) - timedelta(minutes=21)
      )
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})

  async def test_cannot_submit_before_opening(self):
    courses, registered, _ = await self.site.snapshot()
    with self.assertRaises(academy.StopRun):
      await self.site.submit(courses[0], self.journal, datetime.now(timezone.utc) + timedelta(hours=1))
    self.assertEqual(await self.page.evaluate("fixture.clicks"), {})


if __name__ == "__main__":
  unittest.main()
