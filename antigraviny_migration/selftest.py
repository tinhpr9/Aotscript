#!/usr/bin/env python3
"""
Standalone selftest for Antigraviny/Agy Migration System.
Tests backup, corrupt bundle detection, missing manifest, SHA mismatch,
restore into empty env, idempotency, rollback, and verification.
"""

import os
import sys
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.test_antigraviny_migration import TestAntigravinyMigration

if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAntigravinyMigration)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("ANTIGRAVINY_MIGRATION_SELFTEST=OK")
        sys.exit(0)
    else:
        print("ANTIGRAVINY_MIGRATION_SELFTEST=FAIL", file=sys.stderr)
        sys.exit(1)
