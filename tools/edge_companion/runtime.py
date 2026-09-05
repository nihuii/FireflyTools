"""Discover a live Edge capture receiver through a local runtime descriptor."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Callable


RUNTIME_TTL_SECONDS = 600
_PROTOCOL_VERSION = 1
_DESCRIPTOR_FIELDS = {
    "port",
    "token",
    "pid",
    "protocol_version",
    "expires_at",
}
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS = {}


@dataclass(frozen=True)
class RuntimeDescriptor:
    """Describe one short-lived authenticated loopback receiver."""

    port: int
    token: str
    pid: int
    protocol_version: int
    expires_at: datetime

    def to_dict(self) -> dict:
        """Return the descriptor's compact JSON-compatible representation."""
        if (
            not isinstance(self.expires_at, datetime)
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at 必须是带时区的日期时间")
        expires_at = self.expires_at.astimezone(timezone.utc).isoformat()
        if expires_at.endswith("+00:00"):
            expires_at = expires_at[:-6] + "Z"
        return {
            "port": self.port,
            "token": self.token,
            "pid": self.pid,
            "protocol_version": self.protocol_version,
            "expires_at": expires_at,
        }


def default_runtime_path() -> Path:
    """Return the per-user Edge capture runtime descriptor path."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA 未设置，无法定位运行时描述文件")
    return (
        Path(local_app_data)
        / "FireflyTools"
        / "runtime"
        / "edge_capture.json"
    )


@contextmanager
def _runtime_descriptor_lock(path: Path):
    """Hold the stable same-directory OS lock for one descriptor operation."""
    lock_path = path.with_name(path.name + ".lock")
    lock_key = os.path.normcase(os.path.abspath(lock_path))
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(lock_key, threading.RLock())

    process_lock.acquire()
    lock_handle = None
    acquired = False
    try:
        try:
            lock_handle = lock_path.open("a+b")
        except OSError as exc:
            raise RuntimeError("无法打开运行时描述文件锁") from exc
        if os.name == "nt":
            import msvcrt

            lock_handle.seek(0, os.SEEK_END)
            if lock_handle.tell() == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()
            lock_handle.seek(0)
            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            except OSError as exc:
                raise RuntimeError("无法获取运行时描述文件锁") from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise RuntimeError("无法获取运行时描述文件锁") from exc
        acquired = True
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if lock_handle is not None:
            lock_handle.close()
        process_lock.release()


def write_runtime_descriptor(path: Path, descriptor: RuntimeDescriptor) -> None:
    """Atomically replace ``path`` with ``descriptor`` encoded as UTF-8 JSON."""
    runtime_path = Path(path)
    try:
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            descriptor.to_dict(), ensure_ascii=False, separators=(",", ":")
        )
        with _runtime_descriptor_lock(runtime_path):
            _replace_runtime_descriptor_locked(runtime_path, serialized)
    except OSError as exc:
        raise RuntimeError("无法写入运行时描述文件") from exc


def _replace_runtime_descriptor_locked(
    runtime_path: Path,
    serialized: str,
) -> None:
    """Atomically replace a descriptor while its OS lock is already held."""
    temporary_path = None
    file_descriptor = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=runtime_path.name + ".",
            suffix=".tmp",
            dir=runtime_path.parent,
        )
        temporary_path = Path(temporary_name)
        raw_handle = os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        )
        file_descriptor = None
        with raw_handle as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, runtime_path)
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def replace_runtime_descriptor_if_token(
    path: Path,
    expected_token: str,
    descriptor: RuntimeDescriptor,
) -> bool:
    """Atomically replace a descriptor only while its token still matches."""
    runtime_path = Path(path)
    if not runtime_path.parent.is_dir() or not isinstance(expected_token, str):
        return False
    if not hmac.compare_digest(descriptor.token, expected_token):
        return False
    serialized = json.dumps(
        descriptor.to_dict(), ensure_ascii=False, separators=(",", ":")
    )
    with _runtime_descriptor_lock(runtime_path):
        try:
            raw_descriptor = json.loads(runtime_path.read_text(encoding="utf-8"))
            current_token = raw_descriptor.get("token")
        except (AttributeError, json.JSONDecodeError, OSError, UnicodeError):
            return False
        if not isinstance(current_token, str):
            return False
        try:
            matches = hmac.compare_digest(current_token, expected_token)
        except TypeError:
            return False
        if not matches:
            return False
        try:
            _replace_runtime_descriptor_locked(runtime_path, serialized)
        except OSError as exc:
            raise RuntimeError("无法写入运行时描述文件") from exc
        return True


def remove_runtime_descriptor_if_token(path: Path, token: str) -> bool:
    """Remove ``path`` under its OS lock only when its token still matches."""
    runtime_path = Path(path)
    if not runtime_path.parent.is_dir() or not isinstance(token, str):
        return False
    with _runtime_descriptor_lock(runtime_path):
        try:
            raw_descriptor = json.loads(runtime_path.read_text(encoding="utf-8"))
            descriptor_token = raw_descriptor.get("token")
        except (AttributeError, json.JSONDecodeError, OSError, UnicodeError):
            return False
        if not isinstance(descriptor_token, str):
            return False
        try:
            matches = hmac.compare_digest(
                descriptor_token.encode("utf-8"), token.encode("utf-8")
            )
        except UnicodeError:
            return False
        if not matches:
            return False
        try:
            runtime_path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RuntimeError("无法删除运行时描述文件") from exc
        return True


def pid_is_alive(pid: int) -> bool:
    """Return whether ``pid`` identifies a live local process."""
    if type(pid) is not int or pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        try:
            return True
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return exc.errno == errno.EPERM
    return True


def _object_without_duplicate_fields(pairs):
    """Build a JSON object while rejecting duplicate field names."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("运行时描述文件包含重复字段")
        result[key] = value
    return result


def _parse_expiry(raw_expiry: object) -> datetime:
    """Parse a strictly UTC runtime expiry value."""
    if not isinstance(raw_expiry, str) or not raw_expiry:
        raise ValueError("expires_at 必须是 UTC 日期时间字符串")
    normalized = (
        raw_expiry[:-1] + "+00:00" if raw_expiry.endswith("Z") else raw_expiry
    )
    try:
        expires_at = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("expires_at 格式无效") from exc
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("expires_at 必须包含 UTC 时区")
    if expires_at.utcoffset() != timedelta(0):
        raise ValueError("expires_at 必须使用 UTC 时区")
    return expires_at.astimezone(timezone.utc)


def _current_utc(now: Callable[[], datetime]) -> datetime:
    """Read an aware injected clock and normalize it to UTC."""
    current = now()
    if (
        not isinstance(current, datetime)
        or current.tzinfo is None
        or current.utcoffset() is None
    ):
        raise ValueError("当前时间必须是带时区的日期时间")
    return current.astimezone(timezone.utc)


def read_runtime_descriptor(
    path: Path,
    *,
    now: Callable[[], datetime] | None = None,
    pid_checker: Callable[[int], bool] = pid_is_alive,
) -> RuntimeDescriptor:
    """Read and validate a live V1 runtime descriptor from ``path``."""
    runtime_path = Path(path)
    try:
        serialized = runtime_path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValueError("运行时描述文件不是有效 UTF-8") from exc
    except OSError as exc:
        raise RuntimeError("无法读取运行时描述文件") from exc
    try:
        raw = json.loads(
            serialized,
            object_pairs_hook=_object_without_duplicate_fields,
        )
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError("运行时描述文件不是有效 JSON") from exc

    if not isinstance(raw, dict) or set(raw) != _DESCRIPTOR_FIELDS:
        raise ValueError("运行时描述文件字段不完整或包含未知字段")

    port = raw["port"]
    token = raw["token"]
    pid = raw["pid"]
    protocol_version = raw["protocol_version"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("运行时描述文件的 port 无效")
    if not isinstance(token, str) or not token:
        raise ValueError("运行时描述文件的 token 无效")
    if type(pid) is not int or pid <= 0:
        raise ValueError("运行时描述文件的 pid 无效")
    if type(protocol_version) is not int or protocol_version != _PROTOCOL_VERSION:
        raise ValueError("运行时描述文件的协议版本无效")

    expires_at = _parse_expiry(raw["expires_at"])
    current = _current_utc(now or (lambda: datetime.now(timezone.utc)))
    if expires_at <= current:
        raise ValueError("运行时描述文件已过期")
    try:
        process_alive = pid_checker(pid)
    except Exception as exc:
        raise RuntimeError("无法检查运行时描述文件对应的进程") from exc
    if not process_alive:
        raise ValueError("运行时描述文件对应的进程不存在")

    return RuntimeDescriptor(
        port=port,
        token=token,
        pid=pid,
        protocol_version=protocol_version,
        expires_at=expires_at,
    )
