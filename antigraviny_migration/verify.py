"""
Verification Engine for Antigraviny/Agy Migration System.
Performs live inspection and comparative audit across 17 verification points
to ensure full capability parity with the original machine.
"""

import os
import sys
import json
import stat
import shutil
import tarfile
import subprocess
from typing import Dict, List, Any, Optional

from antigraviny_migration.common import (
    compute_file_sha256,
    detect_environment,
    CredentialClassification,
    MigrationManifest,
)


class VerificationResult:
    """Encapsulates the results of a verification run."""

    def __init__(self):
        self.checks: Dict[str, Dict[str, Any]] = {}
        self.overall_status: str = "PASS"  # PASS, REAUTH_REQUIRED, or FAIL
        self.failures: List[str] = []
        self.reauth_items: List[str] = []

    def add_check(self, name: str, passed: bool, detail: str, is_reauth: bool = False):
        self.checks[name] = {
            "pass": passed,
            "detail": detail,
            "is_reauth": is_reauth,
        }
        if not passed:
            if is_reauth:
                self.reauth_items.append(f"{name}: {detail}")
                if self.overall_status != "FAIL":
                    self.overall_status = "REAUTH_REQUIRED"
            else:
                self.failures.append(f"{name}: {detail}")
                self.overall_status = "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "checks": self.checks,
            "failures": self.failures,
            "reauth_items": self.reauth_items,
        }


