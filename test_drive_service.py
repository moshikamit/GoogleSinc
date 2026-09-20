from app.drive_service import GoogleDriveService


def main() -> None:
    service = GoogleDriveService()
    files = service.list_files(page_size=10)

    if not files:
        print("No files found.")
        return

    print("Files:")
    for item in files:
        print(f"{item.get('name')} ({item.get('id')})")


if __name__ == "__main__":
    main()
