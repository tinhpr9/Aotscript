"""
Command Line Interface (CLI) for Antigraviny/Agy Migration System.
Provides 4 main entrypoints:
  - agy-backup
  - agy-bootstrap
  - agy-restore
  - agy-verify
"""

import sys
import os
import argparse
import json

from antigraviny_migration.backup import AgyBackupEngine
from antigraviny_migration.bootstrap import AgyBootstrapEngine
from antigraviny_migration.restore import AgyRestoreEngine, RestoreError
from antigraviny_migration.verify import AgyVerifyEngine


def _build_backup_parser(parser: argparse.ArgumentParser):
    parser.add_argument("-o", "--output", help="Output path for bundle archive (.tar.gz)")
    parser.add_argument("--source-root", help="Source filesystem root override (for testing)")
    parser.add_argument("--agy-bin", help="Explicit path to agy binary")
    parser.add_argument("--repo-path", help="Explicit path to Aotscript repository")
    parser.add_argument("--no-binary", action="store_true", help="Exclude large binary from bundle")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logs")


def _build_bootstrap_parser(parser: argparse.ArgumentParser):
    parser.add_argument("--target-root", help="Target filesystem root override (for testing)")
    parser.add_argument("--repo-url", help="Aotscript Git repository URL")
    parser.add_argument("--branch", help="Git branch to clone")
    parser.add_argument("--skip-pkg-install", action="store_true", help="Skip host package installation")
    parser.add_argument("--dry-run", action="store_true", help="Simulate bootstrap steps without changing system")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logs")


def _build_restore_parser(parser: argparse.ArgumentParser):
    parser.add_argument("bundle", help="Path to migration bundle archive (.tar.gz)")
    parser.add_argument("--target-root", help="Target filesystem root override (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Verify bundle without writing to disk")
    parser.add_argument("-f", "--force", action="store_true", help="Force overwrite without prompt")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logs")


def _build_verify_parser(parser: argparse.ArgumentParser):
    parser.add_argument("--bundle", help="Path to migration bundle archive to compare against")
    parser.add_argument("--manifest", help="Path to standalone MANIFEST.json")
    parser.add_argument("--target-root", help="Target filesystem root override (for testing)")
    parser.add_argument("--repo-path", help="Explicit path to repository")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logs")


def run_backup(args):
    engine = AgyBackupEngine(
        source_root=getattr(args, "source_root", None),
        agy_binary_path=getattr(args, "agy_bin", None),
        repo_path=getattr(args, "repo_path", None),
        output_path=getattr(args, "output", None),
        include_binary=not getattr(args, "no_binary", False),
        quiet=getattr(args, "quiet", False),
    )
    res = engine.create_backup()
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
    else:
        print("\n" + "="*50)
        print("  ANTIGRAVINY MIGRATION BACKUP COMPLETED")
        print("="*50)
        print(f"Bundle: {res['bundle_path']}")
        print(f"SHA-256: {res['bundle_sha256']}")
        print(f"Size: {res['bundle_size']} bytes")
        print(f"Files: {len(res['manifest']['files'])}")
        print("="*50)
    return 0


def run_bootstrap(args):
    engine = AgyBootstrapEngine(
        target_root=getattr(args, "target_root", None),
        repo_url=getattr(args, "repo_url", None),
        branch=getattr(args, "branch", None),
        skip_pkg_install=getattr(args, "skip_pkg_install", False),
        dry_run=getattr(args, "dry_run", False),
        quiet=getattr(args, "quiet", False),
    )
    res = engine.bootstrap()
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
    else:
        print("\n" + "="*50)
        print("  ANTIGRAVINY BOOTSTRAP COMPLETED")
        print("="*50)
        print(f"System: {res['system']} ({res['architecture']})")
        print(f"Repo Dir: {res['repo_dir']}")
        print(f"Steps: {len(res['steps'])}")
        print("="*50)
    return 0


