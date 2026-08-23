from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
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
        self.tag = "worker-v2026.08.23.03"
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

    def verify(
        self,
        release: dict,
        ref: dict | None = None,
        *,
        published: bool = False,
        draft_release: dict | None = None,
    ) -> None:
        release_path = self.write_json("release.json", release)
        ref_path = self.write_json("ref.json", self.ref if ref is None else ref) if published else None
        draft_path = (
            self.write_json("draft.json", self.release if draft_release is None else draft_release)
            if published
            else None
        )
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
        missing_published_id = copy.deepcopy(published)
        missing_published_id.pop("id")
        mutations.append((missing_published_id, self.ref, True))
        for release, ref, is_published in mutations:
            with self.subTest(release=release, ref=ref, published=is_published), self.assertRaises(
                MODULE.ReleaseVerificationError
            ):
                self.verify(release, ref, published=is_published)
        missing_draft_id = copy.deepcopy(self.release)
        missing_draft_id.pop("id")
        with self.assertRaisesRegex(MODULE.ReleaseVerificationError, "release_identity_missing"):
            self.verify(published, published=True, draft_release=missing_draft_id)

    def test_draft_resume_requires_exact_identity_tag_state_and_unique_target(self) -> None:
        releases = [copy.deepcopy(self.release)]
        self.assertEqual(
            MODULE.validate_draft(
                release=self.release, releases=releases, release_id=123, tag=self.tag
            ),
            self.commit,
        )
        mutations = []
        wrong_id = copy.deepcopy(self.release)
        wrong_id["id"] = 124
        mutations.append((wrong_id, releases, 123, self.tag))
        missing_id = copy.deepcopy(self.release)
        missing_id.pop("id")
        mutations.append((missing_id, releases, 123, self.tag))
        published = copy.deepcopy(self.release)
        published["draft"] = False
        mutations.append((published, releases, 123, self.tag))
        immutable = copy.deepcopy(self.release)
        immutable["immutable"] = True
        mutations.append((immutable, releases, 123, self.tag))
        wrong_target = copy.deepcopy(self.release)
        wrong_target["target_commitish"] = "main"
        mutations.append((wrong_target, [wrong_target], 123, self.tag))
        duplicate = [copy.deepcopy(self.release), copy.deepcopy(self.release)]
        duplicate[1]["id"] = 124
        mutations.append((self.release, duplicate, 123, self.tag))
        for release, listing, release_id, tag in mutations:
            with self.subTest(release=release, listing=listing), self.assertRaises(
                MODULE.ReleaseVerificationError
            ):
                MODULE.validate_draft(
                    release=release, releases=listing, release_id=release_id, tag=tag
                )

    def test_draft_asset_verification_does_not_require_git_ref(self) -> None:
        release_path = self.write_json("draft-no-ref.json", self.release)
        MODULE.verify_release(
            commit=self.commit,
            tag=self.tag,
            asset_folder=self.assets,
            release_path=release_path,
            ref_path=None,
            published=False,
        )

    def test_published_release_requires_exact_ref_identity_and_immutability(self) -> None:
        published = copy.deepcopy(self.release)
        published.update({"draft": False, "immutable": True})
        wrong_ref = copy.deepcopy(self.ref)
        wrong_ref["object"]["sha"] = "b" * 40
        with self.assertRaisesRegex(MODULE.ReleaseVerificationError, "tag_ref_commit_mismatch"):
            self.verify(published, wrong_ref, published=True)
        wrong_id = copy.deepcopy(published)
        wrong_id["id"] = 999
        with self.assertRaisesRegex(MODULE.ReleaseVerificationError, "release_identity_changed"):
            self.verify(wrong_id, published=True)

    def test_create_mode_rejects_any_existing_release_with_tag(self) -> None:
        MODULE.require_no_release_with_tag([], self.tag)
        with self.assertRaisesRegex(MODULE.ReleaseVerificationError, "already_exists"):
            MODULE.require_no_release_with_tag([self.release], self.tag)

    def test_resume_rebuild_reproduces_all_release_assets_from_source(self) -> None:
        first = self.root / "first-build"
        rebuilt = self.root / "rebuilt"
        command = [
            sys.executable,
            str(ROOT / "scripts/build-worker-release.py"),
            "--version",
            "2026.08.23.03",
            "--commit",
            self.commit,
            "--source-root",
            str(ROOT),
        ]
        subprocess.run(command + ["--output", str(first)], check=True, capture_output=True)
        subprocess.run(
            command
            + [
                "--reproduce-metadata-from",
                str(first),
                "--output",
                str(rebuilt),
            ],
            check=True,
            capture_output=True,
        )
        first_assets = {
            path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in first.iterdir()
        }
        rebuilt_assets = {
            path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in rebuilt.iterdir()
        }
        self.assertEqual(14, len(first_assets))
        self.assertEqual(first_assets, rebuilt_assets)

    def test_workflow_is_fail_closed_and_never_uses_admin_endpoint(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('test "$RELEASE_VERSION" = "$CANONICAL_VERSION"', text)
        self.assertIn("prepare_worker_release.py --check", text)
        self.assertIn("aot-group-control/worker_smoke_test.py", text)
        self.assertIn("https://uploads.github.com/repos/${GITHUB_REPOSITORY}", text)
        self.assertNotIn("/immutable-releases", text)
        self.assertNotIn("len(data[\"assets\"])", text)
        self.assertNotIn("gh release delete", text)
        self.assertNotIn("DELETE", text)
        self.assertNotIn("git push --delete", text)
        self.assertNotIn("--clobber", text)
        self.assertIn("require-absent", text)
        self.assertIn("resume_release_id", text)
        self.assertIn('releases/${RELEASE_ID}', text)
        self.assertNotIn('releases/tags/${TAG} > draft', text)
        self.assertNotIn('git/ref/tags/${TAG} > release-ref', text)
        self.assertGreaterEqual(text.count("scripts/verify_worker_release.py verify"), 3)
        self.assertLess(text.index('upload_asset "$asset"'), text.index("-F draft=false"))

    def test_resume_mode_only_uploads_an_empty_draft(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        create_block = text[text.index('if [[ "$RELEASE_MODE" == create ]]'):]
        resume_start = create_block.index('          else\n            RELEASE_ID="$RESUME_RELEASE_ID"')
        resume_end = create_block.index('          fi\n          gh api', resume_start)
        resume_branch = create_block[resume_start:resume_end]
        self.assertIn('[[ "$asset_count" == 0 ]]', resume_branch)
        self.assertIn('upload_asset "$asset"', resume_branch)


class CalVerEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import release_version
        self.engine = release_version

    def test_valid_calver_parsing(self) -> None:
        cv = self.engine.parse_calver("2026.08.23.03")
        self.assertEqual(2026, cv.year)
        self.assertEqual(8, cv.month)
        self.assertEqual(23, cv.day)
        self.assertEqual(3, cv.seq)
        self.assertEqual("2026.08.23.03", cv.serialize())

        cv2027 = self.engine.parse_calver("2027.01.01.01")
        self.assertEqual(2027, cv2027.year)
        self.assertEqual("2027.01.01.01", cv2027.serialize())

    def test_invalid_calver_rejections(self) -> None:
        invalid_versions = [
            "2026.8.23.03",       # non-zero-padded month
            "2026.08.3.03",        # non-zero-padded day
            "2026.08.23.3",        # non-zero-padded seq
            "2026.13.01.01",       # month 13
            "2026.02.29.01",       # non-leap year Feb 29
            "2026.04.31.01",       # April 31
            "2026.08.23.00",       # sequence 0
            "2026.08.23.100",      # sequence 100
            "v2026.08.23.01",      # prefix
            "2026.08.23",          # missing seq
        ]
        for v in invalid_versions:
            with self.subTest(version=v), self.assertRaises(ValueError):
                self.engine.parse_calver(v)

    def test_same_day_increment(self) -> None:
        import datetime
        cur_date = datetime.date(2026, 8, 23)
        next_ver = self.engine.next_calver("2026.08.23.03", target_date=cur_date)
        self.assertEqual("2026.08.23.04", next_ver.serialize())

    def test_new_day_reset(self) -> None:
        import datetime
        next_day = datetime.date(2026, 8, 24)
        next_ver = self.engine.next_calver("2026.08.23.03", target_date=next_day)
        self.assertEqual("2026.08.24.01", next_ver.serialize())

    def test_year_rollover(self) -> None:
        import datetime
        next_year = datetime.date(2027, 1, 1)
        next_ver = self.engine.next_calver("2026.12.31.05", target_date=next_year)
        self.assertEqual("2027.01.01.01", next_ver.serialize())


class DeterministicReleaseBuilderTests(unittest.TestCase):
    def test_clean_builds_are_byte_for_byte_identical(self) -> None:
        import datetime
        with tempfile.TemporaryDirectory(prefix="det-run1-") as d1, tempfile.TemporaryDirectory(prefix="det-run2-") as d2:
            out1 = pathlib.Path(d1) / "release"
            out2 = pathlib.Path(d2) / "release"
            commit = "c" * 40
            ver = "2026.08.23.03"
            cmd = [
                sys.executable,
                str(ROOT / "scripts/build-worker-release.py"),
                "--version", ver,
                "--commit", commit,
                "--source-root", str(ROOT),
            ]
            subprocess.run(cmd + ["--output", str(out1)], check=True, capture_output=True)
            subprocess.run(cmd + ["--output", str(out2)], check=True, capture_output=True)

            files1 = sorted(f.name for f in out1.iterdir())
            files2 = sorted(f.name for f in out2.iterdir())
            self.assertEqual(files1, files2)
            self.assertEqual(14, len(files1))

            for name in files1:
                h1 = hashlib.sha256((out1 / name).read_bytes()).hexdigest()
                h2 = hashlib.sha256((out2 / name).read_bytes()).hexdigest()
                self.assertEqual(h1, h2, f"Artifact mismatch for {name}")


class ReleasePreparationAndCoherenceTests(unittest.TestCase):
    def test_prepare_worker_release_check_passes_on_clean_repo(self) -> None:
        res = subprocess.run(
            [sys.executable, str(ROOT / "scripts/prepare_worker_release.py"), "--check", "--repo-root", str(ROOT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, res.returncode, f"Check failed: {res.stdout}\n{res.stderr}")
        self.assertIn("RELEASE_COHERENCE_CHECK=PASS", res.stdout)

    def test_prepare_worker_release_fails_on_stale_manifest_sha(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coherence-test-") as temp_dir:
            temp_root = pathlib.Path(temp_dir)
            shutil.copytree(ROOT / "aot-group-control", temp_root / "aot-group-control")
            shutil.copytree(ROOT / "cloudflare-worker", temp_root / "cloudflare-worker")
            shutil.copytree(ROOT / "scripts", temp_root / "scripts")
            
            # Mutate one file SHA in manifest
            stable_manifest_path = temp_root / "aot-group-control" / "worker-manifest-stable.json"
            data = json.loads(stable_manifest_path.read_text(encoding="utf-8"))
            data["files"][0]["sha256"] = "0" * 64
            stable_manifest_path.write_text(json.dumps(data), encoding="utf-8")

            res = subprocess.run(
                [sys.executable, str(temp_root / "scripts/prepare_worker_release.py"), "--check", "--repo-root", str(temp_root)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, res.returncode)
            self.assertIn("RELEASE_COHERENCE_CHECK=FAIL", res.stdout)

    def test_prepare_worker_release_fails_on_wrong_channel(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coherence-test-") as temp_dir:
            temp_root = pathlib.Path(temp_dir)
            shutil.copytree(ROOT / "aot-group-control", temp_root / "aot-group-control")
            shutil.copytree(ROOT / "cloudflare-worker", temp_root / "cloudflare-worker")
            shutil.copytree(ROOT / "scripts", temp_root / "scripts")
            
            # Mutate channel
            stable_manifest_path = temp_root / "aot-group-control" / "worker-manifest-stable.json"
            data = json.loads(stable_manifest_path.read_text(encoding="utf-8"))
            data["channel"] = "canary"
            stable_manifest_path.write_text(json.dumps(data), encoding="utf-8")

            res = subprocess.run(
                [sys.executable, str(temp_root / "scripts/prepare_worker_release.py"), "--check", "--repo-root", str(temp_root)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, res.returncode)
            self.assertIn("RELEASE_COHERENCE_CHECK=FAIL", res.stdout)

    def test_prepare_worker_release_fails_on_missing_file_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coherence-test-") as temp_dir:
            temp_root = pathlib.Path(temp_dir)
            shutil.copytree(ROOT / "aot-group-control", temp_root / "aot-group-control")
            shutil.copytree(ROOT / "cloudflare-worker", temp_root / "cloudflare-worker")
            shutil.copytree(ROOT / "scripts", temp_root / "scripts")
            
            stable_manifest_path = temp_root / "aot-group-control" / "worker-manifest-stable.json"
            data = json.loads(stable_manifest_path.read_text(encoding="utf-8"))
            data["files"].pop()
            stable_manifest_path.write_text(json.dumps(data), encoding="utf-8")

            res = subprocess.run(
                [sys.executable, str(temp_root / "scripts/prepare_worker_release.py"), "--check", "--repo-root", str(temp_root)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, res.returncode)
            self.assertIn("RELEASE_COHERENCE_CHECK=FAIL", res.stdout)

    def test_builder_fails_on_invalid_calver_dates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="build-invalid-") as temp_dir:
            out = pathlib.Path(temp_dir) / "out"
            cmd = [
                sys.executable,
                str(ROOT / "scripts/build-worker-release.py"),
                "--version", "2026.02.31.01",
                "--commit", "a" * 40,
                "--output", str(out),
                "--source-root", str(ROOT),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(0, res.returncode)

    def test_builder_fails_on_malformed_source_date_epoch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="build-epoch-") as temp_dir:
            out = pathlib.Path(temp_dir) / "out"
            cmd = [
                sys.executable,
                str(ROOT / "scripts/build-worker-release.py"),
                "--version", "2026.08.23.03",
                "--commit", "a" * 40,
                "--output", str(out),
                "--source-root", str(ROOT),
            ]
            env = os.environ.copy()
            env["SOURCE_DATE_EPOCH"] = "not-a-number"
            res = subprocess.run(cmd, env=env, capture_output=True, text=True)
            self.assertNotEqual(0, res.returncode)

    def test_canonical_loader_validates_and_rejects_mismatched_stored_fields(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import release_version
        with tempfile.TemporaryDirectory(prefix="canonical-test-") as temp_dir:
            vfile = pathlib.Path(temp_dir) / "worker-release.json"
            vfile.write_text(json.dumps({
                "version": "2026.08.23.03",
                "worker_version": "aot-worker-2026.08.23.99"
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                release_version.load_canonical_version(vfile)


class BootstrapSupervisorReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="bootstrap-test-")
        self.root = pathlib.Path(self.tmp.name)
        
        spec = importlib.util.spec_from_file_location("bootstrap_under_test", ROOT / "aot-group-control/bootstrap.py")
        self.bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bootstrap)
        
        self.bootstrap.ROOT = self.root
        self.bootstrap.RELEASES = self.root / "releases"
        self.bootstrap.CURRENT = self.root / "current"
        self.bootstrap.LAST_GOOD = self.root / "last_good"
        self.bootstrap.PENDING_PATH = self.root / "update_pending.json"
        self.bootstrap.HEALTH_PATH = self.root / "update_health.json"
        self.bootstrap.LOCK_PATH = self.root / "supervisor.lock"
        self.bootstrap.VERSION_PATH = self.root / "worker_version.json"
        self.bootstrap.LOG_PATH = self.root / "relay.log"
        self.bootstrap.DEVICE_ID_PATH = self.root / "device_id.txt"
        self.bootstrap.CONFIG_PATH = self.root / "aot_group_config.json"
        self.bootstrap.AGENT_CONFIG_PATH = self.root / "agent_config.json"
        
        self.bootstrap.DEVICE_ID_PATH.write_text("m74\n", encoding="utf-8")
        self.bootstrap.CONFIG_PATH.write_text(json.dumps({"device_id": "m74", "enabled": True}), encoding="utf-8")
        self.bootstrap.AGENT_CONFIG_PATH.write_text(json.dumps({
            "worker_report_url": "https://example.test/aot/report",
            "agent_report_secret": "secret"
        }), encoding="utf-8")
        
        self.bootstrap.RELEASES.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create_fake_release(self, name: str) -> pathlib.Path:
        rel = self.bootstrap.RELEASES / name
        rel.mkdir(parents=True, exist_ok=True)
        (rel / "relay.py").write_text("print('fake relay')\n", encoding="utf-8")
        return rel

    def test_fresh_install_healthy_promotes_last_good_only_after_health(self):
        rel = self.create_fake_release("aot-worker-2026.08.23.03")
        self.bootstrap._atomic_link(self.bootstrap.CURRENT, rel)
        self.assertIsNone(self.bootstrap._link_target(self.bootstrap.LAST_GOOD))
        
        def mock_start_worker(cfg):
            pending = json.loads(self.bootstrap.PENDING_PATH.read_text(encoding="utf-8"))
            self.bootstrap.notify_health(pending["action_id"], pending["version"])
            return None
        self.bootstrap.start_worker = mock_start_worker
        
        rc = self.bootstrap.startup()
        self.assertEqual(0, rc)
        self.assertEqual(rel, self.bootstrap._link_target(self.bootstrap.LAST_GOOD))
        self.assertEqual(rel, self.bootstrap._link_target(self.bootstrap.CURRENT))

    def test_fresh_install_health_timeout_does_not_promote_last_good(self):
        rel = self.create_fake_release("aot-worker-2026.08.23.03")
        self.bootstrap._atomic_link(self.bootstrap.CURRENT, rel)
        self.assertIsNone(self.bootstrap._link_target(self.bootstrap.LAST_GOOD))
        
        self.bootstrap.start_worker = lambda cfg: None
        self.bootstrap.wait_for_health = lambda pending, timeout=0: False
        
        with self.assertRaises(self.bootstrap.BootstrapError):
            self.bootstrap.startup()
            
        self.assertIsNone(self.bootstrap._link_target(self.bootstrap.LAST_GOOD))
        self.assertIsNone(self.bootstrap._link_target(self.bootstrap.CURRENT))

    def test_existing_last_good_survives_failed_candidate(self):
        old_rel = self.create_fake_release("aot-worker-2026.08.23.01")
        new_rel = self.create_fake_release("aot-worker-2026.08.23.03")
        
        self.bootstrap._atomic_link(self.bootstrap.CURRENT, new_rel)
        self.bootstrap._atomic_link(self.bootstrap.LAST_GOOD, old_rel)
        
        self.bootstrap.start_worker = lambda cfg: None
        self.bootstrap.wait_for_health = lambda pending, timeout=0: False
        
        with self.assertRaises(self.bootstrap.BootstrapError):
            self.bootstrap.startup()
            
        self.assertEqual(old_rel, self.bootstrap._link_target(self.bootstrap.CURRENT))
        self.assertEqual(old_rel, self.bootstrap._link_target(self.bootstrap.LAST_GOOD))

    def test_immutable_release_provenance_enforced(self):
        spec = copy.deepcopy(self.bootstrap.INITIAL_RELEASE_SPEC)
        spec["commit_sha"] = "0" * 40
        with self.assertRaises(self.bootstrap.BootstrapError):
            self.bootstrap.resolve_release_manifest(spec, "stable")


if __name__ == "__main__":
    unittest.main()
