# Security policy for the registration tool

This is a side project for registering courses at the academy. There's no bug bounty, but I care about fixing security issues and I'd rather hear about a problem from you than find it in my logs.

## Scope

Report issues in this tool, including how it signs in, reads the portal, submits courses, and handles settings, run files, screenshots, and reports. The source and release packages are in scope too.

If the issue is in the academy’s portal, the browser, or a dependency, please report it to whoever maintains that first. You can still let me know afterwards if there's something I can do on my end. For ordinary bugs, feature requests, and hardening suggestions without a clear security impact, open a normal issue.

## Supported versions

Please check the latest release or current source. If you found the issue on an older version, include that version and say whether you could reproduce it on the latest one.

## Reporting a vulnerability

Please don't open a public issue for anything that could be exploited before a fix is out. Email me privately at [security@jarema.me](mailto:security@jarema.me) or message me on Signal at `@jarema.26`. You can encrypt your email with my [PGP key](https://keys.openpgp.org/vks/v1/by-fingerprint/D5A8DEAE823F6478185C39879BB802C53628AE75).

> **PGP key fingerprint:** `D5A8 DEAE 823F 6478 185C 3987 9BB8 02C5 3628 AE75`

Please include:

- What happened and what someone could do with it.
- The tool version or commit, your OS, and the browser version if it matters.
- Steps to reproduce it with fake data. A local fixture or a small proof of concept is fine.

Leave out passwords, session cookies, real student IDs, and unredacted screenshots or reports. Please use local fixtures to test anything that would change a real registration.

## What the tool stores

Enter your password at the hidden prompt or set `ACADEMY_PASSWORD`. It stays in memory while the tool runs and goes to the academy’s login page. Keep it out of settings, shell history, and debug logs. The tool doesn't save browser profiles or cookies in its reports.

On Linux and macOS, settings and evidence files use mode `0600` and run and report folders use `0700`. On Windows, the tool uses access lists that grant access to the current Windows user. Keep these files in a local folder you own on a filesystem that supports access lists. The tool saves pending submissions to disk before clicking and replaces report and journal files atomically. Account locks reject symbolic links on Unix and reparse points on Windows. Windows also rejects reparse points at the destination when replacing a report or journal file. Someone with access to your OS account or admin rights can still read your files. A compromised browser or school portal can still see what you send it.

Browser runs save screenshots and reports automatically. These can show your student ID and timetable. Keep them, your local settings, environment files, and `.academy-run` out of public issues and release archives.

If the portal shows something unexpected or the tool can't confirm a submission, it stops for you to check. Handle login challenges, agreements, and payment prompts in the browser yourself.

## Response

I maintain this alongside university, so turnaround depends on severity. I'll prioritise issues that expose credentials or student data, or let the tool change a registration it wasn't supposed to touch. I'll credit you if you want that.

Thanks for taking the time to look at this!
