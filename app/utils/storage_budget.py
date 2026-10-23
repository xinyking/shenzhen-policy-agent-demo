"""Conservative project-wide collection limit; includes dependencies and snapshots."""
import os
from pathlib import Path

MAX_TOTAL_BYTES = 10_000_000_000

class StorageLimit(ValueError):
    pass

def directory_bytes(root):
    total = 0
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    # Build/install temporary files may disappear between walk and stat.
                    # Only an already-removed file contributes zero; permission errors still fail closed.
                    continue
    return total

def ensure_capacity(root, additional=0, limit=MAX_TOTAL_BYTES):
    used = directory_bytes(root)
    if used + additional > limit:
        raise StorageLimit('已达到10 GB采集容量上限或新文档会超过上限；停止新增，不删除已有文件')
    return used
