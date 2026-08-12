#!/usr/bin/env python3
"""Fail-closed validation for the immutable AOT Worker release workflow."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from typing import Any


class ReleaseVerificationError(RuntimeError):
    """GitHub release state does not match the immutable release contract."""


def require_absent_status(label: str, status: int) -> None:
    """Accept only an authoritative GitHub REST 404 for a missing resource."""
    if status == 404:
        return
    if 200 <= status < 300:
        raise ReleaseVerificationError(f"{label}_already_exists:http_{status}")
    raise ReleaseVerificationError(f"{label}_lookup_failed:http_{status}")


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"invalid_json:{path}") from exc
    if not isinstance(value, dict):
        raise ReleaseVerificationError(f"json_object_required:{path}")
    return value


def _local_assets(folder: pathlib.Path) -> dict[str, tuple[int, str]]:
    if not folder.is_dir():
        raise ReleaseVerificationError(f"asset_directory_missing:{folder}")
    assets: dict[str, tuple[int, str]] = {}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.is_symlink():
            raise ReleaseVerificationError(f"invalid_local_asset:{path.name}")
        assets[path.name] = (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    if not assets:
        raise ReleaseVerificationError("local_asset_set_empty")
    return assets


def _remote_assets(release: dict[str, Any]) -> dict[str, tuple[int, str]]:
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise ReleaseVerificationError("release_assets_not_list")
    assets: dict[str, tuple[int, str]] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            raise ReleaseVerificationError("release_asset_not_object")
        name = item.get("name")
        size = item.get("size")
        digest = item.get("digest")
        if not isinstance(name, str) or not name or name in assets:
            raise ReleaseVerificationError(f"invalid_or_duplicate_asset:{name!r}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ReleaseVerificationError(f"invalid_asset_size:{name}")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ReleaseVerificationError(f"invalid_asset_digest:{name}")
        sha256 = digest.removeprefix("sha256:").lower()
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ReleaseVerificationError(f"invalid_asset_digest:{name}")
        assets[name] = (size, sha256)
    return assets


def validate_draft(
    *, release: dict[str, Any], releases: list[Any], release_id: int, tag: str
) -> str:
    """Validate one resumable mutable draft and return its pinned target commit."""
    if release.get("id") != release_id:
        raise ReleaseVerificationError("release_identity_mismatch")
    if release.get("draft") is not True or release.get("immutable") is not False:
        raise ReleaseVerificationError("release_not_resumable_draft")
    if release.get("tag_name") != tag:
        raise ReleaseVerificationError("release_tag_mismatch")
    commit = release.get("target_commitish")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ReleaseVerificationError("release_target_commit_invalid")
    matching = [item for item in releases if isinstance(item, dict) and item.get("tag_name") == tag]
    if len(matching) != 1 or matching[0].get("id") != release_id:
        raise ReleaseVerificationError("release_tag_not_unique")
    return commit


def require_no_release_with_tag(releases: list[Any], tag: str) -> None:
    if any(isinstance(item, dict) and item.get("tag_name") == tag for item in releases):
        raise ReleaseVerificationError("release_with_tag_already_exists")


def _load_release_pages(path: pathlib.Path) -> list[Any]:
    try:
        pages = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"invalid_release_pages:{path}") from exc
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ReleaseVerificationError("release_pages_not_lists")
    return [item for page in pages for item in page]


def verify_release(
    *,
    commit: str,
    tag: str,
    asset_folder: pathlib.Path,
    release_path: pathlib.Path,
    ref_path: pathlib.Path | None,
    published: bool,
    draft_release_path: pathlib.Path | None = None,
) -> None:
    """Verify exact ref, release identity, publication state, and every asset."""
    release = _load_json(release_path)
    ref = _load_json(ref_path) if ref_path is not None else None
    expected_ref = f"refs/tags/{tag}"
    if release.get("tag_name") != tag or release.get("target_commitish") != commit:
        raise ReleaseVerificationError("release_tag_or_commit_mismatch")
    if published:
        if ref is None or ref.get("ref") != expected_ref:
            raise ReleaseVerificationError("tag_ref_name_mismatch")
        ref_object = ref.get("object")
        if not isinstance(ref_object, dict) or ref_object.get("type") != "commit" or ref_object.get("sha") != commit:
            raise ReleaseVerificationError("tag_ref_commit_mismatch")
    if release.get("draft") is not (not published):
        raise ReleaseVerificationError("release_draft_state_mismatch")
    if published:
        if release.get("immutable") is not True:
            raise ReleaseVerificationError("published_release_not_immutable")
        if draft_release_path is None:
            raise ReleaseVerificationError("draft_release_required_for_published_check")
        draft = _load_json(draft_release_path)
        published_id = release.get("id")
        draft_id = draft.get("id")
        if published_id is None or draft_id is None:
            raise ReleaseVerificationError("release_identity_missing")
        if published_id != draft_id:
            raise ReleaseVerificationError("release_identity_changed")
    local_assets = _local_assets(asset_folder)
    remote_assets = _remote_assets(release)
    if remote_assets != local_assets:
        raise ReleaseVerificationError(
            f"release_asset_set_mismatch:local={local_assets!r}:remote={remote_assets!r}"
        )


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "require-absent":
        require_absent_status(argv[2], int(argv[3]))
        return 0
    if len(argv) == 4 and argv[1] == "require-no-tagged-release":
        releases = _load_release_pages(pathlib.Path(argv[2]))
        require_no_release_with_tag(releases, argv[3])
        return 0
    if len(argv) == 6 and argv[1] == "validate-draft":
        release = _load_json(pathlib.Path(argv[2]))
        releases = _load_release_pages(pathlib.Path(argv[3]))
        print(validate_draft(release=release, releases=releases, release_id=int(argv[4]), tag=argv[5]))
        return 0
    if len(argv) not in {8, 9} or argv[1] != "verify":
        raise SystemExit(
            "usage: verify_worker_release.py require-absent LABEL STATUS | "
            "verify COMMIT TAG ASSET_DIR RELEASE_JSON REF_JSON|- draft|published [DRAFT_JSON]"
        )
    state = argv[7]
    if state not in {"draft", "published"}:
        raise SystemExit("release state must be draft or published")
    verify_release(
        commit=argv[2],
        tag=argv[3],
        asset_folder=pathlib.Path(argv[4]),
        release_path=pathlib.Path(argv[5]),
        ref_path=None if argv[6] == "-" else pathlib.Path(argv[6]),
        published=state == "published",
        draft_release_path=pathlib.Path(argv[8]) if len(argv) == 9 else None,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except ReleaseVerificationError as exc:
        print(f"RELEASE_VERIFICATION_FAILED:{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
