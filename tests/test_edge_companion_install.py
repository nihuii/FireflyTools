import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock


try:
    import tools.edge_companion.install as install_module
except ModuleNotFoundError as import_error:
    install_module = None
    INSTALL_IMPORT_ERROR = import_error
else:
    INSTALL_IMPORT_ERROR = None


EXPECTED_HOST_NAME = "com.fireflytools.video_capture"
EXPECTED_ORIGIN = "chrome-extension://applbmkghgaoadhmmcdnbmebgideiefg/"
EXPECTED_REGISTRY_KEY = (
    r"Software\Microsoft\Edge\NativeMessagingHosts\com.fireflytools.video_capture"
)
EXPECTED_DESCRIPTION = "FireflyTools Edge video capture host"


class FakeRegistryKey:
    def __init__(self, registry, path):
        self.registry = registry
        self.path = path
        self.closed = False

    def close(self):
        self.closed = True


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    REG_SZ = 1

    def __init__(self):
        self.keys = set()
        self.values = {}
        self.create_calls = []
        self.open_calls = []
        self.set_calls = []
        self.delete_calls = []

    def CreateKey(self, root, path):
        self.create_calls.append((root, path))
        if root is not self.HKEY_CURRENT_USER:
            raise AssertionError("installer used a non-HKCU registry root")
        self.keys.add(path)
        return FakeRegistryKey(self, path)

    def OpenKey(self, root, path):
        self.open_calls.append((root, path))
        if root is not self.HKEY_CURRENT_USER:
            raise AssertionError("status used a non-HKCU registry root")
        if path not in self.keys:
            raise FileNotFoundError(path)
        return FakeRegistryKey(self, path)

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.set_calls.append((key.path, name, reserved, value_type, value))
        self.values[(key.path, name)] = (value, value_type)

    def QueryValueEx(self, key, name):
        try:
            return self.values[(key.path, name)]
        except KeyError as error:
            raise FileNotFoundError(name) from error

    def DeleteKey(self, root, path):
        self.delete_calls.append((root, path))
        if root is not self.HKEY_CURRENT_USER:
            raise AssertionError("uninstaller used a non-HKCU registry root")
        if path not in self.keys:
            raise FileNotFoundError(path)
        self.keys.remove(path)
        self.values.pop((path, ""), None)

    @staticmethod
    def CloseKey(key):
        key.close()


class ProjectMetadataTests(unittest.TestCase):
    def test_pyproject_declares_only_the_local_package_and_edge_host_script(self):
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"

        metadata = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        self.assertEqual(
            metadata["build-system"],
            {
                "requires": ["setuptools>=69"],
                "build-backend": "setuptools.build_meta",
            },
        )
        self.assertEqual(
            metadata["project"],
            {
                "name": "fireflytools-local",
                "version": "0.1.0",
                "requires-python": ">=3.11",
                "scripts": {
                    "fireflytools-edge-host": (
                        "tools.edge_companion.native_host:main"
                    )
                },
            },
        )
        self.assertEqual(
            metadata["tool"]["setuptools"]["packages"]["find"],
            {"include": ["tools*"]},
        )
        self.assertNotIn("dependencies", metadata["project"])


class InstallerModuleBootstrapTests(unittest.TestCase):
    def test_installer_module_is_available(self):
        self.assertIsNone(INSTALL_IMPORT_ERROR, repr(INSTALL_IMPORT_ERROR))


