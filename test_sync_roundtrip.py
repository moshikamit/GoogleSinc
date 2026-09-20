import os
import tempfile

from app.sync_engine import SyncEngine


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_dir = os.path.join(tmp_dir, "local")
        os.makedirs(local_dir, exist_ok=True)

        file_path = os.path.join(local_dir, "roundtrip.txt")
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("hello from round-trip sync")

        sync = SyncEngine(local_root=local_dir, drive_folder_name="GoogleSinc_Test_Folder")
        sync.sync_local_to_drive()

        download_dir = os.path.join(tmp_dir, "downloaded")
        os.makedirs(download_dir, exist_ok=True)
        downloaded = sync.sync_drive_to_local(download_dir)

        print("Downloaded files:")
        for item in downloaded:
            print(item)

        print("Download content:")
        with open(os.path.join(download_dir, "roundtrip.txt"), "r", encoding="utf-8") as handle:
            print(handle.read())


if __name__ == "__main__":
    main()
