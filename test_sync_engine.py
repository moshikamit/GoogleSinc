import os
import tempfile

from app.sync_engine import SyncEngine


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        sample_file = os.path.join(tmp_dir, "sample.txt")
        with open(sample_file, "w", encoding="utf-8") as handle:
            handle.write("hello from local sync test")

        sync = SyncEngine(local_root=tmp_dir, drive_folder_name="GoogleSinc_Test_Folder")
        uploaded = sync.sync_local_to_drive()
        print("Uploaded files:")
        for item in uploaded:
            print(item)


if __name__ == "__main__":
    main()
