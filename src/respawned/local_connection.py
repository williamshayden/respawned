"""Private, per-engine CLI credentials for a locally launched server."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from urllib.parse import urlsplit

FORMAT = "respawned-local-client-v1"


def _state_directory() -> Path:
    override = os.environ.get("RESPAWNED_STATE_DIR")
    if override:
        base = Path(override).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Respawned"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "respawned"
    return base / "connections"


def _windows_security(path: Path, *, secure: bool) -> None:
    """Require current-user ownership and an ACL limited to this user and SYSTEM."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(pointer)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer]
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(pointer),
        ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.EqualSid.argtypes = [pointer, pointer]
    advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    advapi.GetAclInformation.argtypes = [pointer, pointer, wintypes.DWORD, ctypes.c_int]

    token = wintypes.HANDLE()
    descriptor, replacement, owner, acl, system_sid = (pointer() for _ in range(5))
    sid_string = wintypes.LPWSTR()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        length = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        buffer = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, buffer, length.value, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        current_sid = ctypes.cast(buffer, ctypes.POINTER(pointer))[0]
        result = advapi.GetNamedSecurityInfoW(str(path), 1, 0x00000001 | 0x00000004,
            ctypes.byref(owner), None, ctypes.byref(acl), None, ctypes.byref(descriptor))
        if result:
            raise ctypes.WinError(result)
        if not owner or not advapi.EqualSid(owner, current_sid):
            raise ValueError("Local connection storage must belong to this user")
        if secure:
            if not advapi.ConvertSidToStringSidW(current_sid, ctypes.byref(sid_string)):
                raise ctypes.WinError(ctypes.get_last_error())
            sddl = f"D:P(A;;FA;;;{sid_string.value})(A;;FA;;;SY)"
            if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(replacement), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not advapi.SetFileSecurityW(str(path), 0x00000004 | 0x80000000, replacement):
                raise ctypes.WinError(ctypes.get_last_error())
            return

        if not acl:
            raise ValueError("Local connection storage must have a private access list")
        if not advapi.ConvertStringSidToSidW("S-1-5-18", ctypes.byref(system_sid)):
            raise ctypes.WinError(ctypes.get_last_error())

        class AclSize(ctypes.Structure):
            _fields_ = [("count", wintypes.DWORD), ("used", wintypes.DWORD), ("free", wintypes.DWORD)]

        info = AclSize()
        if not advapi.GetAclInformation(acl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        for index in range(info.count):
            ace = pointer()
            if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            kind = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[0]
            if kind == 1:  # A deny ACE cannot grant additional access.
                continue
            if kind != 0:
                raise ValueError("Unexpected access rule on local connection storage")
            allowed_sid = pointer(ace.value + 8)
            if not (advapi.EqualSid(allowed_sid, current_sid) or advapi.EqualSid(allowed_sid, system_sid)):
                raise ValueError("Local connection storage must be private to this user")
    finally:
        for allocated in (descriptor, replacement, system_sid):
            if allocated:
                kernel.LocalFree(allocated)
        if sid_string:
            kernel.LocalFree(ctypes.cast(sid_string, pointer))
        kernel.CloseHandle(token)


def _check_owned(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or (directory and not stat.S_ISDIR(info.st_mode))):
        raise ValueError("Local connection storage must not be a symlink")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise ValueError("Local connection must be a regular file")
    if os.name == "nt":
        _windows_security(path, secure=False)
    else:
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Local connection storage must be private to this user")


def _local_origin(api_url: object) -> str | None:
    """Canonicalize only root HTTP URLs for the exact local engine address."""
    if (not isinstance(api_url, str)
            or any(ord(char) < 33 or ord(char) > 126 for char in api_url)
            or any(char in api_url for char in "?#\\")):
        return None
    try:
        parsed = urlsplit(api_url)
        port = parsed.port if parsed.port is not None else 80
    except ValueError:
        return None
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.username is not None or parsed.password is not None):
        return None
    if not 1 <= port <= 65535:
        return None
    return "http://127.0.0.1" + (f":{port}" if port != 80 else "")


def _connection_path(api_url: str) -> Path | None:
    origin = _local_origin(api_url)
    if origin is None:
        return None
    port = urlsplit(origin).port or 80
    return _state_directory() / f"{port}.json"


def read_local_token(api_url: str) -> str | None:
    """Never read or forward a local capability for another origin or base path."""
    path = _connection_path(api_url)
    if path is None or not path.exists():
        return None
    _check_owned(path.parent, directory=True)
    _check_owned(path)
    if path.stat().st_size > 8192:
        raise ValueError("Invalid local connection file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid local connection file")
    if (payload.get("format") != FORMAT or _local_origin(payload.get("url")) != _local_origin(api_url)
            or not isinstance(payload.get("token"), str) or not payload["token"]):
        raise ValueError("Invalid local connection file")
    return payload["token"]


@contextmanager
def publish_local_token(api_url: str, token: str):
    """Publish only after the server has bound its port; remove only our file."""
    path = _connection_path(api_url)
    if path is None:
        raise ValueError("Automatic CLI access is restricted to exact loopback engines")
    origin = _local_origin(api_url)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = parent.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Local connection storage must not be a link")
    if os.name == "nt":
        _windows_security(parent, secure=True)
    _check_owned(parent, directory=True)
    if path.exists() or path.is_symlink():
        _check_owned(path)
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("Refusing to overwrite unrelated local connection data")
        if existing.get("format") != FORMAT or _local_origin(existing.get("url")) != origin:
            raise ValueError("Refusing to overwrite unrelated local connection data")
    instance = secrets.token_hex(16)
    payload = {"format": FORMAT, "url": origin, "token": token,
               "instance": instance, "pid": os.getpid()}
    descriptor, temporary = tempfile.mkstemp(prefix=".connection-", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if os.name == "nt":
                _windows_security(Path(temporary), secure=True)
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path = Path(temporary)
        os.replace(temporary_path, path)
        yield path
    finally:
        Path(temporary).unlink(missing_ok=True)
        try:
            if path.exists() and not path.is_symlink():
                _check_owned(path)
                saved = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and saved.get("instance") == instance:
                    path.unlink()
        except (OSError, ValueError):
            # Changed or unreadable storage is no longer ours to remove.
            pass
