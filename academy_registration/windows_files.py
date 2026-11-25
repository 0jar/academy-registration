"""Windows file permissions and locking, using native file handles."""

from contextlib import closing, suppress
from pathlib import Path

import ntsecuritycon
import pywintypes
import win32api
import win32con
import win32file
import win32security
import winerror

from .core import StopRun


def current_user():
  with closing(
    win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
  ) as token:
    return win32security.GetTokenInformation(token, win32security.TokenUser)[0]


def current_token_owner():
  with closing(
    win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
  ) as token:
    return win32security.GetTokenInformation(token, win32security.TokenOwner)


def private_attributes(user, *, directory=False):
  flags = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE if directory else 0
  acl = win32security.ACL()
  acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, user)
  attributes = pywintypes.SECURITY_ATTRIBUTES()
  attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorOwner(user, False)
  attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorDacl(True, acl, False)
  protected = win32security.SE_DACL_PROTECTED
  attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorControl(protected, protected)
  return attributes


def private_directory(folder):
  folder = Path(folder)
  folder.parent.mkdir(parents=True, exist_ok=True)
  user = current_user()
  attributes = private_attributes(user, directory=True)
  try:
    win32file.CreateDirectory(str(folder), attributes)
  except pywintypes.error as error:
    if error.winerror != winerror.ERROR_ALREADY_EXISTS:
      raise

  access = ntsecuritycon.READ_CONTROL | ntsecuritycon.WRITE_DAC | ntsecuritycon.FILE_READ_ATTRIBUTES
  with closing(
    win32file.CreateFile(
      str(folder),
      access,
      win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
      None,
      win32con.OPEN_EXISTING,
      win32con.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT,
      None,
    )
  ) as handle:
    flags = win32file.GetFileInformationByHandleEx(handle, win32file.FileAttributeTagInfo)[
      "FileAttributes"
    ]
    if flags & win32con.FILE_ATTRIBUTE_REPARSE_POINT or not flags & win32con.FILE_ATTRIBUTE_DIRECTORY:
      raise StopRun("Pick a private folder you own. Use the folder's path directly.")
    descriptor = win32security.GetSecurityInfo(
      handle,
      win32security.SE_FILE_OBJECT,
      win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    if descriptor.GetSecurityDescriptorOwner() not in (user, current_token_owner()):
      raise StopRun("Pick a private folder owned by your Windows account.")
    current = descriptor.GetSecurityDescriptorDacl()
    desired = attributes.SECURITY_DESCRIPTOR.GetSecurityDescriptorDacl()
    control, _ = descriptor.GetSecurityDescriptorControl()
    if (
      current is None
      or current.GetAceCount() != 1
      or current.GetAce(0) != desired.GetAce(0)
      or not control & win32security.SE_DACL_PROTECTED
    ):
      win32security.SetSecurityInfo(
        handle,
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        desired,
        None,
      )
  return folder


def create_private_file(path, content):
  try:
    handle = win32file.CreateFile(
      str(path),
      win32con.GENERIC_WRITE,
      0,
      private_attributes(current_user()),
      win32con.CREATE_NEW,
      win32con.FILE_ATTRIBUTE_NORMAL,
      None,
    )
  except pywintypes.error as error:
    if error.winerror in (winerror.ERROR_FILE_EXISTS, winerror.ERROR_ALREADY_EXISTS):
      raise FileExistsError(str(path)) from error
    raise
  try:
    with closing(handle):
      _, written = win32file.WriteFile(handle, content)
      if written != len(content):
        raise OSError("Could not write the full file.")
      win32file.FlushFileBuffers(handle)
  except BaseException:
    with suppress(OSError):
      Path(path).unlink(missing_ok=True)
    raise


def open_account_lock(path):
  try:
    handle = win32file.CreateFile(
      str(path),
      win32con.GENERIC_READ | win32con.GENERIC_WRITE,
      0,
      private_attributes(current_user()),
      win32con.OPEN_ALWAYS,
      win32file.FILE_FLAG_OPEN_REPARSE_POINT,
      None,
    )
  except pywintypes.error as error:
    if error.winerror == winerror.ERROR_SHARING_VIOLATION:
      raise BlockingIOError from error
    raise
  try:
    flags = win32file.GetFileInformationByHandleEx(handle, win32file.FileAttributeTagInfo)[
      "FileAttributes"
    ]
    if flags & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
      raise OSError("The account lock must be a regular file.")
  except BaseException:
    handle.Close()
    raise
  return handle


def replace_file(source, destination):
  try:
    attributes = Path(destination).lstat().st_file_attributes
  except FileNotFoundError:
    pass
  else:
    if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
      raise OSError("Pick a regular output file. The destination is a link or reparse point.")
  win32file.MoveFileEx(
    str(source),
    str(destination),
    win32file.MOVEFILE_REPLACE_EXISTING | win32file.MOVEFILE_WRITE_THROUGH,
  )
