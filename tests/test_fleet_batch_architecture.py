from __future__ import annotations
import importlib.util, pathlib, sys, unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fleet_controller", ROOT / "aot-group-control/controller.py")
CONTROLLER = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = CONTROLLER; SPEC.loader.exec_module(CONTROLLER)

APPS = "<hierarchy><node class='Root' resource-id='root' clickable='false' enabled='true' scrollable='false' password='false' bounds='[0,0][100,100]'><node class='Button' resource-id='org.swiftapps.swiftbackup:id/nav_apps' text='Apps' content-desc='Apps' clickable='true' enabled='true' scrollable='false' selected='false' password='false' bounds='[0,50][100,100]'/></node></hierarchy>"
OPEN = APPS.replace("selected='false'", "selected='true'")

class SwiftAppsSemanticTests(unittest.TestCase):
    def run_action(self, dumps, packages=None):
        packages = packages or [CONTROLLER.SWIFT_BACKUP_PACKAGE, CONTROLLER.SWIFT_BACKUP_PACKAGE]
        with mock.patch.object(CONTROLLER, "foreground_package", side_effect=packages), mock.patch.object(CONTROLLER, "dump_ui_xml", side_effect=dumps), mock.patch.object(CONTROLLER, "_tap_xy") as tap, mock.patch.object(CONTROLLER.time, "sleep"):
            result = CONTROLLER.open_swift_apps()
            return result, tap.call_count

    def test_semantic_success(self):
        result, taps = self.run_action([APPS, APPS, OPEN])
        self.assertTrue(result["executed"]); self.assertEqual(1, taps)

    def test_wrong_package_does_not_click(self):
        with mock.patch.object(CONTROLLER, "foreground_package", return_value="other.pkg"), mock.patch.object(CONTROLLER, "_tap_xy") as tap:
            with self.assertRaisesRegex(CONTROLLER.AotControllerError, "not_foreground"):
                CONTROLLER.open_swift_apps()
            tap.assert_not_called()

    def test_changed_fingerprint_does_not_click(self):
        changed = APPS.replace("nav_apps", "nav_apps_changed")
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=CONTROLLER.SWIFT_BACKUP_PACKAGE), mock.patch.object(CONTROLLER, "dump_ui_xml", side_effect=[APPS, changed]), mock.patch.object(CONTROLLER, "_tap_xy") as tap:
            with self.assertRaisesRegex(CONTROLLER.AotControllerError, "precondition_changed"):
                CONTROLLER.open_swift_apps()
            tap.assert_not_called()

    def test_bad_postcondition_never_reports_success(self):
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=CONTROLLER.SWIFT_BACKUP_PACKAGE), mock.patch.object(CONTROLLER, "dump_ui_xml", side_effect=[APPS, APPS, APPS]), mock.patch.object(CONTROLLER, "_tap_xy") as tap, mock.patch.object(CONTROLLER.time, "sleep"):
            with self.assertRaisesRegex(CONTROLLER.AotControllerError, "postcondition_failed"):
                CONTROLLER.open_swift_apps()
            tap.assert_called_once()

class FleetArchitectureTests(unittest.TestCase):
    def test_new_protocol_is_device_only_and_legacy_control_is_gone(self):
        worker = (ROOT / "cloudflare-worker/worker.js").read_text()
        dashboard = worker[worker.index("function fleetHubHtml()"):worker.index("async function handleAotHubPage")]
        for forbidden in ("session_id", "REFERENCE", "FOLLOWERS", "preview_b64", "x_norm", "y_norm"):
            self.assertNotIn(forbidden, dashboard)
        self.assertIn("cross_device_control_removed", worker)
        relay = (ROOT / "aot-group-control/relay.py").read_text()
        parser = relay[relay.index("def build_parser"):]
        self.assertNotIn('sub.add_parser("reference")', parser)
        self.assertNotIn('sub.add_parser("follower")', parser)

    def test_deployed_point_eight_has_one_way_update_adapter(self):
        worker = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertIn('body.device_id || body.follower_device_id', worker)
        self.assertIn('[AOT_HUB_PROTOCOL_VERSION, "phase4-1"]', worker)
        bridge = (ROOT / "aot-group-control/legacy_relay_bridge.py").read_text()
        self.assertIn("update", bridge.lower())
        for forbidden in ("tap_selector", "tap_normalized", "swipe_normalized", "preview_b64"):
            self.assertNotIn(forbidden, bridge)

    def test_dead_legacy_aotHubHtml_is_removed(self):
        """Regression: aotHubHtml with REFERENCE/FOLLOWERS UI must not exist."""
        worker = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertNotIn("function aotHubHtml()", worker,
                         "dead legacy dashboard aotHubHtml must be deleted")
        # The active dashboard must still be present
        self.assertIn("function fleetHubHtml()", worker)
        self.assertIn("async function handleAotHubPage", worker)

    def test_active_fleet_dashboard_has_both_batch_buttons(self):
        """Active fleet dashboard must have both Swift Backup and Apps buttons."""
        worker = (ROOT / "cloudflare-worker/worker.js").read_text()
        start = worker.index("function fleetHubHtml()")
        end = worker.index("async function handleAotHubPage", start)
        dashboard = worker[start:end]
        self.assertIn("open_swift_backup", dashboard,
                       "fleet dashboard must have open_swift_backup action")
        self.assertIn("open_swift_apps", dashboard,
                       "fleet dashboard must have open_swift_apps action")
        self.assertIn("Mở Swift Backup", dashboard,
                       "fleet dashboard must show Swift Backup button label")
        self.assertIn("Mở Apps", dashboard,
                       "fleet dashboard must show Apps button label")

if __name__ == "__main__": unittest.main()
