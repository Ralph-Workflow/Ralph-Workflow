"""Process-level protection for the MCP broker secret."""

from __future__ import annotations

import ctypes
import os
import secrets
import sys
from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents.invoke import _parent_broker_secret
from ralph.checked_accessors import as_int

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping


class _Prctl(Protocol):
    def __call__(self, option: int, value: int) -> int: ...


class _GetCurrentProcess(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self) -> object: ...


class _NativeFunction(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, *args: object) -> int: ...


class _WindowsLibraryLoader(Protocol):
    def __call__(self, name: str, *, use_last_error: bool) -> ctypes.CDLL: ...


def _protect_darwin_process() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    raw_result: object = libc.ptrace(31, 0, None, 0)
    if as_int(raw_result, field="ptrace result") != 0:
        raise RuntimeError("MCP broker secret process protection failed")


def _protect_windows_process() -> None:
    acl_revision = 2
    dacl_security_information = 0x00000004
    se_kernel_object = 6
    acl = ctypes.create_string_buffer(8)
    windows_library_loader = cast(
        "_WindowsLibraryLoader", getattr(ctypes, "".join(("Win", "DLL")))
    )
    advapi32 = windows_library_loader("advapi32", use_last_error=True)
    kernel32 = windows_library_loader("kernel32", use_last_error=True)
    initialize_acl = cast(
        "_NativeFunction", advapi32.InitializeAcl
    )  # cast-policy: seam: ctypes resolves Win32 functions dynamically
    set_security_info = cast(
        "_NativeFunction", advapi32.SetSecurityInfo
    )  # cast-policy: seam: ctypes resolves Win32 functions dynamically
    get_current_process = cast(
        "_GetCurrentProcess", kernel32.GetCurrentProcess
    )  # cast-policy: seam: ctypes resolves Win32 functions dynamically
    initialize_acl.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    initialize_acl.restype = ctypes.c_int
    set_security_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    set_security_info.restype = ctypes.c_ulong
    get_current_process.argtypes = []
    get_current_process.restype = ctypes.c_void_p
    if not initialize_acl(ctypes.byref(acl), len(acl), acl_revision):
        raise RuntimeError("MCP broker secret process protection failed")
    result = set_security_info(
        as_int(get_current_process(), field="process handle"),
        se_kernel_object,
        dacl_security_information,
        None,
        None,
        ctypes.byref(acl),
        None,
    )
    if as_int(result, field="SetSecurityInfo result") != 0:
        raise RuntimeError("MCP broker secret process protection failed")


def protect_broker_secret(
    *,
    env_getter: Callable[[str], str | None] = os.getenv,
    platform: str = sys.platform,
    prctl: _Prctl | None = None,
    darwin_protect: Callable[[], None] = _protect_darwin_process,
    windows_protect: Callable[[], None] = _protect_windows_process,
) -> None:
    if not env_getter("RALPH_BROKER_SECRET"):
        return
    if platform == "darwin":
        darwin_protect()
        return
    if platform == "win32":
        windows_protect()
        return
    if platform != "linux":
        raise RuntimeError(f"Unsupported platform for broker secret protection: {platform}")
    if prctl is None:
        libc = ctypes.CDLL(None, use_errno=True)
        raw_result: object = libc.prctl(4, 0)
        result = as_int(raw_result, field="prctl result")
    else:
        result = prctl(4, 0)
    if result != 0:
        raise RuntimeError("MCP broker secret process protection failed")


def mint_and_protect_broker_secret(
    *,
    environment: MutableMapping[str, str] | None = None,
    token_factory: Callable[[int], str] = secrets.token_hex,
    protector: Callable[[], None] = protect_broker_secret,
) -> str:
    """Replace any inherited broker secret and protect its owning process."""
    target = os.environ if environment is None else environment
    secret = token_factory(32)
    target["RALPH_BROKER_SECRET"] = secret
    protector()
    return secret


def erase_broker_secret_environment() -> None:
    secret = _parent_broker_secret()
    if not secret:
        return
    if os.name == "posix":
        libc = ctypes.CDLL(None)
        environ = ctypes.POINTER(ctypes.c_void_p).in_dll(libc, "environ")
        prefix = b"RALPH_BROKER_SECRET="
        encoded_secret = secret.encode()
        index = 0
        address = as_int(cast("object", environ[index]), field="environment address")
        while address:
            if ctypes.string_at(address, len(prefix)) == prefix:
                ctypes.memset(address + len(prefix), 0, len(encoded_secret))
                break
            index += 1
            address = as_int(cast("object", environ[index]), field="environment address")
    os.environ.pop("RALPH_BROKER_SECRET", None)
