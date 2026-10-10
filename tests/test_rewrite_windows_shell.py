from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "rewrite_windows_shell.py"
sys.path.insert(0, str(ROOT / "src"))

import rewrite_windows_shell as shell

REAL_HOST_IS_WINDOWS_POWERSHELL = shell.host_is_windows_powershell


def setUpModule() -> None:
    # Run from Windows PowerShell, every rewrite would gain the UTF-8 prefix.
    patcher = patch.object(shell, "host_is_windows_powershell", return_value=False)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
CMD = Path(r"C:\Windows\System32\cmd.exe")
PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")


def config(**overrides: object) -> dict:
    value = dict(shell.DEFAULTS)
    value.update(
        {
            "utf8Prefix": False,
            "wrapHeredoc": False,
            "wrapAndAnd": True,
            "useNoProxyEnv": False,
            "rfc1918NoProxy": True,
            "flushImplicitOutput": False,
        }
    )
    value.update(overrides)
    return value


def route(command: str, cfg: dict, *, bash=BASH, cmd=CMD):
    """Plan a command on a PowerShell 7 host with the given shells installed."""
    with (
        patch.object(shell, "find_git_bash", return_value=bash),
        patch.object(shell, "find_cmd", return_value=cmd),
        patch.object(shell, "host_is_powershell7", return_value=True),
    ):
        return shell.plan_command(command, cfg)


