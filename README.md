# Course registration

This is a small Python tool for checking and registering courses at the academy. You can see what fits before you submit anything. It uses Python 3.10 or newer and a Chromium-based browser. The tool has file handling for Linux, macOS, and native Windows 11. The test workflow runs on all three.

That portal is Claude slop, so I had to defeat it with my hindsight™ and some GPT slop. Full disclosure, this project was assisted in part by OpenAI Codex using GPT 6.2 Sol.

## Set it up

The terminal and desktop launchers check their Python packages on startup and run pip if anything is missing or out of date. Running from a virtual environment keeps the install with this app.

On Linux or macOS, run these from the source folder.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
academy-register --configure
```

On Windows, open PowerShell in the source folder and use the executables inside the virtual environment.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\academy-register.exe --configure
```

For the commands below, use `.\.venv\Scripts\academy-register.exe` in PowerShell. If you run unattended, set `ACADEMY_PASSWORD` in the PowerShell process that launches the tool. Use a local folder you own for settings, run files, and evidence. Choose a Windows filesystem with access permissions, such as NTFS.

The setup asks when registration opens in Hanoi, what courses you want, and which groups you'd take as fallbacks. You'll see the whole plan before you save it. This part runs offline and only saves settings.

Here's what `preferences.txt` looks like.

```text
Student: YOUR_STUDENT_ID
Semester: Học kỳ 2 - Năm học 2026 - 2027
Opens: 2027-01-01 08:00 Hanoi
Refresh: 15 seconds
Window: 20 minutes

Course: IE.014.02 | Quản trị Kinh doanh
Group: 2 | Friday 10-12
Fallback: 1 | Friday 7-9

Course: IE.081.03 | Quản trị tài chính quốc tế
Group: 1 | Monday 7-9, Tuesday 7-9
```

Edit the dates and courses to match your semester. You can leave off the course name after `|`. Blank lines and lines starting with `#` are fine. Write days as `Monday`, `Mon`, `Thứ 2`, or `2`. Give the class periods as shown on the portal. If a group really is your last resort, mark it `Last resort:`. List groups in the order you want them.

```sh
academy-register --check-config
academy-register --demo
```

Both commands run offline once the Python packages are installed. JSON works too. If you have both `preferences.txt` and `preferences.json`, pick one with `--config`.

```sh
academy-register --init-config
academy-register --init-config --config plan.json
```

These give you a starter file to edit. If the file already exists, pick another name. From the source folder, use `python academy_register.py` or `python -m academy_registration` with the same flags.

The planner tries to fit as many of your courses as possible. It uses fewer last-resort groups when it can, then goes by the order you gave it. It checks the dates too, so periods that overlap in different parts of the semester can still fit. Courses you already have selected stay selected, even if you left them out of your settings.

## Check what's available

```sh
academy-register
academy-register --catalog
```

The first command checks your plan. The second shows the whole course list the portal currently shows, including courses with disabled checkboxes. Enter your password at the hidden prompt, or set `ACADEMY_PASSWORD` for an unattended run. Keep the password out of your settings and command arguments. Use `--headless` if you want the browser hidden, or `--browser-executable` followed by a path to pick a specific browser.

Playwright starts an installed browser if it can. On Windows it tries Chrome, Edge, then Brave. On Linux it tries Chrome, Ungoogled Chromium (including packages that use the `chromium` executable name), then Brave. On macOS it looks for Chrome in Applications. If none starts, it tries the copy of Chromium in Playwright's cache. The app downloads Chromium only when it detects no supported browser and the cache is missing the version it needs. An explicit `--browser-executable` takes precedence and reports a launch failure directly. For a portable install or a browser with a different command name, pass its executable path.

Each browser run prints the path to its own private folder under `reports/`. Use `--evidence-dir` if you want those folders somewhere else. Here's what you can find in a run folder.

- `report.json` has the courses, registrations, advisor feedback, and what the planner picked or skipped.
- `courses.csv` is the course list for a spreadsheet.
- `timetable.txt` has your registered classes with teaching dates and periods.
- `result.json` tells you how the run ended. When screenshots are available, a submission run saves `login-verified.png` and `registration-verified.png`.
- `failure.json` records the kind of error if the run fails or gets interrupted.

These files can contain student info. Keep them private. Log timestamps are in UTC, and the opening time is also shown in Hanoi time.

## Desktop window

After installing the tool, run this from the folder where you keep your settings:

```sh
academy-register-gui
```

From a source checkout you can also run `python -m academy_registration.gui`. The desktop window needs Tkinter. If your Python build doesn't include it, install Tk support for that Python. On Windows, the launcher is `.\.venv\Scripts\academy-register-gui.exe` if you're using the virtual environment above.

