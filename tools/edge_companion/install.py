"""Install the Edge Native Messaging host for the current Windows user."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import winreg as _winreg
except ImportError:  # pragma: no cover - exercised by injection on Windows
    _winreg = None


HOST_NAME = "com.fireflytools.video_capture"
ALLOWED_ORIGIN = "chrome-extension://applbmkghgaoadhmmcdnbmebgideiefg/"
REGISTRY_KEY = (
    r"Software\Microsoft\Edge\NativeMessagingHosts\com.fireflytools.video_capture"
)

_LAUNCHER_NAME = "fireflytools-edge-host.exe"
_DESCRIPTION = "FireflyTools Edge video capture host"
_INSTALLED = "Edge 连接组件已安装。"
_NOT_INSTALLED = "Edge 连接组件未安装。"
_MANIFEST_UNAVAILABLE = "Edge 连接组件清单不可用。"
_CONFIG_MISMATCH = "Edge 连接组件配置不匹配。"
_UNINSTALLED = "Edge 连接组件已卸载。"
_ALREADY_UNINSTALLED = "Edge 连接组件未安装，无需卸载。"
_WINDOWS_UNAVAILABLE = "当前系统不支持 Windows 注册表，无法管理 Edge 连接组件。"
_LAUNCHER_UNAVAILABLE = (
    "未找到可用的 fireflytools-edge-host.exe。"
    "请先运行 python -m pip install -e . --no-deps。"
)
_LOCAL_APP_DATA_UNAVAILABLE = "无法确定当前用户的 LOCALAPPDATA 路径。"
_REGISTRY_READ_FAILED = "无法读取 Edge 连接组件安装状态。"
_REGISTRY_WRITE_FAILED = "Edge 连接组件安装失败：无法写入当前用户注册表。"
_MANIFEST_WRITE_FAILED = "Edge 连接组件安装失败：无法写入清单。"
_UNINSTALL_FAILED = "Edge 连接组件卸载失败。"


@dataclass(frozen=True)
class HostInstallStatus:
    """Describe whether the exact Edge host registration is usable."""

    installed: bool
    detail: str
    manifest_path: Path | None = None
    launcher_path: Path | None = None


def default_manifest_path(environ=None) -> Path:
    """Return the one manifest path owned by this installer."""
    environment = os.environ if environ is None else environ
    local_app_data = environment.get("LOCALAPPDATA")
    if not isinstance(local_app_data, str) or not local_app_data.strip():
        raise ValueError(_LOCAL_APP_DATA_UNAVAILABLE)
    return (
        Path(local_app_data).resolve()
        / "FireflyTools"
        / "edge_companion"
        / f"{HOST_NAME}.json"
    )


def _registry(winreg_module):
    """Select an injected registry module or the platform implementation."""
    return _winreg if winreg_module is None else winreg_module


def _close_registry_key(registry, key) -> None:
    """Close a registry handle through either supported module interface."""
    close_key = getattr(registry, "CloseKey", None)
    if callable(close_key):
        close_key(key)
        return
    close = getattr(key, "close", None)
    if callable(close):
        close()


def _is_missing(error: OSError) -> bool:
    """Return whether a registry or file operation reported not found."""
    return (
        isinstance(error, FileNotFoundError)
        or getattr(error, "winerror", None) == 2
    )


def _find_launcher(which=None) -> Path | None:
    """Resolve a single existing executable returned by the PATH lookup."""
    find = shutil.which if which is None else which
    try:
        candidate = find(_LAUNCHER_NAME)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(candidate, (str, os.PathLike)):
        return None
    try:
        path = Path(candidate)
        if path.suffix.lower() != ".exe" or not path.is_file():
            return None
        resolved = path.resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return None
    if resolved.suffix.lower() != ".exe" or not resolved.is_file():
        return None
    return resolved


def _manifest_payload(launcher_path: Path) -> dict:
    """Build the exact Native Messaging manifest object for Edge."""
    return {
        "name": HOST_NAME,
        "description": _DESCRIPTION,
        "path": str(launcher_path),
        "type": "stdio",
        "allowed_origins": [ALLOWED_ORIGIN],
    }


def _write_manifest(path: Path, payload: dict) -> None:
    """Atomically replace the owned manifest through a unique peer file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def install_host(*, winreg_module=None, which=None, environ=None) -> HostInstallStatus:
    """Write the manifest and register it under the current user's Edge key."""
    registry = _registry(winreg_module)
    if registry is None:
        return HostInstallStatus(False, _WINDOWS_UNAVAILABLE)

    launcher_path = _find_launcher(which)
    if launcher_path is None:
        return HostInstallStatus(False, _LAUNCHER_UNAVAILABLE)

    try:
        manifest_path = default_manifest_path(environ)
    except (OSError, TypeError, ValueError):
        return HostInstallStatus(
            False,
            _LOCAL_APP_DATA_UNAVAILABLE,
            launcher_path=launcher_path,
        )

    try:
        _write_manifest(manifest_path, _manifest_payload(launcher_path))
    except (OSError, TypeError, ValueError):
        return HostInstallStatus(
            False,
            _MANIFEST_WRITE_FAILED,
            manifest_path=manifest_path,
            launcher_path=launcher_path,
        )

    key = None
    try:
        key = registry.CreateKey(registry.HKEY_CURRENT_USER, REGISTRY_KEY)
        registry.SetValueEx(
            key,
            "",
            0,
            registry.REG_SZ,
            str(manifest_path),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return HostInstallStatus(
            False,
            _REGISTRY_WRITE_FAILED,
            manifest_path=manifest_path,
            launcher_path=launcher_path,
        )
    finally:
        if key is not None:
            _close_registry_key(registry, key)

    return HostInstallStatus(
        True,
        _INSTALLED,
        manifest_path=manifest_path,
        launcher_path=launcher_path,
    )


def _read_registered_manifest_path(registry) -> tuple[Path | None, str | None]:
    """Read the exact HKCU default value and classify registry failures."""
    key = None
    try:
        key = registry.OpenKey(registry.HKEY_CURRENT_USER, REGISTRY_KEY)
        value, value_type = registry.QueryValueEx(key, "")
    except OSError as error:
        if _is_missing(error):
            return None, _NOT_INSTALLED
        return None, _REGISTRY_READ_FAILED
    except (AttributeError, TypeError, ValueError):
        return None, _REGISTRY_READ_FAILED
    finally:
        if key is not None:
            _close_registry_key(registry, key)

    if value_type != registry.REG_SZ or not isinstance(value, str):
        return None, _CONFIG_MISMATCH
    try:
        return Path(value), None
    except (OSError, TypeError, ValueError):
        return None, _CONFIG_MISMATCH


def get_install_status(
    *, winreg_module=None, which=None, environ=None
) -> HostInstallStatus:
    """Inspect the current-user registration without writing any state."""
    registry = _registry(winreg_module)
    if registry is None:
        return HostInstallStatus(False, _WINDOWS_UNAVAILABLE)
    try:
        expected_manifest_path = default_manifest_path(environ)
    except (OSError, TypeError, ValueError):
        return HostInstallStatus(False, _LOCAL_APP_DATA_UNAVAILABLE)

    registered_path, error_detail = _read_registered_manifest_path(registry)
    if error_detail is not None:
        return HostInstallStatus(False, error_detail)
    if registered_path != expected_manifest_path:
        return HostInstallStatus(
            False,
            _CONFIG_MISMATCH,
            manifest_path=registered_path,
        )

    try:
        payload = json.loads(expected_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return HostInstallStatus(
            False,
            _MANIFEST_UNAVAILABLE,
            manifest_path=expected_manifest_path,
        )

    if not isinstance(payload, dict):
        return HostInstallStatus(
            False,
            _CONFIG_MISMATCH,
            manifest_path=expected_manifest_path,
        )
    launcher_value = payload.get("path")
    if not isinstance(launcher_value, str):
        return HostInstallStatus(
            False,
            _CONFIG_MISMATCH,
            manifest_path=expected_manifest_path,
        )
    try:
        launcher_path = Path(launcher_value)
        if (
            not launcher_path.is_absolute()
            or launcher_path.suffix.lower() != ".exe"
            or not launcher_path.is_file()
        ):
            raise ValueError("invalid launcher")
        resolved_launcher_path = launcher_path.resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return HostInstallStatus(
            False,
            _CONFIG_MISMATCH,
            manifest_path=expected_manifest_path,
        )

    if launcher_path != resolved_launcher_path or payload != _manifest_payload(
        resolved_launcher_path
    ):
        return HostInstallStatus(
            False,
            _CONFIG_MISMATCH,
            manifest_path=expected_manifest_path,
            launcher_path=resolved_launcher_path,
        )

    return HostInstallStatus(
        True,
        _INSTALLED,
        manifest_path=expected_manifest_path,
        launcher_path=resolved_launcher_path,
    )


def uninstall_host(*, winreg_module=None, environ=None) -> HostInstallStatus:
    """Remove only this tool's exact HKCU key and fixed generated manifest."""
    registry = _registry(winreg_module)
    if registry is None:
        return HostInstallStatus(False, _WINDOWS_UNAVAILABLE)
    try:
        manifest_path = default_manifest_path(environ)
    except (OSError, TypeError, ValueError):
        return HostInstallStatus(False, _LOCAL_APP_DATA_UNAVAILABLE)

    removed_registry_key = False
    try:
        registry.DeleteKey(registry.HKEY_CURRENT_USER, REGISTRY_KEY)
        removed_registry_key = True
    except OSError as error:
        if not _is_missing(error):
            return HostInstallStatus(
                False,
                _UNINSTALL_FAILED,
                manifest_path=manifest_path,
            )
    except (AttributeError, TypeError, ValueError):
        return HostInstallStatus(
            False,
            _UNINSTALL_FAILED,
            manifest_path=manifest_path,
        )

    removed_manifest = False
    try:
        manifest_path.unlink()
        removed_manifest = True
    except FileNotFoundError:
        pass
    except OSError:
        return HostInstallStatus(
            False,
            _UNINSTALL_FAILED,
            manifest_path=manifest_path,
        )

    detail = (
        _UNINSTALLED
        if removed_registry_key or removed_manifest
        else _ALREADY_UNINSTALLED
    )
    return HostInstallStatus(False, detail, manifest_path=manifest_path)


def _argument_parser() -> argparse.ArgumentParser:
    """Create the three-command installer argument parser."""
    parser = argparse.ArgumentParser(description="管理 FireflyTools Edge 连接组件")
    parser.add_argument("command", choices=("install", "status", "uninstall"))
    return parser


def main(
    argv=None,
    *,
    stdout=None,
    stderr=None,
    winreg_module=None,
    which=None,
    environ=None,
) -> int:
    """Run the installer CLI and return an explicit process exit code."""
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    arguments = _argument_parser().parse_args(argv)

    if arguments.command == "install":
        status = install_host(
            winreg_module=winreg_module,
            which=which,
            environ=environ,
        )
        success = status.installed
    elif arguments.command == "status":
        status = get_install_status(
            winreg_module=winreg_module,
            which=which,
            environ=environ,
        )
        success = status.installed
    else:
        status = uninstall_host(
            winreg_module=winreg_module,
            environ=environ,
        )
        success = status.detail in {_UNINSTALLED, _ALREADY_UNINSTALLED}

    diagnostic_details = {
        _WINDOWS_UNAVAILABLE,
        _LOCAL_APP_DATA_UNAVAILABLE,
        _REGISTRY_READ_FAILED,
        _REGISTRY_WRITE_FAILED,
        _MANIFEST_WRITE_FAILED,
        _UNINSTALL_FAILED,
        _LAUNCHER_UNAVAILABLE,
    }
    stream = output if success or status.detail not in diagnostic_details else errors
    print(status.detail, file=stream)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
