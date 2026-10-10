from __future__ import annotations

import contextlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import session_start


def fake_winreg(machine: str, user: str) -> types.ModuleType:
    module = types.ModuleType("winreg")
    module.HKEY_LOCAL_MACHINE = "HKLM"
    module.HKEY_CURRENT_USER = "HKCU"
    values = {"HKLM": machine, "HKCU": user}
    module.OpenKey = lambda root, _subkey: contextlib.nullcontext(root)
    module.QueryValueEx = lambda root, _name: (values[root], 2)
    module.ExpandEnvironmentStrings = lambda text: text.replace("%SystemRoot%", r"C:\Windows")
    return module


class ShellPathTests(unittest.TestCase):
    def test_appends_registry_entries_the_editor_path_lacks(self) -> None:
        registry = fake_winreg(
            r"%SystemRoot%\system32;C:\Program Files\nodejs\;C:\Program Files\Python313\\",
            r"C:\Users\me\bin;c:\program files\NODEJS",
        )
        stale = os.pathsep.join([r"C:\Windows\System32", r"C:\Program Files\Git\cmd"])
        with patch.dict(sys.modules, {"winreg": registry}), patch.dict(os.environ, {"PATH": stale}):
            path = session_start.shell_path().split(os.pathsep)
        self.assertEqual(
            path,
            [
                r"C:\Windows\System32",
                r"C:\Program Files\Git\cmd",
                r"C:\Program Files\nodejs",
                r"C:\Program Files\Python313",
                r"C:\Users\me\bin",
            ],
        )


class MachineFactsTests(unittest.TestCase):
    def facts(self, found: dict) -> list:
        def which(name, path=None):
            return found.get(name)

        with patch.object(session_start, "shell_path", return_value=""), patch(
            "shutil.which", side_effect=which
        ):
            return session_start.machine_facts()

    def test_silent_when_python_resolves(self) -> None:
        self.assertEqual(self.facts({"python": r"C:\Program Files\Python313\python.exe"}), [])

    def test_points_to_the_launcher_when_python_is_missing(self) -> None:
        facts = self.facts({"py": r"C:\Windows\py.exe"})
        self.assertEqual(len(facts), 1)
        self.assertIn("py -3", facts[0])

    def test_store_alias_does_not_count_as_python(self) -> None:
        alias = r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\python.exe"
        facts = self.facts({"python": alias, "py": r"C:\Windows\py.exe"})
        self.assertIn("py -3", facts[0])


if __name__ == "__main__":
    unittest.main()