Open a text or JSON settings file, or edit the student, semester, opening time, and course choices in the window. The GUI saves settings as text. For **Suggest schedule**, enter your student ID and password. The tool signs in, refreshes the portal’s course list, and applies your filters. The semester is optional for a preview. Leave it blank to use the current semester on the portal. To schedule registration, fill in your semester, opening time, and course choices. Pick a week to see its timetable, including differences between teaching blocks. **Use suggested groups in settings** copies the suggested additions into the course editor for review.

**Schedule registration** asks you to confirm your course list and the opening time in Hanoi. It uses the courses in the settings editor. Keep the window open and the computer awake until the run finishes. **Cancel run** stops the worker and closes its browser. If it already sent a submission, check the portal before retrying. The GUI shares the CLI's run journal when both start from the same folder. The run log shows where the evidence was saved. Enter your school password in the masked field below Student ID. The window clears the field when a run starts and keeps it out of saved settings. Leave it blank to use `ACADEMY_PASSWORD` if you've set it.

## Suggest a schedule

```sh
academy-register --suggest days
academy-register --suggest fatigue
```

Both commands read all enabled groups with seats in the portal's current course list, even if they aren't in your preferences. They keep existing selections fixed and fit as many different subjects as possible before optimising. They print the groups and teaching dates and save the same proposal as `suggested-schedule.txt` in the run folder. Nothing is submitted. You can leave the course list empty in your settings for these commands. The tool still needs the account, semester, and opening settings to check the portal and use the right run folder.

- `days` minimises actual days at school across the term. Fatigue breaks ties.
- `fatigue` adds up skipped sessions and blocks of three consecutive periods. Sessions are periods 1–3, 4–6, 7–9, and so on. An empty session counts only if there are classes before and after it that day. A six-period stretch counts as four overlapping blocks of three, so longer stretches cost more. Fewer gaps, a shorter longest stretch, and fewer school days break ties, in that order.

To limit what gets suggested, pass subject codes from the portal:

```sh
academy-register --suggest days --include IE.014.02 IE.068.02
academy-register --suggest fatigue --exclude IE.014.02 IE.068.02
```

You can repeat either flag or use both. An excluded subject stays out of the proposed additions even if it's also included. The filters match subject codes regardless of case and apply to all groups of that subject. Anything you've already selected stays in the timetable and still counts toward conflicts, school days, and fatigue.

Teaching dates matter. A break between teaching blocks doesn't count as a school day, and two groups that meet on the same weekday and periods can fit if they meet on different dates. The proposal reflects current seat counts and the visible course list. The portal may still enforce credit limits or other rules. If the search hits its limit, the output says the result is the best it found so far.

## Register

```sh
academy-register --submit
```

Leave your computer awake and connected. Run one copy per account. The tool checks your session five minutes before opening and starts refreshing one minute before opening. The shortest refresh interval is 15 seconds.

It submits one course at a time and refreshes the registered list to confirm the result. If the portal says there's no seat and the tool verifies that, it'll try a fallback that fits. It gives a slow request up to 120 seconds. If a selection disappears, the page is filtered or incomplete, the student ID changes, or the result is unclear, it stops so you can check the portal. Handle CAPTCHAs, one-time codes, agreements, and payment prompts yourself.

Keep the `.academy-run` folder between runs. It holds the account lock and remembers if a submission is still pending. Changing the opening date won't clear that entry. Check the portal before clearing it yourself. Runs using different state folders won't share a lock.

If you already have a run folder from an earlier version, move it to `.academy-run` before submitting or pass its existing path with `--state-dir`. This keeps pending submissions and the account lock together. Set `ACADEMY_PASSWORD` again if you sign in through an environment variable.

Exit code `0` means the check or registration finished, `2` means it stopped before finishing, and `130` means you interrupted it. If a screenshot fails to save, check the final registration status before retrying.

## Work on the code

```sh
python -m pip install '.[dev]'
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
ACADEMY_BROWSER_TESTS=1 python -m unittest discover -s tests -v
python -m build
```

Browser tests use local fixtures. They use the same browser detection and Playwright Chromium fallback as the CLI. Set `ACADEMY_TEST_BROWSER` to use a different executable. The other tests run offline without Playwright.

Start in `cli.py` for the flow of a run, `preferences.py` for setup and the text format, `core.py` for validation and scheduling, `portal.py` for page controls, or `files.py` for reports and run state. The planner skips combinations as soon as they clash and reuses comparisons within each plan. Big course lists can still take a while.

MIT licensed. See [Contributing](CONTRIBUTING.md) and [Security](SECURITY.md).
