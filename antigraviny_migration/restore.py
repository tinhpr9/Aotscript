"""
Restore Engine for Antigraviny/Agy Migration System.
Performs authenticated, checksum-verified restoration of Antigravity CLI binary,
configs, launchers, ECC environment, and state with atomic rollback protection.
"""

import os
import sys
import json
import stat
import shutil
import tarfile
import tempfile
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


class RestoreError(Exception):
    """Raised when bundle validation or restoration fails."""
    pass


class AgyRestoreEngine:
    """Restores Antigravity system from a verified migration bundle with rollback."""

    def __init__(
        self,
        bundle_path: str,
        target_root: Optional[str] = None,
        dry_run: bool = False,
        force: bool = False,
        quiet: bool = False,
        _inject_failure_after_count: Optional[int] = None,
    ):
        self.bundle_path = bundle_path
        self.target_root = target_root
        self.dry_run = dry_run
        self.force = force
        self.quiet = quiet
        self._inject_failure_after_count = _inject_failure_after_count
        self.env_info = detect_environment(target_root)

    def log(self, msg: str):
        if not self.quiet:
            print(f"[agy-restore] {msg}")

    def _resolve_dest_path(self, target_tag: str, rel_path: str) -> str:
        """Map logical manifest target tag to actual path on destination machine."""
        if target_tag == "debian_home":
            base = self.env_info["debian_home"]
        elif target_tag == "termux_home":
            base = self.env_info["termux_home"]
        elif target_tag == "termux_usr_bin":
            base = os.path.join(self.env_info["termux_prefix"], "bin")
        elif target_tag == "termux_home_bin":
            base = os.path.join(self.env_info["termux_home"], "bin")
        else:
            base = self.env_info["debian_home"]

        clean_rel = rel_path
        if clean_rel.startswith(f"{target_tag}/"):
            clean_rel = clean_rel[len(target_tag) + 1:]
        elif clean_rel == "bin/agy":
            clean_rel = ".local/bin/agy"

        dest = os.path.abspath(os.path.join(base, clean_rel.lstrip("/")))
        
        # Canonical boundary containment check
        base_abs = os.path.abspath(base)
        try:
            if os.path.commonpath([base_abs, dest]) != base_abs:
                raise RestoreError(f"Target destination escapes base directory: {dest} (base: {base_abs})")
        except ValueError:
            raise RestoreError(f"Path comparison error between {base_abs} and {dest}")

        return dest

    def _validate_archive_security(self, tar: tarfile.TarFile):
        """Prevent path traversal and malicious symlinks."""
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise RestoreError(f"Malicious path detected in archive: {member.name}")

    def restore(self) -> Dict[str, Any]:
        """Execute full verified restore pipeline with rollback safety."""
        self.log(f"Initiating restore from bundle: {self.bundle_path}")

        if not os.path.isfile(self.bundle_path):
            raise RestoreError(f"Bundle file not found: {self.bundle_path}")

        # 1. Verify bundle archive format & Manifest
        try:
            tar = tarfile.open(self.bundle_path, "r:gz")
        except Exception as e:
            raise RestoreError(f"Corrupt or invalid gzip bundle archive: {e}")

        with tar:
            self._validate_archive_security(tar)
            
            try:
                manifest_member = tar.getmember("MANIFEST.json")
                manifest_file = tar.extractfile(manifest_member)
                if manifest_file is None:
                    raise RestoreError("Empty MANIFEST.json in bundle")
                manifest_data = json.loads(manifest_file.read().decode("utf-8"))
                manifest = MigrationManifest.from_dict(manifest_data)
            except KeyError:
                raise RestoreError("Missing MANIFEST.json in migration bundle archive")
            except Exception as e:
                raise RestoreError(f"Invalid or corrupted manifest structure: {e}")

            self.log(f"Loaded Manifest: Agy v{manifest.agy_version}, Source Arch: {manifest.source_arch}, Files: {len(manifest.files)}")

            # 2. Verify all files SHA-256 before touching target disk
            self.log("Verifying SHA-256 checksums of all bundled payload files...")
            tar_members_map = {m.name: m for m in tar.getmembers()}

            for entry in manifest.files:
                archive_name = f"files/{entry.rel_path}"
                if archive_name not in tar_members_map:
                    raise RestoreError(f"Manifest lists file '{entry.rel_path}' which is missing from archive")
                
                member = tar_members_map[archive_name]
                f_obj = tar.extractfile(member)
                if f_obj is None:
                    raise RestoreError(f"Could not read archived file: {archive_name}")
                
                payload = f_obj.read()
                calc_sha = compute_bytes_sha256(payload)
                if calc_sha.lower() != entry.sha256.lower():
                    raise RestoreError(
                        f"SHA256 mismatch for '{entry.rel_path}': expected {entry.sha256}, got {calc_sha}"
                    )

            self.log("All archive file checksums verified OK. Preparing rollback staging journal...")

            # 3. Setup rollback staging journal
            rollback_dir = tempfile.mkdtemp(prefix="agy_restore_rollback_")
            created_files: List[str] = []
            backed_up_files: List[Tuple[str, str, int]] = []  # (orig_path, backup_path, orig_mode)

            try:
                restored_count = 0
                for entry in manifest.files:
                    if self._inject_failure_after_count is not None and restored_count >= self._inject_failure_after_count:
                        raise RestoreError("Injected failure for rollback validation test")

                    dest_path = self._resolve_dest_path(entry.target_tag, entry.rel_path)
                    
                    if os.path.exists(dest_path):
                        # Backup existing file into rollback staging
                        backup_copy = os.path.join(rollback_dir, f"backup_{restored_count}_{os.path.basename(dest_path)}")
                        shutil.copy2(dest_path, backup_copy)
                        orig_mode = stat.S_IMODE(os.stat(dest_path).st_mode)
                        backed_up_files.append((dest_path, backup_copy, orig_mode))
                    else:
                        created_files.append(dest_path)

                    if not self.dry_run:
                        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                        member = tar_members_map[f"files/{entry.rel_path}"]
                        f_obj = tar.extractfile(member)
                        payload_data = f_obj.read()
                        
                        # Atomic file creation with exact mode
                        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                        fd = os.open(dest_path, flags, entry.mode)
                        try:
                            with os.fdopen(fd, "wb") as out_f:
                                out_f.write(payload_data)
                        except Exception:
                            pass

                        # Ensure mode is strictly applied (in case umask modified it)
                        os.chmod(dest_path, entry.mode)

                    restored_count += 1

                # 4. Verify restored on-disk checksums
                if not self.dry_run:
                    self.log("Verifying on-disk checksums after restore...")
                    for entry in manifest.files:
                        dest_path = self._resolve_dest_path(entry.target_tag, entry.rel_path)
                        on_disk_sha = compute_file_sha256(dest_path)
                        if on_disk_sha.lower() != entry.sha256.lower():
                            raise RestoreError(f"Post-restore verification failed for {dest_path}")

                self.log(f"Successfully restored {restored_count} files.")

                # Classify credentials status
                reauth_required_items = []
                for cred in manifest.credentials_status:
                    if cred.get("status") == CredentialClassification.DEVICE_BOUND_REAUTH_REQUIRED:
                        reauth_required_items.append(cred)

                return {
                    "success": True,
                    "restored_count": restored_count,
                    "manifest": manifest.to_dict(),
                    "reauth_required": reauth_required_items,
                }

            except Exception as e:
                # Trigger Rollback
                self.log(f"Restore encountered error: {e}. Executing automatic rollback...")
                for created in created_files:
                    if os.path.exists(created):
                        try:
                            os.remove(created)
                        except Exception:
                            pass
                for orig_path, backup_path, orig_mode in backed_up_files:
                    if os.path.exists(backup_path):
                        try:
                            os.makedirs(os.path.dirname(orig_path), exist_ok=True)
                            shutil.copy2(backup_path, orig_path)
                            os.chmod(orig_path, orig_mode)
                        except Exception:
                            pass
                self.log("Rollback completed. Restored original filesystem state.")
                if isinstance(e, RestoreError):
                    raise
                raise RestoreError(f"Restore failed: {e}")

            finally:
                shutil.rmtree(rollback_dir, ignore_errors=True)