class RoutingTests(unittest.TestCase):
    def test_routes_clear_bash_syntax_to_git_bash(self) -> None:
        command = "export NAME=agent\nprintf '%s\\n' \"$NAME\""
        with (
            patch.object(shell, "find_git_bash", return_value=BASH),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash", return_value="BASH") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteBash=True))
        self.assertEqual(actual, "BASH")
        wrapper.assert_called_once_with(command, BASH)

    def test_routes_bash_shebang_before_comment_masking(self) -> None:
        decision = route(
            "#!/usr/bin/env bash\nprintf 'hello\\n'",
            config(autoRouteBash=True),
            bash=BASH,
            cmd=None,
        )
        self.assertEqual(decision.shell, "git-bash")
        self.assertEqual(decision.reason, "bash-syntax:shebang")

    def test_routes_clear_batch_syntax_to_cmd(self) -> None:
        command = '@echo off\nset "NAME=agent"\necho %NAME%'
        with (
            patch.object(shell, "find_cmd", return_value=CMD),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_cmd", return_value="CMD") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteCmd=True))
        self.assertEqual(actual, "CMD")
        wrapper.assert_called_once_with(command, CMD)

    def test_percent_encoded_url_does_not_route_to_cmd(self) -> None:
        decision = route(
            "curl.exe https://example.com/a%20b%2Fc",
            config(autoRouteCmd=True),
            bash=None,
            cmd=CMD,
        )
        self.assertEqual(decision.shell, "host")

    def test_closed_percent_variable_routes_to_cmd(self) -> None:
        decision = route(
            "echo %PATH%", config(autoRouteCmd=True), bash=None, cmd=CMD
        )
        self.assertEqual(decision.shell, "cmd")
        self.assertEqual(decision.reason, "cmd-syntax:percent-variable")

    def test_batch_for_variable_routes_to_cmd(self) -> None:
        decision = route(
            "for %%F in (*.txt) do echo %%F",
            config(autoRouteCmd=True),
            bash=None,
            cmd=CMD,
        )
        self.assertEqual(decision.shell, "cmd")
        self.assertEqual(decision.reason, "cmd-syntax:for-variable")

    def test_keeps_powershell_syntax_in_host_shell(self) -> None:
        command = "$env:NAME='agent'; Write-Output $env:NAME"
        with (
            patch.object(shell, "find_git_bash", return_value=BASH),
            patch.object(shell, "find_cmd", return_value=CMD),
            patch.object(shell, "find_pwsh", return_value=None),
        ):
            actual = shell.rewrite_command(
                command, config(autoRouteBash=True, autoRouteCmd=True)
            )
        self.assertEqual(actual, command)

    def test_keeps_native_cli_in_host_shell(self) -> None:
        command = "git status --short"
        with (
            patch.object(shell, "find_git_bash", return_value=BASH),
            patch.object(shell, "find_cmd", return_value=CMD),
            patch.object(shell, "find_pwsh", return_value=None),
        ):
            actual = shell.rewrite_command(
                command, config(autoRouteBash=True, autoRouteCmd=True)
            )
        self.assertEqual(actual, command)

    def test_respects_explicit_wsl_selection(self) -> None:
        command = "wsl.exe --distribution Ubuntu -- bash -lc 'uname -a'"
        with (
            patch.object(shell, "find_git_bash", return_value=BASH),
            patch.object(shell, "find_cmd", return_value=CMD),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash") as bash_wrapper,
            patch.object(shell, "wrap_cmd") as cmd_wrapper,
        ):
            actual = shell.rewrite_command(
                command, config(autoRouteBash=True, autoRouteCmd=True)
            )
        self.assertEqual(actual, command)
        bash_wrapper.assert_not_called()
        cmd_wrapper.assert_not_called()

    def test_does_not_activate_destructive_bash_through_auto_route(self) -> None:
        for command in ("set -e\nrm -rf build", "set -e\nrm -fr build"):
            with (
                patch.object(shell, "find_git_bash", return_value=BASH),
                patch.object(shell, "find_pwsh", return_value=None),
                patch.object(shell, "wrap_git_bash") as wrapper,
            ):
                actual = shell.rewrite_command(command, config(autoRouteBash=True))
            self.assertEqual(actual, command)
            wrapper.assert_not_called()

    def test_does_not_activate_destructive_batch_through_auto_route(self) -> None:
        command = "@echo off\nrd /s /q build"
        with (
            patch.object(shell, "find_cmd", return_value=CMD),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_cmd") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(autoRouteCmd=True))
        self.assertEqual(actual, command)
        wrapper.assert_not_called()

    def test_nested_destructive_commands_stay_in_host(self) -> None:
        bash_case = route(
            "if true; then rm -rf build; fi",
            config(autoRouteBash=True),
            bash=BASH,
            cmd=None,
        )
        cmd_case = route(
            "if exist build (rd /s /q build)",
            config(autoRouteCmd=True),
            bash=None,
            cmd=CMD,
        )
        for decision in (bash_case, cmd_case):
            self.assertEqual(decision.shell, "host")
            self.assertEqual(decision.reason, "unsafe-command")

    def test_destructive_command_hidden_from_the_mask_stays_in_host(self) -> None:
        # An apostrophe or `$(` opens a PowerShell string or subexpression that
        # hides the rest of the command from the masked text.
        for command in (
            "git commit -m \"$(cat <<'EOF'\nfix: don't drop rows\nEOF\n)\"\nrm -rf build",
            "git commit -m \"$(cat <<'EOF'\nfix (see #12\nEOF\n)\" && rm -rf build",
            "export A=1\necho it's\nrm -rf build",
            "set -e\necho ${#arr[@]}; rm -rf build",
            'export A="$(rm -rf build)"',
            "@echo off\necho it's\nrd /s /q build",
        ):
            decision = route(
                command,
                config(wrapHeredoc=True, autoRouteBash=True, autoRouteCmd=True),
                bash=BASH,
                cmd=CMD,
            )
            self.assertEqual((decision.shell, decision.reason), ("host", "unsafe-command"), command)

    def test_wrapped_or_unlisted_destructive_commands_stay_in_host(self) -> None:
        for command in (
            "export A=1; { rm -rf build; }",
            "export A=1; env rm -rf build",
            "export A=1; ls | xargs -0 rm",
            "export A=1; `rm -rf build`",
            "export A=1; bash -c 'rm -rf build'",
            "export A=1; /bin/rm -rf build",
            "export A=1; find . -name x -delete",
            "export A=1; git clean -fdx",
            "export A=1; truncate -s 0 f",
            "export A=1; shred -u f",
            "export A=1; unlink f",
            "export A=1; rsync -a --delete src/ dst/",
            "@echo off\n%COMSPEC% /c rd /s /q build",
            "@echo off\nformat D: /q",
            "@echo off\nrobocopy src dst /MIR",
            "@echo off\nrobocopy src dst /E /PURGE",
            "@echo off\nreg delete HKCU\\Software\\X /f",
            "@echo off\nsc.exe delete svc",
            "@echo off\nschtasks /Delete /TN x /F",
            "export A=1; git.exe clean -fdx",
            "export A=1; git -C . -c core.x=y clean -fdx",
            "export A=1; truncate build/keep.txt -s 0",
            "@echo off\nformat.com D: /q",
            '@echo off\nformat "D:" /q',
        ):
            decision = route(
                command, config(autoRouteBash=True, autoRouteCmd=True), bash=BASH, cmd=CMD
            )
            self.assertEqual((decision.shell, decision.reason), ("host", "unsafe-command"), command)

    def test_escaped_or_expanded_destructive_commands_stay_in_host(self) -> None:
        for command in (
            "export A=1; r\\m -rf build",
            "export A=1; r''m -rf build",
            'export A=1; "r"m -rf build',
            "export A=1; r\\\nm -rf build",
            "export A=1; $1rm -rf build",
            "export A=1; ${X:-rm} -rf build",
            "export A=1; r${X}m -rf build",
            "export A=1; r\x00m -rf build",
            "export A=1; r$(echo)m -rf build",
            "export A=1; r`echo`m -rf build",
            "export A=1; rsync -a --de\\lete a/ b/",
            "@echo off\nr^d /s /q build",
            "@echo off\nr%nope%d /s /q build",
            "cat > .gitignore <<'EOF'\nbuild/\nEOF\ngit -C . clean -fdx",
        ):
            decision = route(
                command,
                config(wrapHeredoc=True, autoRouteBash=True, autoRouteCmd=True),
                bash=BASH,
                cmd=CMD,
            )
            self.assertEqual((decision.shell, decision.reason), ("host", "unsafe-command"), command)

    def test_destructive_options_anywhere_in_the_command_stay_in_host(self) -> None:
        for command in (
            "@echo off\r\nrobocopy src dst ^\r\n  /MIR\r\n",
            "@echo off\nrobocopy src dst -MIR",
            "@echo off\nset OPTS=/MIR\nrobocopy src dst %OPTS%",
            '@echo off\n"C:\\Windows\\System32\\sc.exe" delete svc',
            "@echo off\nsc \\\\server delete svc",
            "@echo off\nschtasks /tn x /delete /f",
            "@echo off\nreg ^\n delete HKCU\\Software\\X /f",
            "@echo off\nnet use Z: /delete",
            "@echo off\nvssadmin delete shadows /all",
            "export A=1; git -C ^\nrepo clean -fdx",
            "export A=1; git reset --hard HEAD~1",
            "export A=1; git branch -D old",
            "export A=1; rsync -a --del src/ dst/",
        ):
            decision = route(
                command, config(autoRouteBash=True, autoRouteCmd=True), bash=BASH, cmd=CMD
            )
            self.assertEqual((decision.shell, decision.reason), ("host", "unsafe-command"), command)

    def test_commit_message_words_do_not_block_a_heredoc(self) -> None:
        for command in (
            "git commit -m \"$(cat <<'EOF'\nfeat: format tables, truncate long names\nEOF\n)\"",
            "git commit -m \"$(cat <<'EOF'\nrefactor: rm unused imports\nEOF\n)\"",
            "git add -A && git commit -m \"$(cat <<'EOF'\nfix: del key\n\nshutdown hook\nEOF\n)\"",
            "gh pr create --title x --body \"$(cat <<'EOF'\n- rm dead code\nEOF\n)\"",
        ):
            decision = route(command, config(wrapHeredoc=True), bash=BASH, cmd=CMD)
            self.assertEqual(
                (decision.shell, decision.reason), ("git-bash", "bash-syntax:heredoc"), command
            )

    def test_only_message_heredocs_are_exempt(self) -> None:
        # Expanded bodies, eval and text after the closing delimiter still run.
        for command in (
            "eval git commit -m \"$(cat <<'EOF'\n$(rm -rf build)\nEOF\n)\"",
            "git commit -m \"$(cat <<EOF\n$(rm -rf build)\nEOF\n)\"",
            "git commit -m \"$(cat <<'EOF'\nmsg\nEOF\nrm -rf build\n)\"",
            "cat <<'EOF' | bash\nrm -rf build\nEOF",
            "git commit -F - <<'EOF'\nmsg\nEOF\nrm -rf build",
        ):
            decision = route(command, config(wrapHeredoc=True), bash=BASH, cmd=CMD)
            self.assertEqual((decision.shell, decision.reason), ("host", "unsafe-command"), command)

    def test_long_options_are_not_cmdlet_names(self) -> None:
        decision = route("docker compose down --remove-orphans && echo ok", config())
        self.assertNotEqual(decision.reason, "powershell-syntax")

    def test_powershell_modulo_is_not_a_batch_variable(self) -> None:
        decision = route(
            "foreach ($i in 1..10) { if ($i%2) { echo $i } }",
            config(autoRouteCmd=True),
            bash=None,
            cmd=CMD,
        )
        self.assertEqual(decision.shell, "host")

    def test_planning_time_grows_linearly_with_length(self) -> None:
        # The hook times out after 10 s. Masked strings keep their line breaks,
        # so an anchored \s* that rescans them makes long commands quadratic.
        body = "\n".join(f"line {i} it's" for i in range(5000))
        settings = config(
            wrapHeredoc=True, autoRouteBash=True, autoRouteCmd=True, flushImplicitOutput=True
        )
        for command in (
            "@'\n" + body + "\n'@ | Set-Content big.txt",
            "cat > big.txt <<'EOF'\n" + body + "\nEOF",
            "echo " + "[[ x " * 20000,
            "Get-Content x | " + "ForEach-Object { try { $_ } catch { } } | " * 5000 + "Sort-Object",
            "Get-Item x|" + "a|" * 40000,
            "set -" + "e" * 80000 + "1",
            "touch a\n" * 20000,
            "export A=1; " + "truncate " * 20000,
            "export A=1; " + "git " * 20000,
            "export A=1\n" + "git commit -m \"$(cat <<'E'\n" * 20000,
            "export A=1; " + "(git " * 20000,
            "@echo off\n" + "robocopy " * 20000,
            "export A=1; " + "$(x" * 40000,
            "export A=1; " + "`x" * 60000,
        ):
            start = time.perf_counter()
            shell.plan_command(command, settings)
            # Each shape takes under 1 s; a quadratic regression takes over 5 s.
            self.assertLess(time.perf_counter() - start, 3.0, command[:40])

    def test_double_brackets_need_bash_spacing(self) -> None:
        bash_case = route("[[ -f setup.sh ]] && echo yes", config(autoRouteBash=True))
        self.assertEqual(bash_case.reason, "bash-syntax:double-brackets")
        generic = route("$t = [Dictionary[[string],[int]]]", config(autoRouteBash=True))
        self.assertEqual(generic.shell, "host")

    def test_source_routes_to_git_bash(self) -> None:
        decision = route(
            "source scripts/setup.sh", config(autoRouteBash=True), bash=BASH, cmd=None
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

    def test_native_command_resolves_no_executables(self) -> None:
        # Resolving executables only when a route needs them keeps the hook cheap.
        with (
            patch.object(shell, "find_git_bash") as bash,
            patch.object(shell, "find_cmd") as cmd,
            patch.object(shell, "find_pwsh") as pwsh,
        ):
            shell.plan_command("git status --short", config())
        bash.assert_not_called()
        cmd.assert_not_called()
        pwsh.assert_not_called()


class HeredocTests(unittest.TestCase):
    def test_real_heredoc_uses_git_bash(self) -> None:
        command = "cat <<'EOF'\nhello\nEOF"
        with (
            patch.object(shell, "find_git_bash", return_value=BASH),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(wrapHeredoc=True))
        self.assertEqual(actual, "WRAPPED")
        wrapper.assert_called_once_with(command, BASH)

    def test_heredoc_text_in_strings_and_comments_is_ignored(self) -> None:
        command = "Write-Output 'cat <<EOF'\n# cat <<EOF"
        with (
            patch.object(shell, "find_git_bash", return_value=BASH),
            patch.object(shell, "find_pwsh", return_value=None),
            patch.object(shell, "wrap_git_bash", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command(command, config(wrapHeredoc=True))
        self.assertEqual(actual, command)
        wrapper.assert_not_called()

    def test_heredoc_inside_quoted_command_substitution_routes_to_bash(self) -> None:
        # The standard commit-message shape agents send: a heredoc inside "$(...)".
        command = (
            'git add -A && git commit -m "$(cat <<\'EOF\'\n'
            "fix: keep Get-Item and $env:PATH out of the way\n"
            "EOF\n)\""
        )
        decision = route(
            command, config(wrapHeredoc=True), bash=BASH, cmd=None
        )
        self.assertEqual(decision.shell, "git-bash")
        self.assertEqual(decision.reason, "bash-syntax:heredoc")

    def test_powershell_head_before_a_heredoc_stays_in_host(self) -> None:
        for command in (
            "Set-Location C:\\work\n$py = 'python.exe'\n& $py - <<'PY'\nprint(1)\nPY",
            "cd C:\\work; $env:PYTHONUTF8='1'; py -3 - <<'EOF'\nprint(1)\nEOF",
            # PowerShell hidden inside strings: variables, a call operator.
            'cd "$env:USERPROFILE\\Projects\\x"\n$PY = "python.exe"\n& $PY - <<\'P\'\nprint(1)\nP',
            '$tok = (git credential fill <<< "" 2>$null)',
            # A drive-letter path with backslashes is garbage to bash.
            "cd C:\\Users\\me\\repo; git add a.md; git commit -m \"$(cat <<'EOF'\nmsg\nEOF\n)\"",
        ):
            decision = route(
                command, config(wrapHeredoc=True), bash=BASH, cmd=None
            )
            self.assertEqual(decision.shell, "host", command)

    def test_destructive_command_next_to_a_heredoc_stays_in_host(self) -> None:
        command = "cat <<'EOF' > notes.txt\nhello\nEOF\nrm -rf build"
        decision = route(
            command, config(wrapHeredoc=True), bash=BASH, cmd=None
        )
        self.assertEqual(decision.shell, "host")
        self.assertEqual(decision.reason, "unsafe-command")

    def test_powershell_here_string_with_heredoc_text_stays_in_host(self) -> None:
        command = "@'\nit's a note about <<EOF and Get-Item\n'@ | Set-Content note.txt"
        decision = route(
            command, config(wrapHeredoc=True), bash=BASH, cmd=None
        )
        self.assertEqual(decision.shell, "host")

    def test_heredoc_routing_can_be_disabled(self) -> None:
        decision = route(
            "cat <<'EOF'\nx\nEOF", config(wrapHeredoc=False), bash=BASH, cmd=None
        )
        self.assertEqual(decision.shell, "host")


class MaskTests(unittest.TestCase):
    def test_masks_strings_and_comments(self) -> None:
        masked = shell.code_mask("echo 'a <<EOF' # b\nWrite-Output \"c\"")
        self.assertNotIn("<<", masked)
        self.assertNotIn("#", masked)
        self.assertIn("Write-Output", masked)
        self.assertEqual(len(masked), len("echo 'a <<EOF' # b\nWrite-Output \"c\""))

    def test_masks_here_strings_including_embedded_quotes(self) -> None:
        text = "@'\nit's <<EOF\n'@ | Out-Null"
        masked = shell.code_mask(text)
        self.assertNotIn("<<", masked)
        self.assertIn("Out-Null", masked)
        double = shell.code_mask('@"\nsay "hi" && more\n"@ | Out-Null')
        self.assertNotIn("&&", double)
        self.assertIn("Out-Null", double)

    def test_keeps_subexpression_code_inside_double_quotes_visible(self) -> None:
        masked = shell.code_mask('"prefix $(cat <<EOF) suffix"')
        self.assertIn("<<EOF", masked)
        self.assertNotIn("prefix", masked)
        self.assertNotIn("suffix", masked)

    def test_hash_inside_a_word_is_not_a_comment(self) -> None:
        self.assertIn("a#b", shell.code_mask("echo a#b"))
        self.assertNotIn("note", shell.code_mask("echo a # note"))

    def test_unterminated_here_string_masks_to_the_end(self) -> None:
        masked = shell.code_mask("@'\nnever closed <<EOF")
        self.assertNotIn("<<", masked)


class AliasAndProxyTests(unittest.TestCase):
    def test_rewrites_only_command_position_aliases(self) -> None:
        command = "ls -la; curl https://example.com | Write-Output"
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(
            actual,
            "Get-ChildItem -Force; curl.exe https://example.com | Write-Output",
        )
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command("curl https://example.com; ls -la; which git", config())
        self.assertEqual(
            actual,
            "curl.exe https://example.com; Get-ChildItem -Force; "
            "(Get-Command git -ErrorAction SilentlyContinue).Source",
        )

    def test_does_not_rewrite_strings_comments_or_arguments(self) -> None:
        command = "Write-Output 'ls -la && curl x'\n# ls -la\nWrite-Output curl"
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command(command, config())
        self.assertEqual(actual, command)

    def test_never_activates_destructive_rm(self) -> None:
        with patch.object(shell, "find_pwsh", return_value=None):
            actual = shell.rewrite_command("rm -rf build", config())
        self.assertEqual(actual, "rm -rf build")


class HostAwareWrapTests(unittest.TestCase):
    """`&&`, `||` and -NoProxy need PowerShell 7; a pwsh 7 host needs no child."""

    def plan(self, command: str, host7, pwsh=PWSH, **overrides):
        with (
            patch.object(shell, "host_is_powershell7", return_value=host7),
            patch.object(shell, "find_pwsh", return_value=pwsh),
        ):
            return shell.plan_command(command, config(**overrides))

    def test_pwsh7_host_runs_chain_operators_in_place(self) -> None:
        for command in ("native-one && native-two", "native-one || native-two"):
            plan = self.plan(command, True)
            self.assertEqual(plan.shell, "host")
            self.assertEqual(plan.command, command)

    def test_windows_powershell_host_wraps_chain_operators(self) -> None:
        plan = self.plan("native-one && native-two", False)
        self.assertEqual(plan.shell, "powershell7")
        self.assertEqual(plan.reason, "powershell7:pipeline-chain")

    def test_unknown_host_wraps_chain_operators(self) -> None:
        self.assertEqual(self.plan("a && b", None).shell, "powershell7")

    def test_without_pwsh_preserves_chain_operators(self) -> None:
        for command in ("native-one && native-two", "native-one || native-two"):
            plan = self.plan(command, False, pwsh=None)
            self.assertEqual(plan.shell, "host")
            self.assertEqual(plan.command, command)
            self.assertEqual(plan.reason, "powershell7-unavailable:pipeline-chain")

    def test_wrap_and_and_can_be_disabled(self) -> None:
        plan = self.plan("a && b", False, wrapAndAnd=False)
        self.assertEqual(plan.shell, "host")

    def test_rewrite_command_wraps_through_wrap_pwsh(self) -> None:
        cfg = config()
        with (
            patch.object(shell, "host_is_powershell7", return_value=False),
            patch.object(shell, "find_pwsh", return_value=PWSH),
            patch.object(shell, "wrap_pwsh", return_value="WRAPPED") as wrapper,
        ):
            actual = shell.rewrite_command("native-one && native-two", cfg)
        self.assertEqual(actual, "WRAPPED")
        wrapper.assert_called_once_with("native-one && native-two", PWSH, cfg)

    def test_noproxy_is_injected_in_place_on_a_pwsh7_host(self) -> None:
        plan = self.plan("Invoke-RestMethod http://192.168.1.10/api", True)
        self.assertEqual(plan.shell, "host")
        self.assertIn("Invoke-RestMethod -NoProxy", plan.command)
        self.assertEqual(plan.changes, ("no-proxy",))

    def test_noproxy_wraps_for_windows_powershell(self) -> None:
        plan = self.plan("Invoke-RestMethod http://192.168.1.10/api", False)
        self.assertEqual(plan.shell, "powershell7")
        self.assertIn("Invoke-RestMethod -NoProxy", plan.command)

    def test_noproxy_requires_pwsh_7(self) -> None:
        command = "Invoke-RestMethod http://192.168.1.10/api"
        plan = self.plan(command, False, pwsh=None)
        self.assertEqual(plan.command, command)

    def test_host_detection_maps_parent_process_names(self) -> None:
        for name, expected in (
            ("pwsh.exe", True),
            ("powershell.exe", False),
            ("cmd.exe", None),
            (None, None),
        ):
            with patch.object(shell, "parent_process_name", return_value=name):
                self.assertIs(shell.host_is_powershell7(), expected)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_parent_process_lookup_names_the_launching_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "probe.py"
            probe.write_text(
                "import sys; sys.path.insert(0, sys.argv[1]);"
                "import rewrite_windows_shell as r; print(r.parent_process_name())",
                encoding="utf-8",
            )
            launcher = Path(directory) / "launch.ps1"
            launcher.write_text("& $args[0] $args[1] $args[2]", encoding="utf-8")
            completed = subprocess.run(
                [
                    str(shell.find_windows_powershell()), "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(launcher),
                    sys.executable, str(probe), str(ROOT / "src"),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(completed.stdout.strip(), "powershell.exe", completed.stderr)


class FlushGuardTests(unittest.TestCase):
    """Cursor ends its script with `exit`, which drops buffered table output."""

    RISKY = (
        "Get-ChildItem | Select-Object Name",
        "Get-Process | Select-Object Name, Id | Select-Object -First 3",
        "Get-ChildItem |\n  Select-Object Name, Length",
        "Get-Service\n  | Select-Object Name, Status",
        "Get-Service | Where-Object Status -eq Running | Select-Object -First 5 Name, Status",
        "[pscustomobject]@{a=1;b=2}",
        "Get-Content x.json | ConvertFrom-Json | Select-Object name, version",
        "Get-ChildItem | select Name",
        "Get-ChildItem | Select-Object Name; git status",
        'Get-Item C:\\Windows | Select-Object Name, Mode; cmd /c "exit 0" && Write-Output ok',
        "rg -n x . | Select-Object -First 5; Get-ChildItem | Select-Object Name",
        "'{\"a\":1}' | ConvertFrom-Json",
        "Get-Content x.csv | ConvertFrom-Csv; Write-Output done",
        "Import-Csv users.csv",
        "Invoke-RestMethod https://api.example.com/items",
        "irm https://api.example.com/items; git status",
        "irm https://api.example.com/items | Where-Object id -gt 3 | Sort-Object name",
        "try { irm https://api.example.com/items } catch { $_ }",
        "git status\nirm https://api.example.com/items",
        "if ($ok) { Import-Csv a.csv | Where-Object { $_.ok } } else { 'none' }",
        "foreach ($u in 'a', 'b') {\n  irm https://x/$u\n}",
        # Records reach the output in too many ways to follow, so any source counts.
        "Import-Csv small.csv | Select-Object -First 2",
        "(Get-Content pkg.json -Raw | ConvertFrom-Json).scripts",
        "$j = Get-Content pkg.json -Raw | ConvertFrom-Json; $j.items",
        "irm https://api.example.com/items | ForEach-Object items",
        "$o = [pscustomobject]@{a=1}; $o",
        "$items = irm https://api.example.com/items",
        "Get-Location",
        "pwd",
        "Get-Date | Get-Member -MemberType Property",
        "$PSVersionTable.PSVersion",
    )
    SAFE = (
        "git status --short",
        "git log --oneline | Select-Object -First 3",
        "Get-ChildItem | Select-Object -ExpandProperty Name",
        "Get-ChildItem | Select-Object Name | Format-Table -AutoSize",
        "Get-ChildItem | Select-Object Name | Out-String",
        "$x = Get-Process | Select-Object Name",
        "Get-Process | Select-Object *",
        "Get-Process | Select-Object a,b,c,d,e",
        "Write-Output 'a | Select-Object x'",
        "Get-ChildItem | Select-Object Name > out.txt",
        "python -c \"print(1)\" | Select-Object -First 1",
        "git status --short; py -3 --version",
        "Get-ChildItem | Select-Object -First 3",
        "using namespace System.IO\nGet-ChildItem | Select-Object Name",
        "param($p)\nGet-ChildItem | Select-Object Name",
        "param($p)\nirm https://api.example.com/items",
        "Write-Output 'irm https://x | ConvertFrom-Json'",
        "Write-Output 'pwd'  # Get-Location",
        "git log --oneline -- gm.txt scripts/pwd",
    )

    def test_flags_commands_that_would_lose_a_table(self) -> None:
        for command in self.RISKY:
            with self.subTest(command=command):
                self.assertTrue(shell.needs_flush_guard(command))

    def test_leaves_other_commands_alone(self) -> None:
        for command in self.SAFE:
            with self.subTest(command=command):
                self.assertFalse(shell.needs_flush_guard(command))

    def test_plan_wraps_in_a_dot_sourced_block(self) -> None:
        command = "Get-ChildItem | Select-Object Name"
        plan = shell.plan_command(command, config(flushImplicitOutput=True))
        self.assertEqual(plan.command, ". {\n" + command + "\n} | Out-Host")
        self.assertIn("flush-guard", plan.changes)

    def test_can_be_disabled(self) -> None:
        command = "Get-ChildItem | Select-Object Name"
        plan = shell.plan_command(command, config(flushImplicitOutput=False))
        self.assertEqual(plan.command, command)

    @unittest.skipUnless(PWSH.is_file(), "PowerShell 7 is not installed")
    def test_run_command_reports_a_failure_after_a_table(self) -> None:
        # The --run wrapper reads $?, which a flush guard would reset to success.
        real_run = subprocess.run

        def quiet(argv, **kwargs):
            return real_run(argv, capture_output=True, **kwargs)

        for command, expected in (
            ("Get-ChildItem C:\\Windows | Select-Object -First 1 Name; Get-Item C:\\agent-missing-x", 1),
            ("Get-ChildItem C:\\Windows | Select-Object -First 1 Name; cmd /c exit 7", 7),
            ("Get-ChildItem C:\\Windows | Select-Object -First 1 Name", 0),
        ):
            with (
                self.subTest(command=command),
                tempfile.TemporaryDirectory() as directory,
                patch.object(shell, "tmp_dir", return_value=Path(directory)),
                patch("subprocess.run", side_effect=quiet),
            ):
                actual = shell.run_command(command, config(flushImplicitOutput=True))
                self.assertEqual(actual, expected)
                self.assertEqual(list(Path(directory).glob("agent-*")), [])


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
class FlushGuardEndToEndTests(unittest.TestCase):
    """Run the guarded command the way Cursor does: inline, then `exit`."""

    def run_script(self, command: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "cursor-like.ps1"
            script.write_text(command + "\nexit $LASTEXITCODE\n", encoding="utf-8")
            return subprocess.run(
                [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )

    def test_table_survives_the_trailing_exit(self) -> None:
        command = "[pscustomobject]@{Alpha=1;Beta=2}; Write-Output done"
        self.assertTrue(shell.needs_flush_guard(command))
        result = self.run_script(shell.flush_guarded(command))
        self.assertIn("Alpha", result.stdout)
        self.assertIn("done", result.stdout)

    def test_other_records_survive_the_trailing_exit(self) -> None:
        for command, expected in (
            ("Get-Location; Write-Output done", "Path"),
            ("'Alpha,Beta\n1,2\n3,4' | ConvertFrom-Csv | Select-Object -First 1; Write-Output done", "Alpha"),
            ("$j = '{\"items\":[{\"Alpha\":1}]}' | ConvertFrom-Json; $j.items; Write-Output done", "Alpha"),
        ):
            with self.subTest(command=command):
                self.assertTrue(shell.needs_flush_guard(command))
                lost = self.run_script(command)
                self.assertNotIn(expected, lost.stdout)
                kept = self.run_script(shell.flush_guarded(command))
                self.assertIn(expected, kept.stdout)
                self.assertIn("done", kept.stdout)

    def test_parsed_json_survives_the_trailing_exit(self) -> None:
        command = "'{\"Alpha\":1,\"Beta\":2}' | ConvertFrom-Json; Write-Output done"
        self.assertTrue(shell.needs_flush_guard(command))
        result = self.run_script(shell.flush_guarded(command))
        self.assertIn("Alpha", result.stdout)
        self.assertIn("done", result.stdout)

    def test_native_exit_code_and_output_survive(self) -> None:
        command = (
            "[pscustomobject]@{Alpha=1;Beta=2}; "
            f'& "{sys.executable}" -c "import sys; print(\'native\'); sys.exit(7)"'
        )
        self.assertTrue(shell.needs_flush_guard(command))
        result = self.run_script(shell.flush_guarded(command))
        self.assertEqual(result.returncode, 7)
        self.assertIn("native", result.stdout)
        self.assertIn("Alpha", result.stdout)


class WrapperTests(unittest.TestCase):
    def test_run_command_propagates_exit_and_cleans_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = subprocess.CompletedProcess([], 17)
            plan = shell.CommandPlan("host", "native-or-powershell", "Write-Output ok", None)
            with (
                patch.object(shell, "find_pwsh", return_value=root / "pwsh.exe"),
                patch.object(shell, "plan_command", return_value=plan),
                patch.object(shell, "tmp_dir", return_value=root),
                patch("subprocess.run", return_value=completed) as runner,
            ):
                actual = shell.run_command("native-command", config())
            self.assertEqual(actual, 17)
            self.assertEqual(list(root.glob("agent-*.ps1")), [])
            argv = runner.call_args.args[0]
            self.assertIn("-NonInteractive", argv)
            self.assertIn("-NoProfile", argv)

    def test_run_command_starts_git_bash_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bash = root / "bash.exe"
            completed = subprocess.CompletedProcess([], 0)
            plan = shell.CommandPlan("git-bash", "bash-syntax:export", "export NAME=agent", bash)
            with (
                patch.object(shell, "plan_command", return_value=plan),
                patch.object(shell, "tmp_dir", return_value=root),
                patch("subprocess.run", return_value=completed) as runner,
            ):
                actual = shell.run_command("export NAME=agent", config())
            self.assertEqual(actual, 0)
            argv = runner.call_args.args[0]
            self.assertEqual(argv[:3], [str(bash), "--noprofile", "--norc"])
            self.assertEqual(runner.call_args.kwargs["env"]["MSYS_NO_PATHCONV"], "1")
            self.assertEqual(list(root.glob("agent-*.sh")), [])

    def test_run_command_sets_up_utf8_because_it_starts_without_a_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seen = {}

            def capture(argv, **_kwargs):
                seen["script"] = Path(argv[-1]).read_text(encoding="utf-8")
                return subprocess.CompletedProcess([], 0)

            plan = shell.CommandPlan("host", "x", "Write-Output ok", None)
            with (
                patch.object(shell, "find_pwsh", return_value=root / "pwsh.exe"),
                patch.object(shell, "plan_command", return_value=plan),
                patch.object(shell, "tmp_dir", return_value=root),
                patch("subprocess.run", side_effect=capture),
            ):
                shell.run_command("x", config(utf8Prefix=False))
        self.assertIn("[Console]::OutputEncoding=$utf8", seen["script"])

    def run_wrapper_in_windows_powershell(self, wrapped: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            host_script = Path(directory) / "host.ps1"
            host_script.write_text(wrapped, encoding="utf-8-sig")
            return subprocess.run(
                [
                    str(shell.find_windows_powershell()), "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(host_script),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

    @unittest.skipUnless(PWSH.is_file(), "PowerShell 7 is not installed")
    def test_pwsh_wrapper_keeps_chain_semantics_and_exit_code(self) -> None:
        # What a Windows PowerShell 5.1 host runs for a command with `&&`.
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(shell, "tmp_dir", return_value=Path(directory)):
                wrapped = shell.wrap_pwsh(
                    "cmd.exe /d /c exit 7 && Write-Output SHOULD_NOT_RUN", PWSH, config()
                )
                result = self.run_wrapper_in_windows_powershell(wrapped)
                leftovers = list(Path(directory).glob("agent-*"))
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn("SHOULD_NOT_RUN", result.stdout)
        self.assertEqual(leftovers, [])

    @unittest.skipUnless(CMD.is_file(), "cmd.exe not found")
    def test_cmd_wrapper_propagates_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(shell, "tmp_dir", return_value=Path(directory)):
                result = self.run_wrapper_in_windows_powershell(
                    shell.wrap_cmd("echo before\r\ncmd /c exit 9", CMD)
                )
                leftovers = list(Path(directory).glob("agent-*"))
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertIn("before", result.stdout)
        self.assertEqual(leftovers, [])

    def test_utf8_prefix_is_lean_and_opt_in(self) -> None:
        self.assertEqual(shell.prepend_utf8("x", config(utf8Prefix=False)), "x")
        prefixed = shell.prepend_utf8("x", config(utf8Prefix=True))
        self.assertTrue(prefixed.endswith("x"))
        self.assertIn("[Console]::OutputEncoding=$utf8", prefixed)
        for expensive in ("InputEncoding", "chcp", "PSNativeCommandUseErrorActionPreference"):
            self.assertNotIn(expensive, prefixed)

    def test_windows_powershell_host_is_read_from_module_path(self) -> None:
        for first, expected in (
            ("C:\\Users\\me\\Documents\\WindowsPowerShell\\Modules", True),
            ("C:\\Users\\me\\OneDrive\\Dokumente\\WindowsPowerShell\\Modules\\", True),
            ("C:\\Users\\me\\Documents\\PowerShell\\Modules", False),
            ("", False),
            # Started outside PowerShell (Explorer, cmd, Claude's exec form).
            ("C:\\Program Files\\WindowsPowerShell\\Modules", False),
            ("C:\\Windows\\system32\\WindowsPowerShell\\v1.0\\Modules", False),
        ):
            value = first + ";C:\\Program Files\\WindowsPowerShell\\Modules"
            env = {
                "PSModulePath": value,
                "ProgramFiles": "C:\\Program Files",
                "ProgramW6432": "C:\\Program Files",
                "SystemRoot": "C:\\Windows",
            }
            with self.subTest(first=first), patch.dict(os.environ, env):
                self.assertIs(REAL_HOST_IS_WINDOWS_POWERSHELL(), expected)
        with patch.dict(os.environ, {"PSModulePath": "C:\\Program Files\\WindowsPowerShell\\Modules"}):
            for name in ("ProgramFiles", "ProgramW6432", "SystemRoot"):
                os.environ.pop(name, None)
            self.assertIs(REAL_HOST_IS_WINDOWS_POWERSHELL(), False)

    def test_windows_powershell_host_gets_utf8_setup(self) -> None:
        # The kit profile is written for PowerShell 7 only.
        with patch.object(shell, "host_is_windows_powershell", return_value=True):
            actual = shell.rewrite_command("git status", config(utf8Prefix=False))
            script = shell.rewrite_command("using namespace System.Text\n[StringBuilder]::new('ok')", config())
        self.assertEqual(actual, shell.UTF8_PREFIX + "git status")
        self.assertFalse(script.startswith(shell.UTF8_PREFIX))

    def test_stale_wrapper_scripts_are_swept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "agent-old.ps1"
            fresh = root / "agent-new.ps1"
            other = root / "keep.txt"
            for path in (old, fresh, other):
                path.write_text("x", encoding="utf-8")
            week_ago = time.time() - 7 * 86400
            os.utime(old, (week_ago, week_ago))
            os.utime(other, (week_ago, week_ago))
            shell._sweep_stale(root)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(other.exists())


class ConfigTests(unittest.TestCase):
    def test_bom_configuration_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps({"utf8Prefix": True}), encoding="utf-8-sig"
            )
            with (
                patch.object(shell, "KIT_ROOT", str(root)),
                patch.dict(os.environ, {"USERPROFILE": str(root / "home")}, clear=True),
            ):
                actual = shell.load_config()
        self.assertTrue(actual["utf8Prefix"])

    def test_defaults_avoid_per_command_rewriting(self) -> None:
        self.assertFalse(shell.DEFAULTS["utf8Prefix"])
        self.assertTrue(shell.DEFAULTS["flushImplicitOutput"])


class HookProtocolTests(unittest.TestCase):
    def test_cursor_response_uses_updated_input(self) -> None:
        payload = {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "ls -la", "working_directory": "C:\\"},
            "transcript_path": "C:\\transcripts\\conversation.jsonl",
        }
        response = shell.hook_response(payload, payload["tool_input"], "Get-ChildItem -Force")
        self.assertEqual(response["permission"], "allow")
        self.assertEqual(response["updated_input"]["command"], "Get-ChildItem -Force")
        self.assertEqual(response["updated_input"]["working_directory"], "C:\\")
        self.assertNotIn("hookSpecificOutput", response)

    def test_unchanged_cursor_payload_allows(self) -> None:
        payload = {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "git status"},
            "transcript_path": "C:\\transcripts\\conversation.jsonl",
        }
        self.assertEqual(shell.hook_response(payload), {"permission": "allow"})

    def test_hook_reads_utf8_stdin_with_bom_under_legacy_code_page(self) -> None:
        payload = {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "echo 你好"},
            "transcript_path": "C:\\transcripts\\conversation.jsonl",
        }
        data = b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdin = io.TextIOWrapper(io.BytesIO(data), encoding="cp936")
        stdout = io.StringIO()
        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(shell, "rewrite_command", lambda cmd: "rewritten " + cmd),
        ):
            shell.hook_main()
        response = json.loads(stdout.getvalue())
        self.assertEqual(response["permission"], "allow")
        self.assertEqual(response["updated_input"]["command"], "rewritten echo 你好")

    def test_claude_response_uses_hook_specific_output(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "PowerShell",
            "tool_input": {"command": "ls -la"},
            "transcript_path": "transcript.jsonl",
        }
        response = shell.hook_response(payload, payload["tool_input"], "Get-ChildItem -Force")
        output = response["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["updatedInput"]["command"], "Get-ChildItem -Force")
        self.assertNotIn("permission", response)

    def test_claude_bash_tool_is_left_to_bash(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la && echo ok"},
        }
        stdout = io.StringIO()
        with (
            patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode()))),
            patch.object(sys, "stdout", stdout),
        ):
            shell.hook_main()
        self.assertEqual(json.loads(stdout.getvalue()), {})

    def test_non_shell_tool_is_ignored(self) -> None:
        tool_input, command = shell.extract_command(
            {"tool_name": "Read", "tool_input": {"command": "ls -la"}}
        )
        self.assertIsNone(tool_input)
        self.assertEqual(command, "")


class HookProcessTests(unittest.TestCase):
    """Run the script the way Cursor does: a real process, a BOM, no PYTHONUTF8."""

    def run_hook(self, command: str, **extra_config: object) -> dict:
        payload = {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": command, "working_directory": "C:\\"},
            "transcript_path": "C:\\transcripts\\conversation.jsonl",
        }
        data = b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "config.json"
            settings.write_text(json.dumps(extra_config), encoding="utf-8")
            env = dict(os.environ)
            env.pop("PYTHONUTF8", None)
            env.update(
                {
                    "PSModulePath": "C:\\Users\\me\\Documents\\PowerShell\\Modules",
                    "USERPROFILE": directory,
                    "AGENT_WINDOWS_SHELL_CONFIG": str(settings),
                }
            )
            completed = subprocess.run(
                [sys.executable, str(SCRIPT)],
                input=data,
                capture_output=True,
                env=env,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        return json.loads(completed.stdout.decode("utf-8"))

    def test_safe_alias_is_rewritten_end_to_end(self) -> None:
        response = self.run_hook("ls -la")
        self.assertEqual(response["permission"], "allow")
        self.assertEqual(response["updated_input"]["command"], "Get-ChildItem -Force")
        self.assertEqual(response["updated_input"]["working_directory"], "C:\\")

    def test_non_ascii_command_survives(self) -> None:
        response = self.run_hook("Write-Output '你好'")
        self.assertEqual(response, {"permission": "allow"})

    def test_table_output_is_guarded_end_to_end(self) -> None:
        response = self.run_hook("Get-ChildItem | Select-Object Name")
        command = response["updated_input"]["command"]
        self.assertTrue(command.startswith(". {"))
        self.assertTrue(command.endswith("} | Out-Host"))

    def test_plain_command_is_left_alone(self) -> None:
        self.assertEqual(self.run_hook("git status --short"), {"permission": "allow"})

    def test_internal_error_fails_open(self) -> None:
        # A malformed setting makes planning raise; the command must still run.
        self.assertEqual(self.run_hook("git status", internalHostPatterns=5), {"permission": "allow"})

    def test_invalid_payload_fails_open(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=b"not json",
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout), {"permission": "allow"})

    def test_common_path_does_not_import_heavy_modules(self) -> None:
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "import rewrite_windows_shell as r;"
            "r.rewrite_command('git status --short', dict(r.DEFAULTS));"
            "r.rewrite_command('Get-ChildItem | Select-Object Name', dict(r.DEFAULTS));"
            "heavy = ('argparse','subprocess','dataclasses','pathlib','tempfile','shutil','inspect','unittest','ctypes');"
            "print(','.join(m for m in heavy if m in sys.modules))"
        )
        completed = subprocess.run(
            [sys.executable, "-S", "-c", code, str(ROOT / "src")],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "")


LEGACY_PROFILE_BLOCK = "\r\n".join(
    (
        "# agent-windows-shell (do not edit; managed by install.ps1)",
        "$root = $env:AGENT_WINDOWS_SHELL_ROOT",
        "if (-not $root) {",
        "    $cfg = Join-Path $env:USERPROFILE '.agent-windows-shell.json'",
        "    if (Test-Path -LiteralPath $cfg) {",
        "        $root = (Get-Content -LiteralPath $cfg -Raw -Encoding UTF8 | ConvertFrom-Json).kitRoot",
        "    }",
        "}",
        "if ($root) {",
        "    $kitProfile = Join-Path $root 'src\\profile.ps1'",
        "    if (Test-Path -LiteralPath $kitProfile) { . $kitProfile }",
        "}",
    )
)
MANAGED_PROFILE_BEGIN = "# >>> agent-windows-shell (managed by install.ps1; do not edit) >>>"
USER_PROFILE_CODE = "function Get-Mine { 'mine' }\r\n"


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "needs Windows PowerShell")
@unittest.skipIf(os.environ.get("AGENT_WINDOWS_SHELL_SELFTEST"), "install.ps1 is running this suite")
class InstallerTests(unittest.TestCase):
    """install.ps1 -Profile in a throwaway home, under the oldest supported host."""

    def install(self, profile: bytes, settings: dict | None = None):
        home = Path(tempfile.mkdtemp(prefix="aws-install-"))
        self.addCleanup(shutil.rmtree, home, True)
        path = home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
        path.parent.mkdir(parents=True)
        path.write_bytes(profile)
        config_path = home / ".agent-windows-shell.json"
        config_path.write_text(json.dumps(settings or {}), encoding="utf-8")
        env = dict(
            os.environ,
            USERPROFILE=str(home),
            APPDATA=str(home / "AppData" / "Roaming"),
            LOCALAPPDATA=str(home / "AppData" / "Local"),
            AGENT_WINDOWS_SHELL_SELFTEST="1",
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(ROOT / "install.ps1"), "-Profile"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        # Windows PowerShell wraps warnings at the console width.
        output = " ".join((completed.stdout + completed.stderr).split())
        self.assertEqual(completed.returncode, 0, output)
        return path, output, json.loads(config_path.read_text(encoding="utf-8-sig"))

    def test_first_release_block_is_replaced_and_user_code_kept(self) -> None:
        original = ("Set-Alias ll Get-ChildItem\r\n" + LEGACY_PROFILE_BLOCK + "\r\n" + USER_PROFILE_CODE).encode()
        path, _output, settings = self.install(original, {"kitRoot": "C:\\old", "utf8Prefix": True})
        text = path.read_bytes().decode("utf-8")
        self.assertNotIn("$root = $env:AGENT_WINDOWS_SHELL_ROOT", text)
        self.assertEqual(text.count(MANAGED_PROFILE_BEGIN), 1)
        self.assertIn("Set-Alias ll Get-ChildItem", text)
        self.assertIn(USER_PROFILE_CODE, text)
        self.assertEqual(path.with_name(path.name + ".agent-windows-shell.bak").read_bytes(), original)
        self.assertNotIn("kitRoot", settings)
        self.assertNotIn("utf8Prefix", settings)

    def test_edited_first_release_block_is_left_alone(self) -> None:
        edited = LEGACY_PROFILE_BLOCK.replace("if ($root) {", "if ($root) {\r\n    Write-Host mine", 1)
        original = (edited + "\r\n" + USER_PROFILE_CODE).encode()
        path, output, settings = self.install(original, {"kitRoot": "C:\\old", "utf8Prefix": True})
        self.assertEqual(path.read_bytes(), original)
        self.assertIn("edited block", output)
        self.assertEqual(settings.get("kitRoot"), "C:\\old")
        self.assertIs(settings.get("utf8Prefix"), True)

    def test_first_release_block_next_to_the_managed_block_is_removed(self) -> None:
        managed = MANAGED_PROFILE_BEGIN + "\nif ($false) { }\n# <<< agent-windows-shell <<<\n"
        original = (LEGACY_PROFILE_BLOCK.replace("\r\n", "\n") + "\n" + managed + USER_PROFILE_CODE).encode()
        path, _output, _settings = self.install(original)
        text = path.read_bytes().decode("utf-8")
        self.assertNotIn("$root = $env:AGENT_WINDOWS_SHELL_ROOT", text)
        self.assertEqual(text.count(MANAGED_PROFILE_BEGIN), 1)
        self.assertNotIn("if ($false)", text)
        self.assertIn(USER_PROFILE_CODE, text)

    def test_utf16_profile_without_bom_is_left_alone(self) -> None:
        original = ("Set-Alias ll Get-ChildItem\r\n").encode("utf-16-le")
        path, output, settings = self.install(original, {"utf8Prefix": True})
        self.assertEqual(path.read_bytes(), original)
        self.assertIn("is not UTF-8", output)
        self.assertIs(settings.get("utf8Prefix"), True)

    def test_profile_keeps_its_bom(self) -> None:
        path, _output, _settings = self.install(b"\xef\xbb\xbfSet-Alias ll Get-ChildItem\r\n")
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbfSet-Alias ll Get-ChildItem"))
        self.assertEqual(raw.count(b"\xef\xbb\xbf"), 1)
        self.assertIn(MANAGED_PROFILE_BEGIN.encode(), raw)


if __name__ == "__main__":
    unittest.main()
