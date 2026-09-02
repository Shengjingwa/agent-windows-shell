from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import rewrite_windows_shell as shell


def config(**overrides: object) -> dict:
    value = dict(shell.DEFAULTS)
    value.update(
        {
            "utf8Prefix": False,
            "wrapHeredoc": False,
            "wrapAndAnd": True,
            "useNoProxyEnv": False,
            "rfc1918NoProxy": True,
        }
    )
    value.update(overrides)
    return value


class RewriteTests(unittest.TestCase):
    def test_routes_clear_bash_syntax_to_git_bash(self) -> None:
        command = "export NAME=agent\nprintf '%s\\n' \"$NAME\""
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash", return_value="BASH") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteBash=True))
        self.assertEqual(actual, "BASH")
        wrapper.assert_called_once_with(command, bash)

    def test_routes_bash_shebang_before_comment_masking(self) -> None:
        command = "#!/usr/bin/env bash\nprintf 'hello\\n'"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        decision = shell.classify_command(
            command,
            config(autoRouteBash=True),
            bash=bash,
            cmd=None,
        )
        self.assertEqual(decision.shell, "git-bash")
        self.assertEqual(decision.reason, "bash-syntax:shebang")

    def test_routes_clear_batch_syntax_to_cmd(self) -> None:
        command = '@echo off\nset "NAME=agent"\necho %NAME%'
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        with (
            patch.object(shell, "find_cmd", return_value=cmd),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_cmd", return_value="CMD") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteCmd=True))
        self.assertEqual(actual, "CMD")
        wrapper.assert_called_once_with(command, cmd)

    def test_percent_encoded_url_does_not_route_to_cmd(self) -> None:
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        decision = shell.classify_command(
            "curl.exe https://example.com/a%20b%2Fc",
            config(autoRouteCmd=True),
            bash=None,
            cmd=cmd,
        )
        self.assertEqual(decision.shell, "host")

    def test_closed_percent_variable_routes_to_cmd(self) -> None:
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        decision = shell.classify_command(
            "echo %PATH%",
            config(autoRouteCmd=True),
            bash=None,
            cmd=cmd,
        )
        self.assertEqual(decision.shell, "cmd")
        self.assertEqual(decision.reason, "cmd-syntax:percent-variable")

    def test_batch_for_variable_routes_to_cmd(self) -> None:
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        decision = shell.classify_command(
            "for %%F in (*.txt) do echo %%F",
            config(autoRouteCmd=True),
            bash=None,
            cmd=cmd,
        )
        self.assertEqual(decision.shell, "cmd")
        self.assertEqual(decision.reason, "cmd-syntax:for-variable")

    def test_keeps_powershell_syntax_in_host_shell(self) -> None:
        command = "$env:NAME='agent'; Write-Output $env:NAME"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_cmd", return_value=cmd),
            patch.object(shell, "find_pwsh", return_value=None),
        ):
            actual = shell.rewrite_command(
                command,
                config(autoRouteBash=True, autoRouteCmd=True),
            )
        self.assertEqual(actual, command)

    def test_keeps_native_cli_in_host_shell(self) -> None:
        command = "git status --short"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_cmd", return_value=cmd),
            patch.object(shell, "find_pwsh", return_value=None),
        ):
            actual = shell.rewrite_command(
                command,
                config(autoRouteBash=True, autoRouteCmd=True),
            )
        self.assertEqual(actual, command)

    def test_respects_explicit_wsl_selection(self) -> None:
        command = "wsl.exe --distribution Ubuntu -- bash -lc 'uname -a'"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_cmd", return_value=cmd),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash") as bash_wrapper,
            patch.object(shell, "wrap_cmd") as cmd_wrapper,
        ):
            actual = shell.rewrite_command(
                command,
                config(autoRouteBash=True, autoRouteCmd=True),
            )
        self.assertEqual(actual, command)
        bash_wrapper.assert_not_called()
        cmd_wrapper.assert_not_called()

    def test_does_not_activate_destructive_bash_through_auto_route(self) -> None:
        command = "set -e\nrm -rf build"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteBash=True))
        self.assertEqual(actual, command)
        wrapper.assert_not_called()

    def test_does_not_activate_reordered_destructive_bash_flags(self) -> None:
        command = "set -e\nrm -fr build"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteBash=True))
        self.assertEqual(actual, command)
        wrapper.assert_not_called()

    def test_does_not_activate_destructive_batch_through_auto_route(self) -> None:
        command = "@echo off\nrd /s /q build"
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        with (
            patch.object(shell, "find_cmd", return_value=cmd),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_cmd") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteCmd=True))
        self.assertEqual(actual, command)
        wrapper.assert_not_called()

    def test_nested_destructive_bash_stays_in_host(self) -> None:
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        decision = shell.classify_command(
            "if true; then rm -rf build; fi",
            config(autoRouteBash=True),
            bash=bash,
            cmd=None,
        )
        self.assertEqual(decision.shell, "host")
        self.assertEqual(decision.reason, "unsafe-command")

    def test_parenthesized_destructive_batch_stays_in_host(self) -> None:
        cmd = Path(r"C:\Windows\System32\cmd.exe")
        decision = shell.classify_command(
            "if exist build (rd /s /q build)",
            config(autoRouteCmd=True),
            bash=None,
            cmd=cmd,
        )
        self.assertEqual(decision.shell, "host")
        self.assertEqual(decision.reason, "unsafe-command")

    def test_route_explanation_is_stable_and_machine_readable(self) -> None:
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        decision = shell.classify_command(
            "source scripts/setup.sh",
            config(autoRouteBash=True),
            bash=bash,
            cmd=None,
        )
        self.assertEqual(decision.shell, "git-bash")
        self.assertEqual(decision.reason, "bash-syntax:source")

    def test_explain_reports_shell_reason_and_changes(self) -> None:
        with (
            patch.object(shell, "find_git_bash", return_value=None),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "find_cmd", return_value=None),
        ):
            actual = shell.explain_command("ls -la", config())
        self.assertEqual(actual["shell"], "host")
        self.assertEqual(actual["reason"], "native-or-powershell")
        self.assertTrue(actual["commandChanged"])
        self.assertEqual(actual["changes"], ["safe-aliases"])

    def test_run_command_propagates_exit_and_cleans_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pwsh = root / "pwsh.exe"
            completed = shell.subprocess.CompletedProcess([], 17)
            with (
                patch.object(shell, "find_pwsh", return_value=pwsh),
                patch.object(
                    shell,
                    "plan_command",
                    return_value=shell.CommandPlan(
                        "host",
                        "native-or-powershell",
                        "Write-Output ok",
                        None,
                    ),
                ),
                patch.object(shell, "tmp_dir", return_value=root),
                patch.object(shell.subprocess, "run", return_value=completed) as runner,
            ):
                actual = shell.run_command("native-command", config())
            self.assertEqual(actual, 17)
            self.assertEqual(list(root.glob("agent-*.ps1")), [])
            self.assertIn("-NonInteractive", runner.call_args.args[0])

    def test_run_command_starts_git_bash_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bash = root / "bash.exe"
            completed = shell.subprocess.CompletedProcess([], 0)
            with (
                patch.object(
                    shell,
                    "plan_command",
                    return_value=shell.CommandPlan(
                        "git-bash",
                        "bash-syntax:export",
                        "export NAME=agent",
                        bash,
                    ),
                ),
                patch.object(shell, "tmp_dir", return_value=root),
                patch.object(shell.subprocess, "run", return_value=completed) as runner,
            ):
                actual = shell.run_command("export NAME=agent", config())
            self.assertEqual(actual, 0)
            argv = runner.call_args.args[0]
            self.assertEqual(argv[:3], [str(bash), "--noprofile", "--norc"])
            self.assertEqual(runner.call_args.kwargs["env"]["MSYS_NO_PATHCONV"], "1")
            self.assertEqual(list(root.glob("agent-*.sh")), [])

    def test_rewrites_only_command_position_aliases(self) -> None:
        command = "ls -la; curl https://example.com | Write-Output"
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(
            actual,
            "Get-ChildItem -Force; curl.exe https://example.com | Write-Output",
        )

    def test_does_not_rewrite_strings_comments_or_arguments(self) -> None:
        command = (
            "Write-Output 'ls -la && curl x'\n"
            "# ls -la\n"
            "Write-Output curl"
        )
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, command)

    def test_never_activates_destructive_rm(self) -> None:
        command = "rm -rf build"
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, command)
        self.assertNotIn("Remove-Item", actual)

    def test_heredoc_text_in_strings_and_comments_is_ignored(self) -> None:
        command = "Write-Output 'cat <<EOF'\n# cat <<EOF"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command(
                command,
                config(wrapHeredoc=True),
            )
        self.assertEqual(actual, command)
        wrapper.assert_not_called()

    def test_real_heredoc_uses_git_bash(self) -> None:
        command = "cat <<'EOF'\nhello\nEOF"
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        with (
            patch.object(shell, "find_git_bash", return_value=bash),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command(
                command,
                config(wrapHeredoc=True),
            )
        self.assertEqual(actual, "WRAPPED")
        wrapper.assert_called_once_with(command, bash)

    def test_without_pwsh_preserves_and_and_for_visible_failure(self) -> None:
        command = "native-one && native-two"
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, command)
        self.assertNotIn("; native-two", actual)

    def test_without_pwsh_preserves_or_or_for_visible_failure(self) -> None:
        command = "native-one || native-two"
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, command)

    def test_with_pwsh_wraps_and_and_without_changing_semantics(self) -> None:
        command = "native-one && native-two"
        pwsh = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
        with (
            patch.object(shell, "find_pwsh", return_value=pwsh),
            patch.object(shell, "wrap_pwsh", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, "WRAPPED")
        wrapper.assert_called_once_with(command, pwsh, config())

    def test_with_pwsh_wraps_or_or_without_changing_semantics(self) -> None:
        command = "native-one || native-two"
        pwsh = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
        with (
            patch.object(shell, "find_pwsh", return_value=pwsh),
            patch.object(shell, "wrap_pwsh", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, "WRAPPED")
        wrapper.assert_called_once_with(command, pwsh, config())

    def test_pwsh_script_propagates_native_exit_code(self) -> None:
        pwsh = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
        with (
            patch.object(shell, "_write_temp_script") as writer,
            patch.object(shell, "_wrapper", return_value="WRAPPED"),
        ):
            actual = shell.wrap_pwsh(
                "cmd.exe /d /c exit 7 && Write-Output SHOULD_NOT_RUN",
                pwsh,
                config(),
            )
        self.assertEqual(actual, "WRAPPED")
        body = writer.call_args.args[1]
        self.assertTrue(body.startswith("$global:LASTEXITCODE = 0\n"))
        self.assertIn("$agentShellSucceeded = $?", body)
        self.assertIn("$agentShellNativeExit = $LASTEXITCODE", body)
        self.assertIn("exit $agentShellNativeExit", body)
        self.assertIn("exit 1", body)
        self.assertEqual(body.count("exit $agentShellNativeExit"), 1)

    def test_noproxy_requires_pwsh_7(self) -> None:
        command = "Invoke-RestMethod http://192.168.1.10/api"
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, command)

    def test_noproxy_is_injected_before_pwsh_7_wrapper(self) -> None:
        command = "Invoke-RestMethod http://192.168.1.10/api"
        pwsh = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
        with (
            patch.object(shell, "find_pwsh", return_value=pwsh),
            patch.object(shell, "wrap_pwsh", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, "WRAPPED")
        wrapped_command = wrapper.call_args.args[0]
        self.assertIn("Invoke-RestMethod -NoProxy", wrapped_command)

    def test_bom_configuration_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps({"utf8Prefix": False}),
                encoding="utf-8-sig",
            )
            with (
                patch.object(shell, "KIT_ROOT", root),
                patch.object(Path, "home", return_value=root / "home"),
                patch.dict(os.environ, {}, clear=True),
            ):
                actual = shell.load_config()
        self.assertFalse(actual["utf8Prefix"])


class HookProtocolTests(unittest.TestCase):
    def test_cursor_response_uses_updated_input(self) -> None:
        payload = {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "ls -la", "working_directory": "C:\\"},
        }
        response = shell.hook_response(
            payload,
            payload["tool_input"],
            "Get-ChildItem -Force",
        )
        self.assertEqual(response["permission"], "allow")
        self.assertEqual(
            response["updated_input"]["command"],
            "Get-ChildItem -Force",
        )
        self.assertNotIn("hookSpecificOutput", response)

    def test_claude_response_uses_hook_specific_output(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "transcript_path": "transcript.jsonl",
        }
        response = shell.hook_response(
            payload,
            payload["tool_input"],
            "Get-ChildItem -Force",
        )
        output = response["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["updatedInput"]["command"], "Get-ChildItem -Force")
        self.assertNotIn("permission", response)

    def test_non_shell_tool_is_ignored(self) -> None:
        payload = {
            "tool_name": "Read",
            "tool_input": {"command": "ls -la"},
        }
        tool_input, command = shell.extract_command(payload)
        self.assertIsNone(tool_input)
        self.assertEqual(command, "")


if __name__ == "__main__":
    unittest.main()
