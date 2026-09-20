from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SyncPlan:
    """The actions the sync engine should take for one folder pair."""

    uploads: List[str] = field(default_factory=list)
    downloads: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    local_deletions: List[str] = field(default_factory=list)
    remote_deletions: List[str] = field(default_factory=list)
    stale_records: List[str] = field(default_factory=list)
    execute_deletes: bool = False

    def summary(self) -> str:
        return (
            f"uploads={len(self.uploads)} downloads={len(self.downloads)} "
            f"unchanged={len(self.unchanged)} conflicts={len(self.conflicts)} "
            f"local_deletions={len(self.local_deletions)} "
            f"remote_deletions={len(self.remote_deletions)} "
            f"stale_records={len(self.stale_records)}"
        )


def _remote_md5(remote_item: Dict) -> Optional[str]:
    return remote_item.get("md5Checksum")


def _local_changed(stored: Optional[Dict], local: Dict) -> bool:
    if stored is None:
        return True
    return stored.get("local_md5") != local["md5"]


def _remote_changed(stored: Optional[Dict], remote: Dict) -> bool:
    if stored is None:
        return True
    stored_remote_md5 = stored.get("remote_md5")
    if stored_remote_md5 is None:
        return False
    return stored_remote_md5 != _remote_md5(remote)


def build_sync_plan(
    local_scan: Dict[str, Dict],
    remote_files: List[Dict],
    stored_state: List[Dict],
    execute_deletes: bool = False,
) -> SyncPlan:
    """Compare local files, Drive files, and stored metadata into a plan.

    Decision rules per path:
    - only local, never synced      -> upload (new file)
    - only local, synced before     -> remote deletion or conflict:
                                       remote vanished but local unchanged
                                       means it was deleted on Drive;
                                       if local also changed, it is a conflict
    - only remote, never synced     -> download (new remote file)
    - only remote, synced before    -> local deletion (remote unchanged)
                                       or conflict (remote also changed)
    - both sides, local changed     -> upload
    - both sides, remote changed    -> download
    - both sides, both changed      -> conflict (reported, not executed)
    - both sides, neither changed   -> unchanged (skipped)
    - only in stored metadata       -> deleted on both sides (clean up record)

    Deletions are collected in the plan but the engine only performs them
    when execute_deletes is True.
    """
    plan = SyncPlan()
    plan.execute_deletes = execute_deletes
    remote_by_name = {item["name"]: item for item in remote_files}
    stored_by_path = {row["path"]: row for row in stored_state}

    all_paths = set(local_scan) | set(remote_by_name) | set(stored_by_path)

    for path in sorted(all_paths):
        local = local_scan.get(path)
        remote = remote_by_name.get(path)
        stored = stored_by_path.get(path)

        if local and not remote:
            if stored is None:
                plan.uploads.append(path)
            elif _local_changed(stored, local):
                # Remote copy vanished AND local changed since last sync:
                # we cannot know which side should win -> conflict.
                plan.conflicts.append(path)
            else:
                # Remote copy of a previously synced file is gone and the
                # local file is untouched: it was deleted on Drive.
                plan.remote_deletions.append(path)
            continue

        if remote and not local:
            if stored is None:
                plan.downloads.append(path)
            elif _remote_changed(stored, remote):
                plan.conflicts.append(path)
            else:
                plan.local_deletions.append(path)
            continue

        if local and remote:
            local_changed = _local_changed(stored, local)
            remote_changed = _remote_changed(stored, remote)

            if local_changed and remote_changed:
                plan.conflicts.append(path)
            elif local_changed:
                plan.uploads.append(path)
            elif remote_changed:
                plan.downloads.append(path)
            else:
                plan.unchanged.append(path)
            continue

        # Not on disk and not in Drive: only a stale metadata record remains.
        plan.stale_records.append(path)

    return plan
