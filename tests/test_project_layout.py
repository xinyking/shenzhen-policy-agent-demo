"""Directory migration contracts: importability, packaged paths and launch compatibility."""
import importlib
import json
import pkgutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.utils.path_utils import PROJECT_ROOT


class ProjectLayoutTests(unittest.TestCase):
    def test_application_modules_import_without_launching_server(self):
        import app
        names = [item.name for item in pkgutil.walk_packages(app.__path__, 'app.')]
        self.assertIn('app.agent.main_agent', names)
        self.assertIn('app.agent.subagents.schedule_planning_agent', names)
        for name in names:
            importlib.import_module(name)

    def test_demo_resources_exist(self):
        for name in ['app/agent/main_agent.py', 'app/api/server.py',
                     'scripts/bootstrap_demo.py', 'data/demo/sources.json']:
            self.assertTrue((PROJECT_ROOT / name).is_file(), name)

    def test_installed_import_and_legacy_launch_outside_project(self):
        # Use an explicitly writable sibling workspace, still outside the
        # project so cwd-dependent imports/assets cannot accidentally pass.
        with tempfile.TemporaryDirectory(prefix='policy-layout-', dir=PROJECT_ROOT.parent) as folder:
            result = subprocess.run([sys.executable, '-c',
                'from app.api.server import default_settings; from app.utils.path_utils import PROJECT_ROOT; '
                'assert default_settings().store == PROJECT_ROOT / "data/store"; '
                'assert (PROJECT_ROOT / "frontend/dist/index.html").is_file()'],
                cwd=folder, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, '-m', 'policy_agent.api', '--help'],
                                    cwd=folder, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0)
