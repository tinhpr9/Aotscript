#!/usr/bin/env python3
"""PTY and process-lock regression tests for aotsetup."""

from __future__ import annotations

import fcntl
import hashlib
import os
import pathlib
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time

from launcher_test_support import launcher_command


SETUP = pathlib.Path(os.environ.get("AOTSCRIPT_SETUP_UNDER_TEST", "setup.sh")).resolve()
TIMEOUT = int(os.environ.get("AOTSCRIPT_TEST_TIMEOUT", "30"))
PASSED: list[str] = []


def passed(name: str) -> None:
    PASSED.append(name)
    print(f"{name}=PASS", flush=True)


def proc_start(pid: int) -> str:
    text = pathlib.Path(f"/proc/{pid}/stat").read_text()
    return text.rsplit(")", 1)[1].split()[19]


def proc_uid(pid: int) -> str:
    for line in pathlib.Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("Uid:"):
            return line.split()[1]
    raise AssertionError("missing proc uid")


def fixture(root: pathlib.Path, device_id: str = "m88", group: str = "NOVA") -> None:
    state = root / "state/aotscript"
    setup_dir = state / "setup-driver"
    shouko = root / "storage/Download/Shouko"
    setup_dir.mkdir(parents=True)
    shouko.mkdir(parents=True)
    (root / "home").mkdir()
    (root / "prefix/bin").mkdir(parents=True)
    host_hash = hashlib.sha256(b"test-host").hexdigest()
    (setup_dir / "device_id").write_text(device_id + "\n")
    (setup_dir / "device_group").write_text(group + "\n")
    (setup_dir / "host_fingerprint").write_text(host_hash + "\n")
    (setup_dir / "bootstrap_ui_done").write_text("yes\n")
    (setup_dir / "provision_initialized").write_text("yes\n")
    (state / "mprovision.json").write_text(
        '{"device_id":"%s","device_group":"%s","phase":"complete"}\n'
        % (device_id, group)
    )
    (shouko / "device_id.txt").write_text(device_id + "\n")
    (shouko / "device_group.txt").write_text(group + "\n")


def fresh_dirs(root: pathlib.Path) -> None:
    (root / "home").mkdir(parents=True)
    (root / "prefix/bin").mkdir(parents=True)
    (root / "storage").mkdir(parents=True)


def base_env(root: pathlib.Path, input_mode: str = "env") -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(root / "home"),
            "XDG_STATE_HOME": str(root / "state"),
            "PREFIX": str(root / "prefix"),
            "AOTSCRIPT_SETUP_TEST_MODE": "1",
            "AOTSCRIPT_SETUP_INPUT_MODE": input_mode,
            "AOTSCRIPT_SETUP_STORAGE_ROOT": str(root / "storage"),
            "AOTSCRIPT_SETUP_HOST_ID": "test-host",
            "AOTSCRIPT_SETUP_DRY_RUN": "1",
            "AOTSCRIPT_SETUP_DEVICE_ID": "m88",
            "AOTSCRIPT_SETUP_GROUP": "NOVA",
            "AOTSCRIPT_SETUP_CONFIRM": "yes",
        }
    )
    return env


def run_setup(root: pathlib.Path, *, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = base_env(root)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SETUP)],
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=TIMEOUT,
        check=False,
    )


class PtySetup:
    def __init__(self, root: pathlib.Path, env_extra: dict[str, str] | None = None):
        self.master, self.slave = pty.openpty()
        env = base_env(root, "tty")
        if env_extra:
            env.update(env_extra)

        def child_setup() -> None:
            os.setsid()
            fcntl.ioctl(self.slave, termios.TIOCSCTTY, 0)

        self.process = subprocess.Popen(
            ["bash", str(SETUP)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self.slave,
            stderr=self.slave,
            preexec_fn=child_setup,
            close_fds=True,
        )
        os.set_blocking(self.master, False)
        self.output = bytearray()

    def read_until(self, needle: bytes, timeout: float = TIMEOUT) -> bytes:
        deadline = time.monotonic() + timeout
        while needle not in self.output and time.monotonic() < deadline:
            readable, _, _ = select.select([self.master], [], [], 0.1)
            if readable:
                try:
                    self.output.extend(os.read(self.master, 65536))
                except OSError:
                    break
            if self.process.poll() is not None and needle not in self.output:
                break
        if needle not in self.output:
            raise AssertionError(
                f"PTY did not show {needle!r}; rc={self.process.poll()} output={self.output[-1000:]!r}"
            )
        return bytes(self.output)

    def send(self, value: bytes) -> None:
        os.write(self.master, value)

    def finish(self, timeout: float = TIMEOUT) -> tuple[int, bytes]:
        rc = self.process.wait(timeout=timeout)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.master], [], [], 0.05)
            if not readable:
                break
            try:
                self.output.extend(os.read(self.master, 65536))
            except OSError:
                break
        return rc, bytes(self.output)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        os.close(self.master)
        os.close(self.slave)


