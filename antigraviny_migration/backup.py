"""
Backup Engine for Antigraviny/Agy Migration System.
Discovers and bundles Antigravity CLI binary, configs, skills, launchers,
runtime metadata, and portable state into an authenticated archive.
"""

import os
import sys
import json
import stat
import shutil
import tarfile
import tempfile
import subprocess
from typing import Dict, List, Any, Optional, Tuple

from antigraviny_migration.common import (
    compute_file_sha256,
    compute_bytes_sha256,
    mask_secret,
    detect_environment,
    CredentialClassification,
    MigrationManifest,
    FileEntry,
)


class AgyBackupEngine:
    """Creates a complete migration bundle from the current machine."""

    def __init__(
        self,
        source_root: Optional[str] = None,
        agy_binary_path: Optional[str] = None,
        repo_path: Optional[str] = None,
        output_path: Optional[str] = None,
        include_binary: bool = True,
        quiet: bool = False,
    ):
        self.source_root = source_root
        self.env_info = detect_environment(source_root)
        self.agy_binary_path = agy_binary_path or self.env_info.get("agy_binary")
        self.repo_path = repo_path or self.env_info.get("repo_dir")
        self.output_path = output_path or os.path.abspath("agy-migration-bundle.tar.gz")
        self.include_binary = include_binary
        self.quiet = quiet

    def log(self, msg: str):
        if not self.quiet:
            print(f"[agy-backup] {msg}")

    def _resolve_source_path(self, target_tag: str, rel_path: str) -> str:
        """Resolve absolute path on source filesystem based on logical tag."""
        if target_tag == "debian_home":
            base = self.env_info["debian_home"]
        elif target_tag == "termux_home":
            base = self.env_info["termux_home"]
        elif target_tag == "termux_usr_bin":
            base = os.path.join(self.env_info["termux_prefix"], "bin")
        elif target_tag == "termux_home_bin":
            base = os.path.join(self.env_info["termux_home"], "bin")
        elif target_tag == "repo":
            base = self.repo_path or self.env_info["termux_home"]
        else:
            base = self.env_info["debian_home"]
        return os.path.join(base, rel_path)

    def _get_agy_version(self, binary_path: str) -> str:
        """Execute or inspect agy binary to retrieve version."""
        if not os.path.isfile(binary_path):
            return "unknown"
        try:
            res = subprocess.run([binary_path, "--version"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip().split("\n")[0].strip()
        except Exception:
            pass
        return "unknown"

    def _get_repo_git_info(self) -> Tuple[str, str, str]:
        """Extract remote, HEAD commit SHA, and branch from repo."""
        if not self.repo_path or not os.path.exists(self.repo_path):
            return ("", "", "")
        
        remote = ""
        head = ""
        branch = ""
        try:
            r = subprocess.run(["git", "-C", self.repo_path, "remote", "get-url", "origin"], capture_output=True, text=True)
            if r.returncode == 0:
                remote = r.stdout.strip()
            
            h = subprocess.run(["git", "-C", self.repo_path, "rev-parse", "HEAD"], capture_output=True, text=True)
            if h.returncode == 0:
                head = h.stdout.strip()
                
            b = subprocess.run(["git", "-C", self.repo_path, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
            if b.returncode == 0:
                branch = b.stdout.strip()
        except Exception:
            pass
        return (remote, head, branch)

    def _discover_candidate_files(self) -> List[Tuple[str, str, str]]:
        """
        Returns list of (target_tag, rel_path_in_tag, description).
        """
        candidates = [
            # Launchers & Binaries
            ("debian_home", ".local/bin/agyn", "Debian agyn launcher"),
            ("termux_usr_bin", "agyn", "Termux host agyn launcher"),
            ("termux_usr_bin", "agy-watch", "Termux host agy-watch notification monitor"),
            ("termux_home_bin", "toolcheck", "Toolcheck script"),
            ("termux_home_bin", "csupload", "CSUpload script"),
            ("termux_home", ".local/bin/csrun", "CSRun script"),
            ("termux_home", ".local/bin/auto-ss.sh", "Auto screenshot script"),
            
            # Gemini / Antigravity configs
            ("debian_home", ".gemini/config/config.json", "Antigravity core config"),
            ("debian_home", ".gemini/config/mcp_config.json", "MCP servers config"),
            ("debian_home", ".gemini/config/projects/default-cli-project.json", "Default CLI project config"),
            ("debian_home", ".gemini/antigravity-cli/settings.json", "Antigravity CLI settings"),
            
            # Auth & session files (portable local state)
            ("debian_home", ".gemini/antigravity-cli/antigravity-oauth-token", "Antigravity OAuth Token"),
            ("debian_home", ".gemini/antigravity-cli/installation_id", "Installation ID"),
            ("debian_home", ".gemini/antigravity-cli/jetski_state.pbtxt", "Session state"),
            
            # Shell & Tmux configs
            ("debian_home", ".serena/serena_config.yml", "Serena LSP configuration"),
            ("debian_home", ".bashrc", "Debian bashrc"),
            ("debian_home", ".profile", "Debian profile"),
            ("termux_home", ".tmux.conf", "Tmux configuration"),
            ("termux_home", ".bashrc", "Termux bashrc"),
            ("termux_home", ".config/rclone/rclone.conf", "Rclone config"),
            ("debian_home", ".config/gh/hosts.yml", "GitHub CLI hosts"),
            ("debian_home", ".config/gh/config.yml", "GitHub CLI config"),
            ("termux_home", ".config/gh/hosts.yml", "Termux GitHub CLI hosts"),
            ("termux_home", ".config/gh/config.yml", "Termux GitHub CLI config"),
            ("debian_home", ".claude.json", "Claude CLI config"),
        ]

        # Add custom skills in .gemini/config/skills/
        gemini_skills_dir = self._resolve_source_path("debian_home", ".gemini/config/skills")
        if os.path.exists(gemini_skills_dir) and os.path.isdir(gemini_skills_dir):
            for root, _, files in os.walk(gemini_skills_dir):
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, self.env_info["debian_home"])
                    candidates.append(("debian_home", rel_p, f"Gemini skill file: {f}"))

        # Add MCP definitions in .gemini/antigravity-cli/mcp/
        mcp_dir = self._resolve_source_path("debian_home", ".gemini/antigravity-cli/mcp")
        if os.path.exists(mcp_dir) and os.path.isdir(mcp_dir):
            for root, _, files in os.walk(mcp_dir):
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, self.env_info["debian_home"])
                    candidates.append(("debian_home", rel_p, f"MCP tool schema: {f}"))

        # Add SSH keys if present
        ssh_dir = self._resolve_source_path("termux_home", ".ssh")
        if os.path.exists(ssh_dir) and os.path.isdir(ssh_dir):
            for f in os.listdir(ssh_dir):
                full_p = os.path.join(ssh_dir, f)
                if os.path.isfile(full_p):
                    candidates.append(("termux_home", f".ssh/{f}", f"SSH key: {f}"))

        return candidates

    def create_backup(self) -> Dict[str, Any]:
        """Execute backup process and produce tar.gz bundle."""
        self.log(f"Starting backup from source root: {self.source_root or '/'}")
        
        # 1. Validate agy binary
        if not self.agy_binary_path or not os.path.isfile(self.agy_binary_path):
            raise FileNotFoundError(f"Critical error: Antigravity (agy) binary not found at '{self.agy_binary_path}'")

        agy_sha256 = compute_file_sha256(self.agy_binary_path)
        agy_size = os.path.getsize(self.agy_binary_path)
        agy_version = self._get_agy_version(self.agy_binary_path)
        self.log(f"Detected Agy Binary: {self.agy_binary_path} (version {agy_version}, sha256={agy_sha256[:12]}...)")

        # 2. Get Git repo info
        repo_remote, repo_head, repo_branch = self._get_repo_git_info()
        self.log(f"Repo Info: remote={repo_remote or 'local'}, HEAD={repo_head[:8] if repo_head else 'none'}, branch={repo_branch or 'none'}")

        # 3. Discover files
        candidates = self._discover_candidate_files()
        staging_dir = tempfile.mkdtemp(prefix="agy_backup_stage_")
        files_stage_dir = os.path.join(staging_dir, "files")
        os.makedirs(files_stage_dir, exist_ok=True)

        file_entries: List[FileEntry] = []
        credentials_status: List[Dict[str, Any]] = []

        try:
            # Add agy binary to bundle if requested
            if self.include_binary:
                agy_arc_name = "bin/agy"
                dest_stage = os.path.join(files_stage_dir, agy_arc_name)
                os.makedirs(os.path.dirname(dest_stage), exist_ok=True)
                shutil.copy2(self.agy_binary_path, dest_stage)
                mode = stat.S_IMODE(os.stat(self.agy_binary_path).st_mode)
                file_entries.append(
                    FileEntry(
                        rel_path=agy_arc_name,
                        target_tag="debian_home",
                        sha256=agy_sha256,
                        size=agy_size,
                        mode=mode,
                    )
                )

            # Process candidates
            for target_tag, rel_path, desc in candidates:
                src_path = self._resolve_source_path(target_tag, rel_path)
                if not os.path.exists(src_path) or not os.path.isfile(src_path):
                    continue

                f_sha = compute_file_sha256(src_path)
                f_size = os.path.getsize(src_path)
                f_mode = stat.S_IMODE(os.stat(src_path).st_mode)
                
                # Compute safe archive path
                arc_rel_path = f"{target_tag}/{rel_path.lstrip('/')}"
                dest_stage = os.path.join(files_stage_dir, arc_rel_path)
                os.makedirs(os.path.dirname(dest_stage), exist_ok=True)
                shutil.copy2(src_path, dest_stage)

                file_entries.append(
                    FileEntry(
                        rel_path=arc_rel_path,
                        target_tag=target_tag,
                        sha256=f_sha,
                        size=f_size,
                        mode=f_mode,
                    )
                )

                # Classify credential if applicable
                classification = CredentialClassification.classify(os.path.basename(rel_path))
                if classification["type"] in ["oauth_token", "ssh_key", "hardware_bound", "cli_state"]:
                    credentials_status.append({
                        "name": classification["name"],
                        "file": rel_path,
                        "status": classification["status"],
                        "notes": classification["notes"],
                        "reauth_command": classification["reauth_command"],
                    })

            # Record dependencies metadata
            dependencies = {
                "python": sys.version.split()[0],
                "platform": sys.platform,
                "uv_tools": ["serena-agent", "specify-cli", "aider-chat"],
                "debian_packages": ["python3", "python3-pip", "git", "curl", "tar", "tmux", "rclone", "jq"],
                "termux_packages": ["proot-distro", "tmux", "termux-api", "git", "nodejs", "python", "rclone"],
            }

            # 4. Create MANIFEST
            manifest = MigrationManifest(
                source_arch=self.env_info["arch"],
                source_os=self.env_info["system"],
                agy_version=agy_version,
                agy_sha256=agy_sha256,
                agy_size=agy_size,
                repo_remote=repo_remote,
                repo_head=repo_head,
                repo_branch=repo_branch,
                dependencies=dependencies,
                credentials_status=credentials_status,
                files=file_entries,
            )

            manifest_path = os.path.join(staging_dir, "MANIFEST.json")
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest.to_dict(), mf, indent=2)

            # 5. Pack into tar.gz
            os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
            with tarfile.open(self.output_path, "w:gz") as tar:
                tar.add(manifest_path, arcname="MANIFEST.json")
                for root, _, files in os.walk(files_stage_dir):
                    for f in files:
                        full_f = os.path.join(root, f)
                        rel_in_stage = os.path.relpath(full_f, staging_dir)
                        tar.add(full_f, arcname=rel_in_stage)

            bundle_sha = compute_file_sha256(self.output_path)
            bundle_size = os.path.getsize(self.output_path)
            self.log(f"Migration bundle created successfully: {self.output_path}")
            self.log(f"Bundle size: {bundle_size} bytes | SHA-256: {bundle_sha}")
            self.log(f"Archived {len(file_entries)} files across Termux & Debian environments.")

            return {
                "success": True,
                "bundle_path": self.output_path,
                "bundle_sha256": bundle_sha,
                "bundle_size": bundle_size,
                "manifest": manifest.to_dict(),
            }

        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
