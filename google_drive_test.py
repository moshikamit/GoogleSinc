import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def load_or_create_credentials():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    return creds


def main():
    print("Starting Google Drive authentication test...")

    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"Missing {CREDENTIALS_FILE}. Put the downloaded OAuth client JSON in the project root."
        )

    creds = load_or_create_credentials()
    service = build("drive", "v3", credentials=creds)

    results = (
        service.files()
        .list(pageSize=10, fields="nextPageToken, files(id, name, mimeType)")
        .execute()
    )

    items = results.get("files", [])

    if not items:
        print("No files found in the authenticated Drive account.")
        return

    print("Drive files found:")
    for item in items:
        print(f"- {item.get('name')} ({item.get('id')})")


if __name__ == "__main__":
    main()
