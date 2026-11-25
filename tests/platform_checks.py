"""Assertions for private files and link handling on supported systems."""

import os
from contextlib import closing


def assert_private(test, path, mode):
  if os.name != "nt":
    test.assertEqual(path.stat().st_mode & 0o777, mode)
    return

  import ntsecuritycon
  import win32con
  import win32file
  import win32security

  from academy_registration.windows_files import current_user

  with closing(
    win32file.CreateFile(
      str(path),
      ntsecuritycon.READ_CONTROL,
      win32con.FILE_SHARE_READ,
      None,
      win32con.OPEN_EXISTING,
      win32con.FILE_FLAG_BACKUP_SEMANTICS,
      None,
    )
  ) as handle:
    descriptor = win32security.GetSecurityInfo(
      handle, win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
  acl = descriptor.GetSecurityDescriptorDacl()
  test.assertIsNotNone(acl)
  test.assertGreater(acl.GetAceCount(), 0)
  user = current_user()
  system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
  granted = []
  for index in range(acl.GetAceCount()):
    header, _, sid = acl.GetAce(index)
    test.assertEqual(header[0], win32security.ACCESS_ALLOWED_ACE_TYPE)
    test.assertIn(sid, (user, system) if path.is_dir() else (user,))
    granted.append(sid)
  test.assertIn(user, granted)


def symlink_or_skip(test, path, target, **kwargs):
  try:
    path.symlink_to(target, **kwargs)
  except OSError as error:
    if os.name == "nt" and getattr(error, "winerror", None) == 1314:
      test.skipTest("Windows symlink creation needs Developer Mode or the symlink privilege")
    raise