def wait_lock(root: pathlib.Path, state: str | None = None) -> pathlib.Path:
    lock = root / "state/aotscript/setup-driver/setup.lock"
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        try:
            current = (lock / "state").read_text().strip()
            if state is None or current == state:
                return lock
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"lock state not reached: {state}")


def write_lock(
    root: pathlib.Path,
    pid: int,
    *,
    start: str | None = None,
    state: str = "RUNNING_STEP",
    script: pathlib.Path = SETUP,
    legacy: bool = False,
) -> pathlib.Path:
    lock = root / "state/aotscript/setup-driver/setup.lock"
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "pid").write_text(f"{pid}\n")
    if legacy:
        return lock
    (lock / "start_time").write_text((start or proc_start(pid)) + "\n")
    (lock / "state").write_text(state + "\n")
    (lock / "uid").write_text(proc_uid(pid) + "\n")
    (lock / "state_dir").write_text(str(lock.parent) + "\n")
    (lock / "script_path").write_text(str(script) + "\n")
    (lock / "identity").write_text("AOTSETUP_LOCK_V2\n")
    return lock


def test_prompt_pty_and_redirect(root: pathlib.Path) -> None:
    case = root / "prompt"
    fresh_dirs(case)
    child = PtySetup(case)
    try:
        child.read_until(b"Device ID")
        child.send(b"M88\n")
        child.read_until(b"Nh\xc3\xb3m hi\xe1\xbb\x87n t\xe1\xba\xa1i")
        child.send(b"nova\n")
        child.read_until(b"[y/N]")
        child.send(b"yes\n")
        rc, output = child.finish()
        assert rc == 0, output
        assert b"M88" in output and b"nova" in output and b"yes" in output
        setup_state = case / "state/aotscript/setup-driver"
        assert (setup_state / "device_id").read_text().strip() == "m88"
        assert (setup_state / "device_group").read_text().strip() == "NOVA"
    finally:
        child.close()
    passed("AOTSETUP_PTY_PROMPT")
    passed("AOTSETUP_REDIRECTED_STDIN_TTY")
    passed("AOTSETUP_PROMPT_ECHO")


def test_interactive_without_tty_fails_cleanly(root: pathlib.Path) -> None:
    case = root / "no-tty"
    fresh_dirs(case)
    result = subprocess.run(
        ["bash", str(SETUP)],
        env=base_env(case, "tty"),
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=TIMEOUT,
        start_new_session=True,
        check=False,
    )
    assert result.returncode != 0
    assert "không mở được /dev/tty" in result.stderr
    assert not (case / "state/aotscript/setup-driver/setup.lock").exists()
    passed("AOTSETUP_INTERACTIVE_NO_TTY_FAILS_CLEANLY")


def test_su_cannot_steal_input(root: pathlib.Path) -> None:
    case = root / "su"
    fresh_dirs(case)
    fake = case / "fake-bin"
    fake.mkdir()
    marker = case / "su-marker"
    su = fake / "su"
    su.write_text(
        "#!/usr/bin/env bash\n"
        "if IFS= read -r -t 1 value; then printf STOLEN; else printf EOF; fi > \"$AOT_SU_MARKER\"\n"
    )
    su.chmod(0o700)
    child = PtySetup(
        case,
        {
            "PATH": str(fake) + os.pathsep + os.environ["PATH"],
            "AOT_SU_MARKER": str(marker),
            "AOTSCRIPT_SETUP_TEST_SU_STDIN_PROBE": "1",
        },
    )
    try:
        child.read_until(b"Device ID")
        child.send(b"m88\nNOVA\nyes\n")
        rc, output = child.finish()
        assert rc == 0, output
        assert marker.read_text() == "EOF"
    finally:
        child.close()
    passed("AOTSETUP_SU_STDIN_ISOLATED")


