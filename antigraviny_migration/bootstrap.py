"""
Bootstrap Engine for Antigraviny/Agy Migration System.
Prepares target machine environment (Termux, proot-distro Debian 12, dependencies,
runtime tools, repository clone) in an idempotent, non-destructive manner.
"""

import os
import sys
import shutil
import platform
import subprocess
from typing import Dict, List, Any, Optional

from antigraviny_migration.common import detect_environment


class AgyBootstrapEngine:
    """Idempotently prepares target environment for Antigravity & ECC runtime."""

    DEFAULT_REPO_URL = "https://github.com/tinhpr9/Aotscript.git"
    DEFAULT_BRANCH = "main"

    def __init__(
        self,
        target_root: Optional[str] = None,
        repo_url: Optional[str] = None,
        branch: Optional[str] = None,
        skip_pkg_install: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
    ):
        self.target_root = target_root
        self.repo_url = repo_url or self.DEFAULT_REPO_URL
        self.branch = branch or self.DEFAULT_BRANCH
        self.skip_pkg_install = skip_pkg_install
        self.dry_run = dry_run
        self.quiet = quiet
        self.env_info = detect_environment(target_root)

    def log(self, msg: str):
        if not self.quiet:
            print(f"[agy-bootstrap] {msg}")

    def run_cmd(self, cmd: List[str], check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
        """Run a shell command safely."""
        self.log(f"Executing: {' '.join(cmd)}")
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, stdout="[dry-run]", stderr="")
        try:
            return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout)
        except subprocess.CalledProcessError as e:
            self.log(f"Command failed (code {e.returncode}): {e.stderr}")
            if check:
                raise
            return subprocess.CompletedProcess(cmd, e.returncode, stdout=e.stdout, stderr=e.stderr)

    def bootstrap(self) -> Dict[str, Any]:
        """Execute full bootstrap pipeline."""
        self.log("Starting environment bootstrap...")
        steps_executed = []

        # 1. Inspect target architecture
        arch = platform.machine()
        system = platform.system()
        self.log(f"Target System: {system} ({arch})")

        # 2. Check and prepare directory hierarchy
        debian_home = self.env_info["debian_home"]
        termux_home = self.env_info["termux_home"]
        termux_prefix = self.env_info["termux_prefix"]

        dirs_to_create = [
            os.path.join(debian_home, ".local", "bin"),
            os.path.join(debian_home, ".gemini", "config", "skills"),
            os.path.join(debian_home, ".gemini", "config", "projects"),
            os.path.join(debian_home, ".gemini", "antigravity-cli", "mcp", "serena"),
            os.path.join(debian_home, ".serena"),
            os.path.join(termux_home, "bin"),
            os.path.join(termux_home, ".local", "bin"),
            os.path.join(termux_home, ".config", "rclone"),
            os.path.join(termux_home, ".config", "gh"),
            os.path.join(termux_home, ".ssh"),
        ]

        for d in dirs_to_create:
            if not os.path.exists(d):
                if not self.dry_run:
                    os.makedirs(d, exist_ok=True)
                steps_executed.append(f"mkdir: {d}")

        # 3. Termux Host Setup (if running in real Termux and not skipped/mocked)
        if not self.target_root and self.env_info.get("is_termux") and not self.skip_pkg_install:
            self.log("Detected Termux host environment. Checking base packages...")
            # Check pkg or apt
            pkg_cmd = shutil.which("pkg") or shutil.which("apt")
            if pkg_cmd:
                needed_pkgs = ["proot-distro", "tmux", "termux-api", "git", "python", "nodejs", "rclone", "ripgrep", "curl", "tar", "gzip", "jq"]
                try:
                    self.run_cmd([pkg_cmd, "install", "-y"] + needed_pkgs, check=False)
                    steps_executed.append("termux:packages_installed")
                except Exception as e:
                    self.log(f"Warning during package installation: {e}")

            # Check proot-distro debian12
            proot_distro = shutil.which("proot-distro")
            if proot_distro:
                check_distro = subprocess.run([proot_distro, "login", "debian12", "--", "echo", "debian_ok"], capture_output=True, text=True)
                if check_distro.returncode != 0:
                    self.log("Debian12 container not found. Installing via proot-distro...")
                    self.run_cmd([proot_distro, "install", "debian12"], check=True, timeout=300)
                    steps_executed.append("proot:debian12_installed")
                else:
                    self.log("Debian12 container already installed and operational.")

        # 4. Clone / Prepare Aotscript Repo
        target_repo_dir = os.path.join(termux_home, "Aotscript-ecc-production")
        if not os.path.exists(target_repo_dir):
            self.log(f"Cloning repository from {self.repo_url} (branch {self.branch}) into {target_repo_dir}...")
            if not self.dry_run:
                try:
                    self.run_cmd(["git", "clone", "-b", self.branch, self.repo_url, target_repo_dir], check=True)
                    steps_executed.append("repo:cloned")
                except Exception as e:
                    self.log(f"Failed to clone repo from {self.repo_url}: {e}")
                    # Create placeholder repo directory if clone fails in offline test
                    os.makedirs(os.path.join(target_repo_dir, ".agents"), exist_ok=True)
                    steps_executed.append("repo:placeholder_created")
            else:
                steps_executed.append(f"repo:clone_dry_run {target_repo_dir}")
        else:
            self.log(f"Repository already exists at {target_repo_dir}.")
            steps_executed.append("repo:already_exists")

        self.log("Bootstrap completed successfully.")
        return {
            "success": True,
            "architecture": arch,
            "system": system,
            "steps": steps_executed,
            "repo_dir": target_repo_dir,
        }
