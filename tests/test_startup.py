import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_registration import cli, gui, startup
from academy_registration.core import StopRun


class StartupTests(unittest.TestCase):
  def test_install_uses_bundled_requirements_and_can_be_skipped_on_next_launch(self):
    versions = {}

    def installed_version(name):
      if name not in versions:
        raise startup.metadata.PackageNotFoundError(name)
      return versions[name]

    def install(command, *, check, cwd):
      self.assertTrue(check)
      self.assertEqual(command[:4], [sys.executable, "-m", "pip", "install"])
      requirements = Path(command[command.index("-r") + 1])
      self.assertEqual(requirements.parent, Path(startup.__file__).parent)
      self.assertEqual(cwd, requirements.parent)
      self.assertIn("playwright==", requirements.read_text(encoding="utf-8"))
      versions.update(playwright="1.65.2")

    announce = Mock()
    with (
      patch.object(sys, "platform", "linux"),
      patch.object(startup.metadata, "version", side_effect=installed_version),
      patch.object(startup.subprocess, "run", side_effect=install) as pip,
    ):
      startup.ensure_requirements(announce)
      startup.ensure_requirements(announce)
    announce.assert_called_once()
    pip.assert_called_once()

  def test_windows_checks_versions_and_enables_newly_installed_win32_modules(self):
    distribution = Mock()
    distribution.locate_file.return_value = Path("test-site-packages")
    with (
      patch.object(sys, "platform", "win32"),
      patch.object(startup.site, "ENABLE_USER_SITE", False),
      patch.object(
        startup.metadata, "version", side_effect=["old", "2026.1", "305", "1.65.2", "2026.1", "306"]
      ),
      patch.object(startup.metadata, "distribution", return_value=distribution),
      patch.object(startup.site, "addsitedir") as add_site,
      patch.object(startup.subprocess, "run") as pip,
    ):
      startup.ensure_requirements(Mock())
    pip.assert_called_once()
    add_site.assert_called_once_with("test-site-packages")

  def test_failed_install_stops_cli_before_creating_settings(self):
    with (
      tempfile.TemporaryDirectory() as folder,
      patch.object(startup, "missing_requirements", return_value=["playwright"]),
      patch.object(startup.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")),
      self.assertLogs(cli.LOG, level="ERROR") as log,
    ):
      settings = Path(folder, "preferences.txt")
      self.assertEqual(cli.main(["--init-config", "--config", str(settings)]), 2)
      self.assertFalse(settings.exists())
    self.assertIn("Couldn't install the required Python packages", log.output[0])

  def test_pip_output_becomes_progress_messages(self):
    process = Mock()
    process.stdout = [
      "Looking in indexes: https://private.example/",
      "Collecting playwright",
      "Downloading playwright",
      "Downloading dependency",
      "Installing collected packages: playwright",
    ]
    process.wait.return_value = 1
    messages = []
    with patch.object(startup.subprocess, "Popen") as launch:
      launch.return_value.__enter__.return_value = process
      with self.assertRaises(subprocess.CalledProcessError):
        startup.install_with_progress([sys.executable, "-m", "pip"], messages.append)
    self.assertEqual(
      messages, ["Finding packages...", "Downloading packages...", "Installing packages..."]
    )
    self.assertEqual(launch.call_args.kwargs["stderr"], subprocess.STDOUT)

  def test_failed_install_is_shown_in_desktop_window(self):
    with (
      patch.object(gui, "tk") as tk,
      patch.object(gui, "ttk", create=True),
      patch.object(gui, "messagebox", create=True) as messagebox,
      patch.object(gui, "ensure_requirements", side_effect=StopRun("Couldn't install.")),
      patch.object(gui, "Thread") as thread,
      patch.object(gui, "App") as app,
    ):
      thread.side_effect = lambda *, target: Mock(start=target)
      root = tk.Tk.return_value
      root.mainloop.side_effect = lambda: root.after.call_args.args[1]()
      self.assertEqual(gui.main(), 2)
    app.assert_not_called()
    root = tk.Tk.return_value
    messagebox.showerror.assert_called_once_with(
      "Academy Registration", "Couldn't install.", parent=root
    )
    root.destroy.assert_called_once()

  def test_desktop_updates_the_progress_screen_before_opening_the_app(self):
    def install(announce, *, on_progress):
      announce("Starting installation.")
      on_progress("Downloading packages...")

    with (
      patch.object(gui, "tk") as tk,
      patch.object(gui, "ttk", create=True) as ttk,
      patch.object(gui, "ensure_requirements", side_effect=install),
      patch.object(gui, "Thread") as thread,
      patch.object(gui, "App") as app,
    ):
      thread.side_effect = lambda *, target: Mock(start=target)
      root = tk.Tk.return_value
      root.mainloop.side_effect = lambda: root.after.call_args.args[1]()
      ttk.Frame.return_value.winfo_manager.side_effect = ["", "pack"]
      self.assertEqual(gui.main(), 0)
    messages = [call.kwargs["text"] for call in ttk.Label.return_value.configure.call_args_list]
    self.assertEqual(messages, ["Starting installation.", "Downloading packages..."])
    ttk.Progressbar.return_value.start.assert_called_once()
    ttk.Progressbar.return_value.stop.assert_called_once()
    app.assert_called_once()
    self.assertIs(app.call_args.args[0], root)
    self.assertEqual(app.call_args.args[1].language, "English")
