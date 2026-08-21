#!/usr/bin/env python3
# test_architecture_guards.py - Enforceable Architecture and Layer Boundary Guards

from __future__ import annotations

import ast
import os
import pathlib
import re
import sys
import unittest
from typing import Dict, List, Set, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AOT_GROUP_CONTROL = REPO_ROOT / "aot-group-control"
CLOUDFLARE_WORKER = REPO_ROOT / "cloudflare-worker"
ANTIGRAVINY_MIGRATION = REPO_ROOT / "antigraviny_migration"


class ArchitectureViolationError(Exception):
    pass


class ImportExtractor(ast.NodeVisitor):
    def __init__(self, filename: str = "") -> None:
        self.filename = filename
        self.imports: List[Tuple[str, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base_module = alias.name.split(".")[0]
            self.imports.append((base_module, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base_module = node.module.split(".")[0]
            self.imports.append((base_module, node.lineno))
        elif node.level and node.level > 0:
            self.imports.append((f".relative_level_{node.level}", node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                base_module = node.args[0].value.split(".")[0]
                self.imports.append((base_module, node.lineno))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                base_module = node.args[0].value.split(".")[0]
                self.imports.append((base_module, node.lineno))
        self.generic_visit(node)


def extract_python_imports(source_code: str, filename: str = "<string>") -> List[Tuple[str, int]]:
    tree = ast.parse(source_code, filename=filename)
    extractor = ImportExtractor(filename=filename)
    extractor.visit(tree)
    return extractor.imports


def extract_js_imports(source_code: str, filename: str = "<string>") -> List[Tuple[str, int]]:
    # Replace block comments with exact equivalent newline counts to preserve line numbering
    cleaned = re.sub(r"/\*[\s\S]*?\*/", lambda m: "\n" * m.group(0).count("\n"), source_code)
    # Remove single line comments
    cleaned = re.sub(r"//[^\n]*", "", cleaned)

    imports: List[Tuple[str, int]] = []
    # Match ES imports (single or multiline), side-effect imports, export from, dynamic imports and CommonJS require
    pattern = r"""(?:\bimport\s+(?:(?:[\w\s{},*]+|\{[^}]*\})\s+from\s+)?|\bexport\s+(?:(?:[\w\s{},*]+|\{[^}]*\})\s+from\s+)|\brequire\s*\(\s*|\bimport\s*\(\s*)["']([^"']+)["']"""
    import_regex = re.compile(pattern, flags=re.DOTALL)
    for match in import_regex.finditer(cleaned):
        target = match.group(1)
        lineno = 1 + cleaned.count("\n", 0, match.start())
        imports.append((target, lineno))
    return imports


def check_boundary_rules(
    source_code: str,
    forbidden_modules: Set[str],
    filename: str = "<string>"
) -> List[str]:
    violations = []
    imports = extract_python_imports(source_code, filename)
    for mod_name, lineno in imports:
        if mod_name in forbidden_modules:
            violations.append(
                f"Architecture violation in {filename}:{lineno}: "
                f"forbidden import {mod_name!r}"
            )
    return violations


class TestPythonArchitectureGuards(unittest.TestCase):
    def test_controller_is_leaf_execution_boundary(self) -> None:
        controller_path = AOT_GROUP_CONTROL / "controller.py"
        self.assertTrue(controller_path.exists(), f"Missing {controller_path}")
        source = controller_path.read_text(encoding="utf-8")

        forbidden = {
            "relay",
            "updater",
            "bootstrap",
            "bootstrap_launcher",
            "msetup_registration",
            "legacy_relay_bridge",
            "e2e",
        }
        violations = check_boundary_rules(source, forbidden, filename="controller.py")
        self.assertEqual(
            violations,
            [],
            f"controller.py violated layer boundary: {violations}"
        )

    def test_runtime_is_leaf_state_boundary(self) -> None:
        runtime_path = AOT_GROUP_CONTROL / "runtime.py"
        self.assertTrue(runtime_path.exists(), f"Missing {runtime_path}")
        source = runtime_path.read_text(encoding="utf-8")

        forbidden = {"relay", "controller", "updater", "bootstrap", "bootstrap_launcher"}
        violations = check_boundary_rules(source, forbidden, filename="runtime.py")
        self.assertEqual(
            violations,
            [],
            f"runtime.py violated layer boundary: {violations}"
        )

    def test_updater_one_way_ownership(self) -> None:
        updater_path = AOT_GROUP_CONTROL / "updater.py"
        self.assertTrue(updater_path.exists(), f"Missing {updater_path}")
        source = updater_path.read_text(encoding="utf-8")

        forbidden = {"controller", "relay", "msetup_registration", "legacy_relay_bridge"}
        violations = check_boundary_rules(source, forbidden, filename="updater.py")
        self.assertEqual(
            violations,
            [],
            f"updater.py violated layer boundary: {violations}"
        )

    def test_bootstrap_launcher_isolation(self) -> None:
        for fname in ["bootstrap.py", "bootstrap_launcher.py"]:
            fpath = AOT_GROUP_CONTROL / fname
            if not fpath.exists():
                continue
            source = fpath.read_text(encoding="utf-8")
            forbidden = {"controller", "relay"}
            violations = check_boundary_rules(source, forbidden, filename=fname)
            self.assertEqual(
                violations,
                [],
                f"{fname} violated layer boundary: {violations}"
            )

    def test_python_call_graph_has_no_circular_dependencies(self) -> None:
        internal_modules = {
            p.stem for p in AOT_GROUP_CONTROL.glob("*.py")
            if not p.name.endswith("_selftest.py") and not p.name.startswith("test_")
        }

        graph: Dict[str, Set[str]] = {m: set() for m in internal_modules}
        for mod in internal_modules:
            fpath = AOT_GROUP_CONTROL / f"{mod}.py"
            if not fpath.exists():
                continue
            source = fpath.read_text(encoding="utf-8")
            for imp, _ in extract_python_imports(source, filename=f"{mod}.py"):
                if imp in internal_modules and imp != mod:
                    graph[mod].add(imp)

        visited = set()
        rec_stack = set()

        def dfs(node: str, path: List[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, ()):
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    cycle = " -> ".join(path + [neighbor])
                    self.fail(f"Circular dependency detected in Python architecture: {cycle}")
            rec_stack.remove(node)

        for node in internal_modules:
            if node not in visited:
                dfs(node, [node])


class TestJavaScriptArchitectureGuards(unittest.TestCase):
    def test_fleet_state_does_not_import_worker(self) -> None:
        fleet_state_path = CLOUDFLARE_WORKER / "fleet-state.js"
        self.assertTrue(fleet_state_path.exists(), f"Missing {fleet_state_path}")
        source = fleet_state_path.read_text(encoding="utf-8")

        imports = extract_js_imports(source, filename="fleet-state.js")
        for target, lineno in imports:
            norm_target = pathlib.Path(target).name
            self.assertNotIn(
                norm_target,
                {"worker.js", "worker"},
                f"fleet-state.js:{lineno} forbidden reverse import of {target!r}"
            )

    def test_rollout_is_standalone_engine(self) -> None:
        rollout_path = CLOUDFLARE_WORKER / "rollout.js"
        if not rollout_path.exists():
            return
        source = rollout_path.read_text(encoding="utf-8")
        imports = extract_js_imports(source, filename="rollout.js")
        for target, lineno in imports:
            norm_target = pathlib.Path(target).name
            self.assertNotIn(norm_target, {"worker.js", "worker"}, f"rollout.js:{lineno} imports worker")
    def test_fleet_state_client_is_pure_transport_boundary(self) -> None:
        client_path = CLOUDFLARE_WORKER / "fleet-state-client.js"
        self.assertTrue(client_path.exists(), f"Missing {client_path}")
        source = client_path.read_text(encoding="utf-8")
        imports = extract_js_imports(source, filename="fleet-state-client.js")
        for target, lineno in imports:
            norm_target = pathlib.Path(target).name
            self.assertNotIn(norm_target, {"worker.js", "worker"}, f"fleet-state-client.js:{lineno} imports worker")
            self.assertNotIn(norm_target, {"fleet-state.js", "fleet-state"}, f"fleet-state-client.js:{lineno} imports fleet-state")

    def test_js_modules_have_no_circular_dependencies(self) -> None:
        js_files = {"worker.js", "fleet-state.js", "rollout.js", "fleet-state-client.js"}
        graph: Dict[str, Set[str]] = {f: set() for f in js_files}

        for js_file in js_files:
            fpath = CLOUDFLARE_WORKER / js_file
            if not fpath.exists():
                continue
            source = fpath.read_text(encoding="utf-8")
            for target, _ in extract_js_imports(source, filename=js_file):
                target_name = pathlib.Path(target).name
                if not target_name.endswith(".js"):
                    target_name += ".js"
                if target_name in js_files and target_name != js_file:
                    graph[js_file].add(target_name)

        visited = set()
        rec_stack = set()

        def dfs(node: str, path: List[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, ()):
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    cycle = " -> ".join(path + [neighbor])
                    self.fail(f"Circular dependency detected in JS architecture: {cycle}")
            rec_stack.remove(node)

        for node in js_files:
            if node not in visited:
                dfs(node, [node])


class TestAdversarialArchitectureGuards(unittest.TestCase):
    def test_adversarial_forbidden_edge_rejection(self) -> None:
        bad_code = "import os\nimport relay\n\ndef run():\n    pass"
        violations = check_boundary_rules(bad_code, {"relay"}, filename="controller.py")
        self.assertEqual(len(violations), 1)
        self.assertIn("forbidden import 'relay'", violations[0])
        self.assertIn("controller.py:2", violations[0])

    def test_adversarial_forbidden_from_import_rejection(self) -> None:
        bad_code = "from updater import download_release\n"
        violations = check_boundary_rules(bad_code, {"updater"}, filename="controller.py")
        self.assertEqual(len(violations), 1)
        self.assertIn("forbidden import 'updater'", violations[0])

    def test_adversarial_dynamic_import_rejection(self) -> None:
        bad_code_1 = "mod = __import__('relay')\n"
        violations_1 = check_boundary_rules(bad_code_1, {"relay"}, filename="controller.py")
        self.assertEqual(len(violations_1), 1)
        self.assertIn("forbidden import 'relay'", violations_1[0])

        bad_code_2 = "import importlib\nmod = importlib.import_module('updater')\n"
        violations_2 = check_boundary_rules(bad_code_2, {"updater"}, filename="controller.py")
        self.assertEqual(len(violations_2), 1)
        self.assertIn("forbidden import 'updater'", violations_2[0])

    def test_adversarial_comments_and_strings_are_immune(self) -> None:
        clean_code = (
            "# import relay\n"
            "# from updater import something\n"
            "msg = 'import relay is not used here'\n"
            "doc = '''\n"
            "This module does not import bootstrap.\n"
            "'''\n"
            "import os\n"
            "import sys\n"
        )
        violations = check_boundary_rules(clean_code, {"relay", "updater", "bootstrap"}, filename="controller.py")
        self.assertEqual(violations, [])

    def test_adversarial_allowed_edges_pass(self) -> None:
        valid_relay_code = (
            "import sys\n"
            "import controller\n"
            "import runtime\n"
        )
        violations = check_boundary_rules(valid_relay_code, {"forbidden_foreign_module"}, filename="relay.py")
        self.assertEqual(violations, [])

    def test_adversarial_js_multiline_import_parsing(self) -> None:
        multiline_js = (
            "import {\n"
            "  DurableObject,\n"
            "  WorkerEntrypoint,\n"
            "} from 'cloudflare:workers';\n"
            "import {\n"
            "  calculateRolloutGroup\n"
            "} from './rollout.js';\n"
        )
        imports = extract_js_imports(multiline_js, "worker.js")
        targets = [t for t, _ in imports]
        self.assertIn("cloudflare:workers", targets)
        self.assertIn("./rollout.js", targets)
        rollout_entry = next((item for item in imports if item[0] == "./rollout.js"), None)
        self.assertIsNotNone(rollout_entry)
        self.assertEqual(rollout_entry[1], 5)

    def test_adversarial_js_exact_name_matching_vs_cloudflare_workers(self) -> None:
        valid_fleet_js = "import { DurableObject } from 'cloudflare:workers';\n"
        imports = extract_js_imports(valid_fleet_js, "fleet-state.js")
        for target, lineno in imports:
            norm = pathlib.Path(target).name
            self.assertNotIn(norm, {"worker.js", "worker"})

    def test_adversarial_js_block_comment_line_number_preservation(self) -> None:
        code_with_block_comment = (
            "/*\n"
            " * Multi-line banner\n"
            " * line 3\n"
            " * line 4\n"
            " */\n"
            "import { FleetState } from './fleet-state.js';\n"
        )
        imports = extract_js_imports(code_with_block_comment, "test.js")
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0][0], "./fleet-state.js")
        self.assertEqual(imports[0][1], 6, f"Expected line 6, got line {imports[0][1]}")


if __name__ == "__main__":
    unittest.main()
