"""
Common utilities, hashing, path discovery, secret masking, and data structures
for Antigraviny/Agy Migration System.
"""

import os
import sys
import hashlib
import json
import stat
import shutil
import platform
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional


def compute_file_sha256(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_bytes_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def mask_secret(value: str) -> str:
    """Mask sensitive secret values for logging or manifest generation."""
    if not value or not isinstance(value, str):
        return "***REDACTED***"
    if len(value) <= 8:
        return "***REDACTED***"
    return f"{value[:3]}***REDACTED***{value[-3:]}"


class CredentialClassification:
    """Classifies credentials into portability categories."""
    
    PORTABLE_BUNDLE = "PORTABLE_BUNDLE"
    DEVICE_BOUND_REAUTH_REQUIRED = "DEVICE_BOUND_REAUTH_REQUIRED"
    OPTIONAL_AUTH = "OPTIONAL_AUTH"

    REGISTRY = {
        "antigravity-oauth-token": {
            "name": "Antigravity OAuth Token",
            "type": "oauth_token",
            "status": "PORTABLE_BUNDLE",
            "notes": "Restored to ~/.gemini/antigravity-cli; subject to Google token validity / re-auth if expired",
            "reauth_command": "agy",
        },
        "jetski_state.pbtxt": {
            "name": "Antigravity CLI Session State",
            "type": "cli_state",
            "status": "PORTABLE_BUNDLE",
            "notes": "Restored to ~/.gemini/antigravity-cli",
            "reauth_command": "agy",
        },
        "installation_id": {
            "name": "Antigravity Installation ID",
            "type": "metadata",
            "status": "PORTABLE_BUNDLE",
            "notes": "Preserves installation identity",
            "reauth_command": None,
        },
        "gh_hosts": {
            "name": "GitHub CLI Auth (hosts.yml)",
            "type": "oauth_token",
            "status": "PORTABLE_BUNDLE",
            "notes": "Restored to ~/.config/gh/hosts.yml",
            "reauth_command": "gh auth login",
        },
        "rclone_conf": {
            "name": "Rclone Google Drive Config",
            "type": "oauth_token",
            "status": "PORTABLE_BUNDLE",
            "notes": "Restored to ~/.config/rclone/rclone.conf",
            "reauth_command": "rclone config reconnect antigraviny:",
        },
        "ssh_keys": {
            "name": "SSH Keys (~/.ssh)",
            "type": "ssh_key",
            "status": "PORTABLE_BUNDLE",
            "notes": "Restored with 0600 permissions",
            "reauth_command": None,
        },
        "android_keystore_key": {
            "name": "Android Hardware Keystore Credential",
            "type": "hardware_bound",
            "status": "DEVICE_BOUND_REAUTH_REQUIRED",
            "notes": "Cannot be exported or migrated across devices. Re-authentication required on target device.",
            "reauth_command": "Re-login on target device",
        },
    }

    @classmethod
    def classify(cls, item_key: str) -> Dict[str, Any]:
        """Classify a credential item by key or substring."""
        for k, v in cls.REGISTRY.items():
            if k in item_key:
                return v
        return {
            "name": item_key,
            "type": "unknown_credential",
            "status": cls.PORTABLE_BUNDLE,
            "notes": "Standard credential file",
            "reauth_command": None,
        }


def detect_environment(source_root: Optional[str] = None) -> Dict[str, Any]:
    """
    Dynamically discover runtime paths, OS environment, Termux prefixes,
    proot/Debian roots, and binary locations without hardcoding.
    """
    if source_root:
        if os.path.basename(source_root) == "root":
            debian_home = source_root
        else:
            debian_home = os.path.join(source_root, "root")
        termux_prefix = os.path.join(source_root, "data/data/com.termux/files/usr")
        termux_home = os.path.join(source_root, "data/data/com.termux/files/home")
    else:
        # Check if running in Termux
        termux_prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
        termux_home = os.environ.get("HOME", "/data/data/com.termux/files/home")
        # Inside proot Debian, HOME is typically /root
        debian_home = "/root" if os.path.exists("/root") else os.environ.get("HOME", "/root")

    # Discover agy binary
    agy_candidates = [
        os.path.join(debian_home, ".local/bin/agy"),
        os.path.join(debian_home, ".gemini/antigravity-cli/bin/agy"),
        os.path.join(termux_prefix, "bin/agy"),
        os.path.join(termux_home, ".local/bin/agy"),
    ]
    if not source_root:
        which_agy = shutil.which("agy")
        if which_agy:
            agy_candidates.insert(0, which_agy)

    agy_path = None
    for cand in agy_candidates:
        if os.path.exists(cand) and os.path.isfile(cand):
            agy_path = cand
            break

    # Discover Gemini config
    gemini_candidates = [
        os.path.join(debian_home, ".gemini"),
        os.path.join(termux_home, ".gemini"),
    ]
    gemini_dir = None
    for gc in gemini_candidates:
        if os.path.exists(gc):
            gemini_dir = gc
            break

    # Discover Repo root
    repo_candidates = [
        os.path.join(termux_home, "Aotscript-ecc-production"),
        os.path.join(termux_home, "Aotscript"),
        os.path.join(debian_home, "Aotscript-ecc-production"),
        os.path.join(debian_home, "Aotscript"),
    ]
    if not source_root:
        try:
            res = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                repo_candidates.insert(0, res.stdout.strip())
        except Exception:
            pass

    repo_dir = None
    for rc in repo_candidates:
        if os.path.exists(rc) and (os.path.exists(os.path.join(rc, ".git")) or os.path.exists(os.path.join(rc, ".agents"))):
            repo_dir = rc
            break

    return {
        "arch": platform.machine(),
        "system": platform.system(),
        "is_termux": os.path.exists(termux_prefix),
        "termux_prefix": termux_prefix,
        "termux_home": termux_home,
        "debian_home": debian_home,
        "agy_binary": agy_path,
        "gemini_dir": gemini_dir,
        "repo_dir": repo_dir,
    }


class FileEntry:
    """Represents a single file entry in the migration manifest."""
    def __init__(self, rel_path: str, target_tag: str, sha256: str, size: int, mode: int):
        self.rel_path = rel_path
        self.target_tag = target_tag  # e.g. 'debian_home', 'termux_home', 'termux_usr_bin', 'repo'
        self.sha256 = sha256
        self.size = size
        self.mode = mode

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "target_tag": self.target_tag,
            "sha256": self.sha256,
            "size": self.size,
            "mode": oct(self.mode),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileEntry":
        mode_val = data["mode"]
        if isinstance(mode_val, str):
            mode_int = int(mode_val, 8)
        else:
            mode_int = int(mode_val)
        return cls(
            rel_path=data["rel_path"],
            target_tag=data["target_tag"],
            sha256=data["sha256"],
            size=int(data["size"]),
            mode=mode_int,
        )


class MigrationManifest:
    """Complete manifest for a migration bundle."""
    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        timestamp: Optional[str] = None,
        source_arch: str = "aarch64",
        source_os: str = "Linux",
        agy_version: str = "unknown",
        agy_sha256: str = "",
        agy_size: int = 0,
        repo_remote: str = "",
        repo_head: str = "",
        repo_branch: str = "",
        dependencies: Optional[Dict[str, Any]] = None,
        credentials_status: Optional[List[Dict[str, Any]]] = None,
        files: Optional[List[FileEntry]] = None,
    ):
        self.schema_version = self.SCHEMA_VERSION
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.source_arch = source_arch
        self.source_os = source_os
        self.agy_version = agy_version
        self.agy_sha256 = agy_sha256
        self.agy_size = agy_size
        self.repo_remote = repo_remote
        self.repo_head = repo_head
        self.repo_branch = repo_branch
        self.dependencies = dependencies or {}
        self.credentials_status = credentials_status or []
        self.files = files or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "source_info": {
                "arch": self.source_arch,
                "os": self.source_os,
            },
            "agy": {
                "version": self.agy_version,
                "sha256": self.agy_sha256,
                "size": self.agy_size,
            },
            "repo": {
                "remote": self.repo_remote,
                "head": self.repo_head,
                "branch": self.repo_branch,
            },
            "dependencies": self.dependencies,
            "credentials_status": self.credentials_status,
            "files": [f.to_dict() for f in self.files],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MigrationManifest":
        if data.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError(f"Unsupported manifest schema version: {data.get('schema_version')}")
        
        source_info = data.get("source_info", {})
        agy_info = data.get("agy", {})
        repo_info = data.get("repo", {})
        
        file_entries = [FileEntry.from_dict(f) for f in data.get("files", [])]
        
        return cls(
            timestamp=data.get("timestamp"),
            source_arch=source_info.get("arch", "aarch64"),
            source_os=source_info.get("os", "Linux"),
            agy_version=agy_info.get("version", "unknown"),
            agy_sha256=agy_info.get("sha256", ""),
            agy_size=agy_info.get("size", 0),
            repo_remote=repo_info.get("remote", ""),
            repo_head=repo_info.get("head", ""),
            repo_branch=repo_info.get("branch", ""),
            dependencies=data.get("dependencies", {}),
            credentials_status=data.get("credentials_status", []),
            files=file_entries,
        )