def run_restore(args):
    engine = AgyRestoreEngine(
        bundle_path=args.bundle,
        target_root=getattr(args, "target_root", None),
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
        quiet=getattr(args, "quiet", False),
    )
    try:
        res = engine.restore()
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "="*50)
            print("  ANTIGRAVINY RESTORE COMPLETED")
            print("="*50)
            print(f"Restored Files: {res['restored_count']}")
            print(f"Agy Version: {res['manifest']['agy']['version']}")
            if res.get("reauth_required"):
                print("\n[!] REAUTH REQUIRED FOR CREDENTIALS:")
                for r in res["reauth_required"]:
                    print(f"  - {r['name']}: {r.get('notes', '')}")
            print("="*50)
        return 0
    except RestoreError as e:
        print(f"\n[ERROR] Restore failed: {e}", file=sys.stderr)
        return 1


def run_verify(args):
    engine = AgyVerifyEngine(
        target_root=getattr(args, "target_root", None),
        bundle_path=getattr(args, "bundle", None),
        manifest_path=getattr(args, "manifest", None),
        repo_path=getattr(args, "repo_path", None),
        quiet=getattr(args, "quiet", False),
    )
    res = engine.verify()
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print("\n" + "="*50)
        print(f"  ANTIGRAVINY VERIFICATION AUDIT: {res.overall_status}")
        print("="*50)
        for check_name, info in res.checks.items():
            status_icon = "PASS" if info["pass"] else ("REAUTH" if info.get("is_reauth") else "FAIL")
            print(f"[{status_icon:6s}] {check_name:30s} : {info['detail']}")
        print("="*50)
        if res.failures:
            print("\nFAILURES:")
            for f in res.failures:
                print(f"  - {f}")
        if res.reauth_items:
            print("\nREAUTH ITEMS:")
            for r in res.reauth_items:
                print(f"  - {r}")
        print(f"\nFINAL VERIFICATION RESULT: {res.overall_status}\n")

    if res.overall_status == "PASS":
        return 0
    elif res.overall_status == "REAUTH_REQUIRED":
        return 2
    else:
        return 1


def main(default_command=None):
    if default_command == "backup":
        parser = argparse.ArgumentParser(prog="agy-backup", description="Create migration bundle from source machine")
        _build_backup_parser(parser)
        args = parser.parse_args()
        sys.exit(run_backup(args))

    elif default_command == "bootstrap":
        parser = argparse.ArgumentParser(prog="agy-bootstrap", description="Prepare target machine environment and dependencies")
        _build_bootstrap_parser(parser)
        args = parser.parse_args()
        sys.exit(run_bootstrap(args))

    elif default_command == "restore":
        parser = argparse.ArgumentParser(prog="agy-restore", description="Restore Antigravity system from migration bundle")
        _build_restore_parser(parser)
        args = parser.parse_args()
        sys.exit(run_restore(args))

    elif default_command == "verify":
        parser = argparse.ArgumentParser(prog="agy-verify", description="Verify target machine environment against snapshot")
        _build_verify_parser(parser)
        args = parser.parse_args()
        sys.exit(run_verify(args))

    else:
        parser = argparse.ArgumentParser(description="Antigraviny Migration & Environment Management CLI")
        subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

        p_backup = subparsers.add_parser("backup", help="Create migration bundle from source machine")
        _build_backup_parser(p_backup)
        p_backup.set_defaults(func=run_backup)

        p_boot = subparsers.add_parser("bootstrap", help="Prepare target machine environment and dependencies")
        _build_bootstrap_parser(p_boot)
        p_boot.set_defaults(func=run_bootstrap)

        p_rest = subparsers.add_parser("restore", help="Restore Antigravity system from migration bundle")
        _build_restore_parser(p_rest)
        p_rest.set_defaults(func=run_restore)

        p_ver = subparsers.add_parser("verify", help="Verify target machine environment against snapshot")
        _build_verify_parser(p_ver)
        p_ver.set_defaults(func=run_verify)

        args = parser.parse_args()
        if not args.command:
            parser.print_help()
            sys.exit(1)

        sys.exit(args.func(args))


if __name__ == "__main__":
    main()
