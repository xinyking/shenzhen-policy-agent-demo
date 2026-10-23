import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from app.utils.storage_budget import ensure_capacity, directory_bytes, StorageLimit
from app.knowledge.ingestion import save_snapshot, parse_html
from test_ingestion import SOURCE, RAW, ROOT

class StorageTests(unittest.TestCase):
    def test_removed_temporary_file_does_not_break_capacity_scan(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as name:
            with patch('app.utils.storage_budget.os.walk', return_value=[(name, [], ['already-removed.tmp'])]):
                self.assertEqual(directory_bytes(name), 0)
    def test_exact_boundary_and_project_files_count(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as name:
            root = Path(name)
            (root / 'dependency.bin').write_bytes(b'x' * 100)
            self.assertEqual(directory_bytes(root), 100)
            self.assertEqual(ensure_capacity(root, 10, 110), 100)
            with self.assertRaises(StorageLimit):
                ensure_capacity(root, 11, 110)

    def test_snapshot_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as name:
            root = Path(name)
            with self.assertRaises(StorageLimit):
                save_snapshot(root / 'data/store', SOURCE, RAW, parse_html(RAW, SOURCE),
                              storage_root=root, max_total_bytes=1)
            self.assertFalse((root / 'data').exists())
