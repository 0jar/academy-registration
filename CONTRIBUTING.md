# Contributing

Use Python 3.10 or newer and two spaces for indentation. Keep the browser stuff in `portal.py` so the planner can run on its own. Keep the registration flow easy to follow.

Run the checks in the README. If you're adding a browser test, use a local fixture and block external requests. For planner changes, compare a small schedule against the exhaustive reference.

A few things the tool relies on:

- A run only submits courses if you pass `--submit`.
- Leave anything already selected alone.
- Save a pending entry before clicking to register. If the result is unclear, stop and check the portal.
- Confirm the course on the refreshed registered list before calling it done.
- Keep credentials, personal settings, reports, and run state out of anything you share.

For a PR, say what changed, what you tested, and what still needs checking. Use fake account details in issues and fixtures.