@unittest.skipIf(install_module is None, "installer module not implemented yet")
class EdgeCompanionInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.local_app_data = self.root / "Local App Data"
        self.environment = {"LOCALAPPDATA": str(self.local_app_data)}
        self.launcher_path = (
            self.root / "Python With Spaces" / "Scripts" / "fireflytools-edge-host.exe"
        )
        self.launcher_path.parent.mkdir(parents=True)
        self.launcher_path.write_bytes(b"test launcher")
        self.manifest_path = (
            self.local_app_data
            / "FireflyTools"
            / "edge_companion"
            / f"{EXPECTED_HOST_NAME}.json"
        ).resolve()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def which_launcher(self, command):
        self.assertEqual(command, "fireflytools-edge-host.exe")
        return str(self.launcher_path)

    def expected_payload(self, launcher_path=None):
        return {
            "name": EXPECTED_HOST_NAME,
            "description": EXPECTED_DESCRIPTION,
            "path": str(launcher_path or self.launcher_path.resolve()),
            "type": "stdio",
            "allowed_origins": [EXPECTED_ORIGIN],
        }

    def install_valid_host(self, registry):
        return install_module.install_host(
            winreg_module=registry,
            which=self.which_launcher,
            environ=self.environment,
        )

    def test_required_constants_and_frozen_status_model_are_stable(self):
        self.assertEqual(install_module.HOST_NAME, EXPECTED_HOST_NAME)
        self.assertEqual(install_module.ALLOWED_ORIGIN, EXPECTED_ORIGIN)
        self.assertEqual(install_module.REGISTRY_KEY, EXPECTED_REGISTRY_KEY)
        status = install_module.HostInstallStatus(False, "detail")
        with self.assertRaises(AttributeError):
            status.installed = True

    def test_install_writes_exact_manifest_atomically_and_registers_hkcu_default(self):
        registry = FakeWinreg()
        real_replace = os.replace
        replace_calls = []

        def recording_replace(source, destination):
            replace_calls.append((Path(source), Path(destination)))
            return real_replace(source, destination)

        with mock.patch.object(
            install_module.os, "replace", side_effect=recording_replace
        ):
            first_status = self.install_valid_host(registry)
            second_status = self.install_valid_host(registry)

        self.assertEqual(
            first_status,
            install_module.HostInstallStatus(
                True,
                "Edge 连接组件已安装。",
                manifest_path=self.manifest_path,
                launcher_path=self.launcher_path.resolve(),
            ),
        )
        self.assertTrue(second_status.installed)
        self.assertEqual(
            json.loads(self.manifest_path.read_text(encoding="utf-8")),
            self.expected_payload(),
        )
        self.assertEqual(
            registry.create_calls,
            [
                (registry.HKEY_CURRENT_USER, EXPECTED_REGISTRY_KEY),
                (registry.HKEY_CURRENT_USER, EXPECTED_REGISTRY_KEY),
            ],
        )
        self.assertEqual(
            registry.set_calls,
            [
                (
                    EXPECTED_REGISTRY_KEY,
                    "",
                    0,
                    registry.REG_SZ,
                    str(self.manifest_path),
                ),
                (
                    EXPECTED_REGISTRY_KEY,
                    "",
                    0,
                    registry.REG_SZ,
                    str(self.manifest_path),
                ),
            ],
        )
        self.assertEqual(len(replace_calls), 2)
        self.assertEqual(len({source.name for source, _ in replace_calls}), 2)
        for source, destination in replace_calls:
            self.assertEqual(source.parent, self.manifest_path.parent)
            self.assertTrue(source.name.startswith(self.manifest_path.name + "."))
            self.assertTrue(source.name.endswith(".tmp"))
            self.assertEqual(destination, self.manifest_path)
        self.assertEqual(
            list(self.manifest_path.parent.glob(self.manifest_path.name + ".*.tmp")),
            [],
        )

    def test_install_rejects_missing_argument_non_exe_non_file_launchers(self):
        non_exe = self.root / "host.cmd"
        non_exe.write_text("launcher", encoding="utf-8")
        missing_exe = self.root / "missing.exe"
        directory_exe = self.root / "directory.exe"
        directory_exe.mkdir()
        invalid_launchers = (
            None,
            f"{self.launcher_path} --extra-argument",
            str(non_exe),
            str(missing_exe),
            str(directory_exe),
        )

        for launcher in invalid_launchers:
            with self.subTest(launcher=launcher):
                registry = FakeWinreg()
                status = install_module.install_host(
                    winreg_module=registry,
                    which=lambda command, launcher=launcher: (
                        launcher
                        if command == "fireflytools-edge-host.exe"
                        else self.fail(f"unexpected launcher lookup: {command}")
                    ),
                    environ=self.environment,
                )

                self.assertFalse(status.installed)
                self.assertIn(
                    "python -m pip install -e . --no-deps",
                    status.detail,
                )
                self.assertEqual(registry.create_calls, [])
                self.assertFalse(self.manifest_path.exists())

    def test_status_missing_key_or_default_value_is_read_only_and_not_installed(self):
        for label, registry in (
            ("missing-key", FakeWinreg()),
            ("missing-default", FakeWinreg()),
        ):
            if label == "missing-default":
                registry.keys.add(EXPECTED_REGISTRY_KEY)
            with self.subTest(label=label):
                status = install_module.get_install_status(
                    winreg_module=registry,
                    which=self.which_launcher,
                    environ=self.environment,
                )

                self.assertEqual(
                    status,
                    install_module.HostInstallStatus(
                        False, "Edge 连接组件未安装。"
                    ),
                )
                self.assertEqual(registry.create_calls, [])
                self.assertEqual(registry.set_calls, [])
                self.assertFalse(self.local_app_data.exists())

    def test_status_accepts_only_the_exact_registered_manifest_and_launcher(self):
        registry = FakeWinreg()
        self.install_valid_host(registry)
        registry.create_calls.clear()
        registry.set_calls.clear()

        status = install_module.get_install_status(
            winreg_module=registry,
            which=self.which_launcher,
            environ=self.environment,
        )

        self.assertEqual(
            status,
            install_module.HostInstallStatus(
                True,
                "Edge 连接组件已安装。",
                manifest_path=self.manifest_path,
                launcher_path=self.launcher_path.resolve(),
            ),
        )
        self.assertEqual(registry.create_calls, [])
        self.assertEqual(registry.set_calls, [])

    def test_status_ignores_current_path_lookup_after_valid_registration(self):
        registry = FakeWinreg()
        self.install_valid_host(registry)
        alternate_launcher = self.root / "Other" / "alternate-host.exe"
        alternate_launcher.parent.mkdir()
        alternate_launcher.write_bytes(b"alternate launcher")
        registry.create_calls.clear()
        registry.set_calls.clear()

        for label, current_lookup in (
            ("different-launcher", str(alternate_launcher)),
            ("missing-from-path", None),
        ):
            with self.subTest(label=label):
                status = install_module.get_install_status(
                    winreg_module=registry,
                    which=lambda command, result=current_lookup: (
                        result
                        if command == "fireflytools-edge-host.exe"
                        else self.fail(f"unexpected launcher lookup: {command}")
                    ),
                    environ=self.environment,
                )

                self.assertEqual(
                    status,
                    install_module.HostInstallStatus(
                        True,
                        "Edge 连接组件已安装。",
                        manifest_path=self.manifest_path,
                        launcher_path=self.launcher_path.resolve(),
                    ),
                )
                self.assertEqual(registry.create_calls, [])
                self.assertEqual(registry.set_calls, [])

    def test_status_reports_unreadable_or_invalid_manifest(self):
        registry = FakeWinreg()
        registry.keys.add(EXPECTED_REGISTRY_KEY)
        registry.values[(EXPECTED_REGISTRY_KEY, "")] = (
            str(self.manifest_path),
            registry.REG_SZ,
        )

        missing_status = install_module.get_install_status(
            winreg_module=registry,
            which=self.which_launcher,
            environ=self.environment,
        )
        self.assertEqual(
            missing_status,
            install_module.HostInstallStatus(
                False,
                "Edge 连接组件清单不可用。",
                manifest_path=self.manifest_path,
            ),
        )

        self.manifest_path.parent.mkdir(parents=True)
        self.manifest_path.write_text("{invalid", encoding="utf-8")
        invalid_status = install_module.get_install_status(
            winreg_module=registry,
            which=self.which_launcher,
            environ=self.environment,
        )
        self.assertEqual(
            invalid_status,
            install_module.HostInstallStatus(
                False,
                "Edge 连接组件清单不可用。",
                manifest_path=self.manifest_path,
            ),
        )

    def test_status_rejects_mismatched_origin_and_invalid_launcher_paths(self):
        registry = FakeWinreg()
        self.install_valid_host(registry)
        non_exe_launcher = self.root / "Other" / "other-host.cmd"
        non_exe_launcher.parent.mkdir()
        non_exe_launcher.write_bytes(b"other launcher")

        payload_cases = []
        wrong_origin = self.expected_payload()
        wrong_origin["allowed_origins"] = ["chrome-extension://wrong/"]
        payload_cases.append(("wrong-origin", wrong_origin))
        nonexistent_launcher = self.expected_payload(self.root / "gone.exe")
        payload_cases.append(("nonexistent-launcher", nonexistent_launcher))
        non_exe_payload = self.expected_payload(non_exe_launcher.resolve())
        payload_cases.append(("non-exe-launcher", non_exe_payload))

        for label, payload in payload_cases:
            with self.subTest(label=label):
                self.manifest_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                status = install_module.get_install_status(
                    winreg_module=registry,
                    which=self.which_launcher,
                    environ=self.environment,
                )
                self.assertEqual(status.detail, "Edge 连接组件配置不匹配。")
                self.assertFalse(status.installed)

        unexpected_manifest = self.root / "attacker-owned.json"
        unexpected_manifest.write_text(
            json.dumps(self.expected_payload()), encoding="utf-8"
        )
        registry.values[(EXPECTED_REGISTRY_KEY, "")] = (
            str(unexpected_manifest),
            registry.REG_SZ,
        )

        unexpected_status = install_module.get_install_status(
            winreg_module=registry,
            which=self.which_launcher,
            environ=self.environment,
        )

        self.assertEqual(
            unexpected_status.detail, "Edge 连接组件配置不匹配。"
        )
        self.assertFalse(unexpected_status.installed)

    def test_uninstall_deletes_only_exact_key_and_owned_manifest(self):
        registry = FakeWinreg()
        self.install_valid_host(registry)
        unrelated_file = self.manifest_path.parent / "keep-me.txt"
        unrelated_file.write_text("keep", encoding="utf-8")
        attacker_manifest = self.root / "attacker-owned.json"
        attacker_manifest.write_text("keep", encoding="utf-8")
        registry.values[(EXPECTED_REGISTRY_KEY, "")] = (
            str(attacker_manifest),
            registry.REG_SZ,
        )

        status = install_module.uninstall_host(
            winreg_module=registry,
            environ=self.environment,
        )

        self.assertEqual(
            status,
            install_module.HostInstallStatus(
                False,
                "Edge 连接组件已卸载。",
                manifest_path=self.manifest_path,
            ),
        )
        self.assertEqual(
            registry.delete_calls,
            [(registry.HKEY_CURRENT_USER, EXPECTED_REGISTRY_KEY)],
        )
        self.assertNotIn(EXPECTED_REGISTRY_KEY, registry.keys)
        self.assertFalse(self.manifest_path.exists())
        self.assertTrue(unrelated_file.exists())
        self.assertTrue(attacker_manifest.exists())
        self.assertTrue(self.manifest_path.parent.exists())

    def test_uninstall_preserves_owned_manifest_directory_when_empty(self):
        registry = FakeWinreg()
        self.install_valid_host(registry)

        status = install_module.uninstall_host(
            winreg_module=registry,
            environ=self.environment,
        )

        self.assertEqual(status.detail, "Edge 连接组件已卸载。")
        self.assertFalse(self.manifest_path.exists())
        self.assertTrue(self.manifest_path.parent.is_dir())
        self.assertEqual(list(self.manifest_path.parent.iterdir()), [])

    def test_uninstall_is_idempotent_and_does_not_create_directories(self):
        registry = FakeWinreg()

        status = install_module.uninstall_host(
            winreg_module=registry,
            environ=self.environment,
        )

        self.assertEqual(
            status,
            install_module.HostInstallStatus(
                False,
                "Edge 连接组件未安装，无需卸载。",
                manifest_path=self.manifest_path,
            ),
        )
        self.assertEqual(
            registry.delete_calls,
            [(registry.HKEY_CURRENT_USER, EXPECTED_REGISTRY_KEY)],
        )
        self.assertFalse(self.local_app_data.exists())

    def test_cli_has_explicit_exit_codes_and_friendly_windows_unavailable_error(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(install_module, "_winreg", None):
            unavailable_code = install_module.main(
                ["status"],
                stdout=stdout,
                stderr=stderr,
                environ=self.environment,
            )

        self.assertEqual(unavailable_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Windows 注册表", stderr.getvalue())

        registry = FakeWinreg()
        stdout = io.StringIO()
        stderr = io.StringIO()
        installed_code = install_module.main(
            ["install"],
            stdout=stdout,
            stderr=stderr,
            winreg_module=registry,
            which=self.which_launcher,
            environ=self.environment,
        )
        self.assertEqual(installed_code, 0)
        self.assertEqual(stdout.getvalue(), "Edge 连接组件已安装。\n")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