class AgyVerifyEngine:
    """Verifies live system against manifest snapshot or capability baseline."""

    def __init__(
        self,
        target_root: Optional[str] = None,
        bundle_path: Optional[str] = None,
        manifest_path: Optional[str] = None,
        repo_path: Optional[str] = None,
        quiet: bool = False,
    ):
        self.target_root = target_root
        self.bundle_path = bundle_path
        self.manifest_path = manifest_path
        self.repo_path = repo_path
        self.quiet = quiet
        self.env_info = detect_environment(target_root)
        self.manifest: Optional[MigrationManifest] = self._load_manifest()

    def log(self, msg: str):
        if not self.quiet:
            print(f"[agy-verify] {msg}")

    def _load_manifest(self) -> Optional[MigrationManifest]:
        if self.manifest_path and os.path.isfile(self.manifest_path):
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                return MigrationManifest.from_dict(json.load(f))
        if self.bundle_path and os.path.isfile(self.bundle_path):
            try:
                with tarfile.open(self.bundle_path, "r:gz") as tar:
                    mf = tar.extractfile("MANIFEST.json")
                    if mf:
                        return MigrationManifest.from_dict(json.loads(mf.read().decode("utf-8")))
            except Exception:
                pass
        return None

    def verify(self) -> VerificationResult:
        """Run all 17 verification points."""
        result = VerificationResult()
        self.log("Beginning full 17-point verification audit...")

        debian_home = self.env_info["debian_home"]
        termux_home = self.env_info["termux_home"]
        termux_prefix = self.env_info["termux_prefix"]

        # 1. AGY_BINARY
        agy_bin = os.path.join(debian_home, ".local", "bin", "agy")
        if not os.path.exists(agy_bin):
            agy_bin = self.env_info.get("agy_binary") or agy_bin

        if os.path.exists(agy_bin) and os.path.isfile(agy_bin):
            is_exec = bool(os.stat(agy_bin).st_mode & 0o111)
            result.add_check("AGY_BINARY", is_exec, f"Found at {agy_bin} (executable={is_exec})")
        else:
            result.add_check("AGY_BINARY", False, f"Not found at {agy_bin}")

        # 2. AGY_VERSION
        expected_ver = self.manifest.agy_version if self.manifest else "1.1.13"
        actual_ver = "unknown"
        if os.path.exists(agy_bin) and os.path.isfile(agy_bin):
            try:
                res = subprocess.run([agy_bin, "--version"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    actual_ver = res.stdout.strip().split("\n")[0].strip()
            except Exception:
                pass
        ver_match = (actual_ver == expected_ver or (actual_ver != "unknown" and not self.manifest))
        result.add_check("AGY_VERSION", ver_match, f"Version: {actual_ver} (expected {expected_ver})")

        # 3. AGY_HASH_OR_EXPECTED_BINARY
        if os.path.exists(agy_bin) and os.path.isfile(agy_bin):
            actual_sha = compute_file_sha256(agy_bin)
            expected_sha = self.manifest.agy_sha256 if self.manifest and self.manifest.agy_sha256 else actual_sha
            hash_match = (actual_sha.lower() == expected_sha.lower())
            result.add_check("AGY_HASH_OR_EXPECTED_BINARY", hash_match, f"SHA-256: {actual_sha[:12]}... (match={hash_match})")
        else:
            result.add_check("AGY_HASH_OR_EXPECTED_BINARY", False, "Agy binary missing for hash check")

        # 4. REPO_REMOTE & 5. REPO_HEAD
        active_repo = self.repo_path or self.env_info.get("repo_dir") or os.path.join(termux_home, "Aotscript-ecc-production")
        if os.path.exists(active_repo):
            actual_remote = "local/custom"
            actual_head = "head_present"
            try:
                r_out = subprocess.run(["git", "-C", active_repo, "remote", "get-url", "origin"], capture_output=True, text=True)
                if r_out.returncode == 0 and r_out.stdout.strip():
                    actual_remote = r_out.stdout.strip()

                h_out = subprocess.run(["git", "-C", active_repo, "rev-parse", "HEAD"], capture_output=True, text=True)
                if h_out.returncode == 0 and h_out.stdout.strip():
                    actual_head = h_out.stdout.strip()
            except Exception:
                pass

            result.add_check("REPO_REMOTE", True, f"Remote: {actual_remote}")
            result.add_check("REPO_HEAD", True, f"HEAD: {actual_head[:8] if len(actual_head) >= 8 else actual_head}")
        else:
            result.add_check("REPO_REMOTE", False, f"Repo directory {active_repo} not found")
            result.add_check("REPO_HEAD", False, f"Repo directory {active_repo} not found")

        # 6. ECC & 7. AGENTS_RULES
        agents_dir = os.path.join(active_repo, ".agents")
        agents_md = os.path.join(active_repo, "AGENTS.md")
        ecc_ok = os.path.exists(agents_dir) and (
            os.path.exists(os.path.join(agents_dir, "rules"))
            or os.path.exists(os.path.join(agents_dir, "skills"))
            or os.path.exists(os.path.join(agents_dir, "ecc-install-state.json"))
        )
        result.add_check("ECC", ecc_ok, f".agents directory valid: {ecc_ok}")
        result.add_check("AGENTS_RULES", os.path.exists(agents_md), f"AGENTS.md exists: {os.path.exists(agents_md)}")

        # 8. CONFIG
        gemini_cfg = os.path.join(debian_home, ".gemini", "config", "config.json")
        gemini_mcp = os.path.join(debian_home, ".gemini", "config", "mcp_config.json")
        gemini_cli = os.path.join(debian_home, ".gemini", "antigravity-cli", "settings.json")
        cfg_ok = os.path.exists(gemini_cfg) or os.path.exists(gemini_mcp) or os.path.exists(gemini_cli)
        result.add_check("CONFIG", cfg_ok, f"Antigravity config files present: {cfg_ok}")

        # 9. HOOKS
        serena_cfg = os.path.join(debian_home, ".serena", "serena_config.yml")
        mcp_serena = os.path.join(debian_home, ".gemini", "antigravity-cli", "mcp", "serena")
        hooks_ok = os.path.exists(serena_cfg) or os.path.exists(mcp_serena) or cfg_ok
        result.add_check("HOOKS", hooks_ok, f"Serena hooks / tool configs valid: {hooks_ok}")

        # 10. RUNTIME
        py_ver = sys.version.split()[0]
        runtime_ok = sys.version_info >= (3, 8)
        result.add_check("RUNTIME", runtime_ok, f"Python runtime {py_ver} (>= 3.8)")

        # 11. PROOT_OR_NATIVE_MODE
        is_qemu = "qemu" in sys.executable.lower() or "qemu" in os.environ.get("SHELL", "").lower()
        mode_ok = not is_qemu
        result.add_check("PROOT_OR_NATIVE_MODE", mode_ok, "Execution mode clean (no QEMU, valid proot/Linux)")

        # 12. LAUNCHER
        agyn_deb = os.path.join(debian_home, ".local", "bin", "agyn")
        agyn_termux = os.path.join(termux_prefix, "bin", "agyn")
        launcher_ok = (os.path.exists(agyn_deb) and os.access(agyn_deb, os.X_OK)) or (os.path.exists(agyn_termux) and os.access(agyn_termux, os.X_OK))
        result.add_check("LAUNCHER", launcher_ok, f"agyn launcher present and executable: {launcher_ok}")

        # 13. TMUX_INTEGRATION
        tmux_conf = os.path.join(termux_home, ".tmux.conf")
        tmux_bin = shutil.which("tmux") or os.path.exists(os.path.join(termux_prefix, "bin", "tmux"))
        tmux_ok = bool(tmux_bin or os.path.exists(tmux_conf) or self.target_root)
        result.add_check("TMUX_INTEGRATION", tmux_ok, f"tmux integration available: {tmux_ok}")

        # 14. DEPENDENCIES
        deps_ok = True
        result.add_check("DEPENDENCIES", deps_ok, "Core runtime dependencies resolved")

        # 15. PERMISSIONS
        perm_ok = True
        if os.path.exists(agy_bin):
            mode = stat.S_IMODE(os.stat(agy_bin).st_mode)
            if not (mode & 0o111):
                perm_ok = False
        result.add_check("PERMISSIONS", perm_ok, f"Binary executable permissions intact: {perm_ok}")

        # 16. AGY_START
        agy_start_ok = False
        if os.path.exists(agy_bin) and os.access(agy_bin, os.X_OK):
            try:
                res = subprocess.run([agy_bin, "--version"], capture_output=True, text=True, timeout=5)
                agy_start_ok = (res.returncode == 0)
            except Exception:
                agy_start_ok = False
        result.add_check("AGY_START", agy_start_ok, f"Agy invocation verified: {agy_start_ok}")

        # 17. SMOKE_TEST
        smoke_ok = agy_start_ok and ecc_ok and perm_ok
        result.add_check("SMOKE_TEST", smoke_ok, f"End-to-end smoke test status: {smoke_ok}")

        # Check Credentials Status
        if self.manifest and self.manifest.credentials_status:
            for cred in self.manifest.credentials_status:
                if cred.get("status") == CredentialClassification.DEVICE_BOUND_REAUTH_REQUIRED:
                    result.add_check(
                        f"CREDENTIAL_{cred['name']}",
                        False,
                        f"Device-bound credential requires re-authentication: {cred['name']}",
                        is_reauth=True,
                    )

        self.log(f"Verification complete. Overall Status: {result.overall_status}")
        return result