def test_echo_and_ctrl_c(root: pathlib.Path) -> None:
    case = root / "interrupt"
    fresh_dirs(case)
    child = PtySetup(case)
    try:
        attrs = termios.tcgetattr(child.slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(child.slave, termios.TCSANOW, attrs)
        child.read_until(b"Device ID")
        assert termios.tcgetattr(child.slave)[3] & termios.ECHO
        time.sleep(0.05)
        child.send(b"\x03")
        rc, _ = child.finish()
        assert rc == 130
        assert termios.tcgetattr(child.slave)[3] & termios.ECHO
        assert not (case / "state/aotscript/setup-driver/setup.lock").exists()
    finally:
        child.close()
    passed("AOTSETUP_RESTORES_ECHO")
    passed("AOTSETUP_CTRL_C_CLEANUP")


def test_stale_locks(root: pathlib.Path) -> None:
    dead_case = root / "dead"
    fixture(dead_case)
    sleeper = subprocess.Popen(["sleep", "0.01"])
    dead_pid = sleeper.pid
    sleeper.wait()
    write_lock(dead_case, dead_pid, legacy=True)
    result = run_setup(dead_case)
    assert result.returncode == 0, result.stderr
    passed("AOTSETUP_DEAD_PID_LOCK")

    legacy_case = root / "legacy"
    fixture(legacy_case)
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        write_lock(legacy_case, sleeper.pid, legacy=True)
        result = run_setup(legacy_case)
        assert result.returncode == 0, result.stderr
        assert sleeper.poll() is None
    finally:
        sleeper.terminate()
        sleeper.wait()
    passed("AOTSETUP_LEGACY_PID_LOCK")

    reused_case = root / "reused"
    fixture(reused_case)
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        wrong = str(int(proc_start(sleeper.pid)) + 1)
        write_lock(reused_case, sleeper.pid, start=wrong)
        result = run_setup(reused_case)
        assert result.returncode == 0, result.stderr
        assert sleeper.poll() is None
    finally:
        sleeper.terminate()
        sleeper.wait()
    passed("AOTSETUP_REUSED_PID_LOCK")


def test_running_and_simultaneous(root: pathlib.Path) -> None:
    case = root / "running"
    fixture(case)
    env = base_env(case)
    env["AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS"] = "10"
    owner = subprocess.Popen(
        ["bash", str(SETUP)], env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        wait_lock(case, "RUNNING_STEP")
        second = run_setup(case)
        assert second.returncode != 0
        assert "RUNNING_STEP" in second.stderr
    finally:
        owner.wait(timeout=TIMEOUT)
    passed("AOTSETUP_RUNNING_STEP_BLOCKS")

    case2 = root / "simultaneous"
    fixture(case2)
    env2 = base_env(case2)
    env2["AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS"] = "1"
    processes = [
        subprocess.Popen(
            ["bash", str(SETUP)], env=env2, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(2)
    ]
    results = [process.wait(timeout=TIMEOUT) for process in processes]
    assert sorted(results) == [0, 1], results
    passed("AOTSETUP_SIMULTANEOUS_SINGLE_OWNER")


def test_waiting_takeover(root: pathlib.Path) -> None:
    case = root / "takeover"
    fresh_dirs(case)
    owner = PtySetup(case)
    try:
        owner.read_until(b"Device ID")
        wait_lock(case, "WAITING_INPUT")
        replacement = run_setup(case)
        assert replacement.returncode == 0, replacement.stderr
        assert "Tiếp quản" in replacement.stdout
        owner_rc, _ = owner.finish()
        assert owner_rc == 143
        assert not (case / "state/aotscript/setup-driver/setup.lock").exists()
    finally:
        owner.close()
    passed("AOTSETUP_WAITING_INPUT_TAKEOVER")


def test_non_aot_not_killed(root: pathlib.Path) -> None:
    case = root / "not-aot"
    fixture(case)
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        write_lock(case, sleeper.pid)
        result = run_setup(case)
        assert result.returncode == 0, result.stderr
        assert sleeper.poll() is None
    finally:
        sleeper.terminate()
        sleeper.wait()
    passed("AOTSETUP_NON_AOT_PROCESS_PRESERVED")


def test_update_while_waiting(root: pathlib.Path) -> None:
    case = root / "update"
    fresh_dirs(case)
    owner = PtySetup(case)
    try:
        owner.read_until(b"Device ID")
        wait_lock(case, "WAITING_INPUT")
        launcher = case / "prefix/bin/aotsetup"
        command = launcher_command(launcher, ["update"])
        result = subprocess.run(
            command,
            env={
                **base_env(case),
                "AOTSCRIPT_SETUP_UPDATE_SOURCE": str(SETUP),
            },
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "aotsetup đã update từ main" in result.stdout
        assert owner.process.poll() is None
        assert wait_lock(case, "WAITING_INPUT")
        owner.send(b"\x03")
        owner.finish()
    finally:
        owner.close()
    passed("AOTSETUP_UPDATE_BYPASSES_WAITING_LOCK")


def main() -> None:
    if not SETUP.is_file():
        raise SystemExit(f"missing setup under test: {SETUP}")
    with tempfile.TemporaryDirectory(prefix="aotsetup-terminal-lock-") as temp:
        root = pathlib.Path(temp)
        tests = [
            test_prompt_pty_and_redirect,
            test_interactive_without_tty_fails_cleanly,
            test_su_cannot_steal_input,
            test_echo_and_ctrl_c,
            test_stale_locks,
            test_running_and_simultaneous,
            test_waiting_takeover,
            test_non_aot_not_killed,
            test_update_while_waiting,
        ]
        selected = os.environ.get("AOTSCRIPT_TERMINAL_LOCK_TEST_FILTER", "")
        if selected:
            tests = [test for test in tests if test.__name__ == selected]
            if not tests:
                raise AssertionError(f"unknown terminal-lock test filter: {selected}")
        for test in tests:
            test(root)
    print(f"AOTSETUP_TERMINAL_LOCK_TESTS={len(PASSED)}_PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"AOTSETUP_TERMINAL_LOCK_TEST=FAIL:{type(exc).__name__}:{exc}", file=sys.stderr)
        raise
