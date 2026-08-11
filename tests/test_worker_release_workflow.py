from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify_worker_release.py"
WORKFLOW = ROOT / ".github/workflows/release-worker.yml"
SPEC = importlib.util.spec_from_file_location("verify_worker_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WorkerReleaseWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="worker-release-guard-")
        self.root = pathlib.Path(self.temporary.name)
        self.assets = self.root / "dist"
        self.assets.mkdir()
        (self.assets / "worker-bundle.zip").write_bytes(b"bundle")
        (self.assets / "worker-manifest.json").write_bytes(b"manifest")
        self.commit = "a" * 40
        self.tag = "worker-v2026.08.11.8"
        self.release = {
            "id": 123,
            "tag_name": self.tag,
            "target_commitish": self.commit,
            "draft": True,
            "immutable": False,
            "assets": [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in sorted(self.assets.iterdir())
            ],
        }
        self.ref = {
            "ref": f"refs/tags/{self.tag}",
            "object": {"type": "commit", "sha": self.commit},
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, name: str, value: object) -> pathlib.Path:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def verify(self, release: dict, ref: dict | None = None, *, published: bool = False) -> None:
        release_path = self.write_json("release.json", release)
        ref_path = self.write_json("ref.json", self.ref if ref is None else ref)
        draft_path = self.write_json("draft.json", self.release) if published else None
        MODULE.verify_release(
            commit=self.commit,
            tag=self.tag,
            asset_folder=self.assets,
            release_path=release_path,
            ref_path=ref_path,
            published=published,
            draft_release_path=draft_path,
        )

    def test_absence_check_accepts_only_404(self) -> None:
        MODULE.require_absent_status("tag", 404)
        for status in (200, 201, 204, 401, 403, 429, 500):
            with self.subTest(status=status), self.assertRaises(MODULE.ReleaseVerificationError):
                MODULE.require_absent_status("tag", status)

    def test_exact_release_verification_rejects_every_mismatch(self) -> None:
        self.verify(self.release)
        published = copy.deepcopy(self.release)
        published.update({"draft": False, "immutable": True})
        self.verify(published, published=True)
        mutations = []
        wrong_commit = copy.deepcopy(self.release)
        wrong_commit["target_commitish"] = "b" * 40
        mutations.append((wrong_commit, self.ref, False))
        wrong_tag = copy.deepcopy(self.release)
        wrong_tag["tag_name"] = "worker-vwrong"
        mutations.append((wrong_tag, self.ref, False))
        wrong_ref = copy.deepcopy(self.ref)
        wrong_ref["object"]["sha"] = "b" * 40
        mutations.append((self.release, wrong_ref, False))
        for field, value in (("name", "wrong-name"), ("size", 999), ("digest", "sha256:" + "0" * 64)):
            changed = copy.deepcopy(self.release)
            changed["assets"][0][field] = value
            mutations.append((changed, self.ref, False))
        missing = copy.deepcopy(self.release)
        missing["assets"].pop()
        mutations.append((missing, self.ref, False))
        extra = copy.deepcopy(self.release)
        extra["assets"].append({"name": "extra", "size": 0, "digest": "sha256:" + "0" * 64})
        mutations.append((extra, self.ref, False))
        duplicate = copy.deepcopy(self.release)
        duplicate["assets"].append(copy.deepcopy(duplicate["assets"][0]))
        mutations.append((duplicate, self.ref, False))
        mutable = copy.deepcopy(published)
        mutable["immutable"] = False
        mutations.append((mutable, self.ref, True))
        for release, ref, is_published in mutations:
            with self.subTest(release=release, ref=ref, published=is_published), self.assertRaises(
                MODULE.ReleaseVerificationError
            ):
                self.verify(release, ref, published=is_published)

    def test_workflow_is_fail_closed_and_never_uses_admin_endpoint(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("/immutable-releases", text)
        self.assertNotIn("len(data[\"assets\"])", text)
        self.assertNotIn("gh release delete", text)
        self.assertNotIn("git push --delete", text)
        self.assertIn("require-absent", text)
        self.assertGreaterEqual(text.count("scripts/verify_worker_release.py verify"), 2)
        self.assertLess(text.index("gh release create"), text.index("gh release upload"))
        self.assertLess(text.index("gh release upload"), text.index("--draft=false"))


if __name__ == "__main__":
    unittest.main()
