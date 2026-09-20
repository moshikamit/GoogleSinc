import hashlib
import os
from typing import Dict

CHUNK_SIZE = 1024 * 1024


def compute_md5(file_path: str) -> str:
    """Compute the MD5 hex digest of a file, reading it in chunks."""
    digest = hashlib.md5()
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def scan_folder(local_root: str) -> Dict[str, Dict]:
    """Scan a local folder recursively.

    Returns a mapping of relative path (forward-slash separated) to file
    facts: absolute path, size in bytes, modification time, and MD5 checksum.
    """
    results: Dict[str, Dict] = {}
    for root, _, files in os.walk(local_root):
        for name in files:
            full_path = os.path.join(root, name)
            relative_path = os.path.relpath(full_path, local_root).replace(os.sep, "/")
            stat = os.stat(full_path)
            results[relative_path] = {
                "full_path": full_path,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "md5": compute_md5(full_path),
            }
    return results
