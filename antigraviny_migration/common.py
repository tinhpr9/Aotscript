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
import tempfile
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


# =====================================================================
# Antigraviny Core Lock & Materialization Contract
# =====================================================================

class CoreLockError(Exception):
    """Raised when ANTIGRAVINY_CORE.lock is missing or invalid."""
    pass


class CoreMaterializeError(Exception):
    """Raised when core fetching, verification, or materialization fails."""
    pass


def load_core_lock(repo_root: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate ANTIGRAVINY_CORE.lock from repository root."""
    if not repo_root:
        env = detect_environment()
        repo_root = env.get("repo_dir") or os.getcwd()

    lock_file = os.path.join(repo_root, "ANTIGRAVINY_CORE.lock")
    if not os.path.isfile(lock_file):
        raise CoreLockError(f"Missing ANTIGRAVINY_CORE.lock in {repo_root}")

    try:
        with open(lock_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise CoreLockError(f"Invalid JSON in {lock_file}: {e}")

    for req_field in ["core_repo", "core_sha", "core_version", "compatibility_schema"]:
        if req_field not in data or not data[req_field]:
            raise CoreLockError(f"ANTIGRAVINY_CORE.lock missing required field '{req_field}'")

    if data.get("compatibility_schema") != "antigraviny-core/v1":
        raise CoreLockError(
            f"Unsupported compatibility schema: {data.get('compatibility_schema')} (expected 'antigraviny-core/v1')"
        )

    return data


def check_gh_auth(repo_url: Optional[str] = None) -> bool:
    """Preflight check to verify GitHub CLI / git auth status for private repos."""
    try:
        res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except Exception:
        # Fallback to checking if git can access without prompting
        return False


def fetch_and_verify_core(
    core_repo_url: str,
    target_sha: str,
    dest_dir: Optional[str] = None,
    auth_check: bool = True,
    _simulate_unauthenticated: bool = False,
) -> str:
    """
    Fetch exact commit SHA from core repo and verify SHA immutability.
    Returns path to local verified core directory.
    """
    if auth_check and (_simulate_unauthenticated or not check_gh_auth(core_repo_url)):
        # Check if local directory exists already
        if not (dest_dir and os.path.exists(dest_dir)):
            raise CoreMaterializeError(
                "BLOCKED_AUTH: GitHub CLI / git authentication not available for private core repository"
            )

    target_dir = dest_dir or tempfile.mkdtemp(prefix="agy_core_fetch_")
    
    if not os.path.exists(os.path.join(target_dir, ".git")):
        os.makedirs(target_dir, exist_ok=True)
        try:
            subprocess.run(["git", "clone", core_repo_url, target_dir], check=True, capture_output=True, text=True, timeout=120)
        except Exception as e:
            raise CoreMaterializeError(f"Failed to clone core repository from {core_repo_url}: {e}")

    # Checkout exact SHA
    try:
        subprocess.run(["git", "-C", target_dir, "fetch", "origin"], capture_output=True, text=True, timeout=60)
        res = subprocess.run(["git", "-C", target_dir, "checkout", target_sha], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise CoreMaterializeError(f"Failed to checkout exact SHA {target_sha} in core repo: {res.stderr}")
    except Exception as e:
        if isinstance(e, CoreMaterializeError):
            raise
        raise CoreMaterializeError(f"Git checkout error for {target_sha}: {e}")

    # Verify verified SHA
    try:
        h = subprocess.run(["git", "-C", target_dir, "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        current_sha = h.stdout.strip()
        if current_sha.lower() != target_sha.lower():
            raise CoreMaterializeError(
                f"Core SHA mismatch: expected {target_sha}, got {current_sha}"
            )
    except Exception as e:
        if isinstance(e, CoreMaterializeError):
            raise
        raise CoreMaterializeError(f"Failed to verify HEAD SHA: {e}")

    return target_dir


def materialize_core_into_repo(
    repo_root: str,
    core_path_or_url: Optional[str] = None,
    lock_data: Optional[Dict[str, Any]] = None,
    overlay_dir: Optional[str] = None,
    dry_run: bool = False,
    _inject_failure: bool = False,
) -> Dict[str, Any]:
    """
    Materialize pinned Antigraviny core into repo_root/.agents/ with atomic rollback.
    """
    lock = lock_data or load_core_lock(repo_root)
    target_sha = lock["core_sha"]
    core_repo_url = core_path_or_url or lock["core_repo"]

    # 1. Resolve core directory
    temp_clone_dir = None
    if os.path.isdir(core_repo_url) and os.path.exists(os.path.join(core_repo_url, "capabilities.json")):
        core_dir = core_repo_url
    else:
        temp_clone_dir = tempfile.mkdtemp(prefix="agy_core_mat_")
        core_dir = fetch_and_verify_core(core_repo_url, target_sha, dest_dir=temp_clone_dir)

    try:
        # Check compatibility file in core
        compat_file = os.path.join(core_dir, "compatibility.json")
        if not os.path.isfile(compat_file):
            raise CoreMaterializeError(f"Core directory missing compatibility.json: {core_dir}")
        with open(compat_file, "r", encoding="utf-8") as f:
            compat_data = json.load(f)
        if compat_data.get("schema_version") != lock.get("compatibility_schema"):
            raise CoreMaterializeError(
                f"Compatibility mismatch: lock specifies '{lock.get('compatibility_schema')}', core provides '{compat_data.get('schema_version')}'"
            )

        # 2. Stage destination .agents directory
        target_agents_dir = os.path.join(repo_root, ".agents")
        stage_dir = tempfile.mkdtemp(prefix="agy_agents_stage_")
        backup_dir = tempfile.mkdtemp(prefix="agy_agents_backup_")
        target_existed = os.path.exists(target_agents_dir)

        # Track previous good sha
        prev_good_sha = None
        if target_existed:
            prev_state_file = os.path.join(target_agents_dir, "ecc-install-state.json")
            if os.path.isfile(prev_state_file):
                try:
                    with open(prev_state_file, "r", encoding="utf-8") as psf:
                        prev_state = json.load(psf)
                        prev_good_sha = prev_state.get("core_sha")
                except Exception:
                    pass

        try:
            if target_existed:
                shutil.rmtree(backup_dir, ignore_errors=True)
                shutil.copytree(target_agents_dir, backup_dir)

            # Copy generic capabilities from core
            for sub in ["agents", "workflows", "rules", "skills"]:
                src_sub = os.path.join(core_dir, sub)
                dst_sub = os.path.join(stage_dir, sub)
                if os.path.exists(src_sub):
                    shutil.copytree(src_sub, dst_sub)

            # Apply overlay if provided or exists in repo (.agents-overlay)
            active_overlay = overlay_dir or os.path.join(repo_root, ".agents-overlay")
            if os.path.exists(active_overlay) and os.path.isdir(active_overlay):
                for root, _, files in os.walk(active_overlay):
                    rel = os.path.relpath(root, active_overlay)
                    dst_folder = os.path.join(stage_dir, rel) if rel != "." else stage_dir
                    os.makedirs(dst_folder, exist_ok=True)
                    for f in files:
                        shutil.copy2(os.path.join(root, f), os.path.join(dst_folder, f))

            # Record install state with sha and previous good sha
            cap_file = os.path.join(core_dir, "capabilities.json")
            cap_count = 0
            if os.path.isfile(cap_file):
                try:
                    with open(cap_file, "r", encoding="utf-8") as cf:
                        cap_count = json.load(cf).get("count", 0)
                except Exception:
                    pass

            install_state = {
                "installed": True,
                "core_repo": lock["core_repo"],
                "core_version": lock.get("core_version", "1.0.0"),
                "core_sha": target_sha,
                "previous_good_core_sha": prev_good_sha or target_sha,
                "compatibility_schema": lock["compatibility_schema"],
                "materialized_at": datetime.now(timezone.utc).isoformat(),
                "capabilities_count": cap_count,
            }
            with open(os.path.join(stage_dir, "ecc-install-state.json"), "w", encoding="utf-8") as sf:
                json.dump(install_state, sf, indent=2)

            if _inject_failure:
                raise CoreMaterializeError("Injected failure for rollback testing")

            if not dry_run:
                if os.path.exists(target_agents_dir):
                    shutil.rmtree(target_agents_dir)
                os.makedirs(os.path.dirname(os.path.abspath(target_agents_dir)), exist_ok=True)
                shutil.copytree(stage_dir, target_agents_dir)

            return {
                "success": True,
                "core_sha": target_sha,
                "previous_good_core_sha": prev_good_sha,
                "capabilities_count": cap_count,
                "install_state": install_state,
            }

        except Exception as e:
            # Atomic rollback
            if target_existed and os.path.exists(backup_dir):
                shutil.rmtree(target_agents_dir, ignore_errors=True)
                shutil.copytree(backup_dir, target_agents_dir)
            elif not target_existed and os.path.exists(target_agents_dir):
                shutil.rmtree(target_agents_dir, ignore_errors=True)
            if isinstance(e, CoreMaterializeError):
                raise
            raise CoreMaterializeError(f"Materialization failed: {e}")

        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)
            shutil.rmtree(backup_dir, ignore_errors=True)

    finally:
        if temp_clone_dir:
            shutil.rmtree(temp_clone_dir, ignore_errors=True)
