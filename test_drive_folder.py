from app.drive_service import GoogleDriveService


def main() -> None:
    service = GoogleDriveService()
    folder_name = "GoogleSinc_Test_Folder"
    folder = service.find_or_create_folder(folder_name)

    print(f"Folder: {folder.get('name')} ({folder.get('id')})")

    upload = service.upload_text_file(
        name="hello_from_sync_test.txt",
        content="This file was created by the GoogleSinc Drive helper test.",
        parent_id=folder.get("id"),
    )

    print(f"Uploaded: {upload.get('name')} ({upload.get('id')})")


if __name__ == "__main__":
    main()
