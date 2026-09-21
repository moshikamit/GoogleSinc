import io
import os
import socket
from typing import Any, Callable, Dict, List, Optional

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload
from google_auth_httplib2 import AuthorizedHttp

SCOPES = ["https://www.googleapis.com/auth/drive"]

# Generous timeout for large listings/downloads over a real connection.
HTTP_TIMEOUT_SECONDS = 120

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id, name, mimeType, parents, size, modifiedTime, md5Checksum, trashed"


class GoogleDriveService:
    """Reusable helper for desktop-app Google Drive authentication and access."""

    def __init__(
        self,
        credentials_path: str = "credentials.json",
        token_path: str = "token.json",
        scopes: Optional[List[str]] = None,
    ) -> None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.credentials_path = os.path.join(project_root, credentials_path)
        self.token_path = os.path.join(project_root, token_path)
        self.scopes = scopes or SCOPES

    def _save_token(self, creds: Credentials) -> None:
        with open(self.token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    def get_credentials(self) -> Credentials:
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, self.scopes)
            if creds and creds.valid:
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_token(creds)
                return creds

        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(
                f"Missing OAuth credentials file: {self.credentials_path}. "
                "Download the JSON from Google Cloud Console and save it in the project root."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            self.credentials_path,
            self.scopes,
        )
        creds = flow.run_local_server(port=0)
        self._save_token(creds)
        return creds

    def get_service(self):
        # Use an explicit HTTP timeout so large listings/downloads don't die
        # on the library's short default read timeout. AuthorizedHttp applies
        # the OAuth credentials on top of our timeout-configured transport.
        http = AuthorizedHttp(self.get_credentials(), http=httplib2.Http(timeout=HTTP_TIMEOUT_SECONDS))
        return build("drive", "v3", http=http)

    def list_files(self, page_size: int = 10) -> List[Dict[str, Any]]:
        service = self.get_service()
        results = service.files().list(
            pageSize=page_size,
            fields="files(id, name, mimeType, parents)",
        ).execute()
        return results.get("files", [])

    def list_files_in_folder(
        self,
        folder_id: str,
        page_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        service = self.get_service()
        query = f"'{folder_id}' in parents and trashed = false"
        results = service.files().list(
            q=query,
            pageSize=page_size,
            spaces="drive",
            fields=f"files({FILE_FIELDS})",
        ).execute()
        return results.get("files", [])

    def list_all_my_drive(
        self,
        page_size: int = 1000,
        should_stop: Optional[Callable[[], bool]] = None,
        on_page: Optional[Callable[[int, int], None]] = None,
        start_page_token: Optional[str] = None,
    ) -> tuple:
        """List non-trashed files/folders in My Drive, paged and resumable.

        Returns (items, next_page_token, completed). `completed` is False when
        a should_stop callback interrupted the listing; next_page_token then
        lets a later call resume instead of restarting from scratch.
        """
        service = self.get_service()
        items: List[Dict[str, Any]] = []
        page_token: Optional[str] = start_page_token
        fields = f"nextPageToken, files({FILE_FIELDS})"
        fetched = 0
        while True:
            if should_stop and should_stop():
                return items, page_token, False
            results = service.files().list(
                q="trashed = false",
                pageSize=page_size,
                spaces="drive",
                fields=fields,
                pageToken=page_token,
            ).execute()
            batch = results.get("files", [])
            items.extend(batch)
            fetched += 1
            if on_page:
                on_page(fetched, len(items))
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        return items, None, True

    def get_root_id(self) -> str:
        service = self.get_service()
        return service.files().get(fileId="root", fields="id").execute()["id"]

    def find_folder_by_name(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service = self.get_service()
        query = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        else:
            query += " and 'root' in parents"

        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name, mimeType, parents)",
            pageSize=10,
        ).execute()

        files = results.get("files", [])
        return files[0] if files else None

    def find_or_create_folder(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = self.find_folder_by_name(name=name, parent_id=parent_id)
        if existing:
            return existing
        return self.create_folder(name=name, parent_id=parent_id)

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> Dict[str, Any]:
        service = self.get_service()
        file_metadata: Dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            file_metadata["parents"] = [parent_id]

        return service.files().create(
            body=file_metadata,
            fields="id, name, mimeType",
        ).execute()

    def upload_text_file(
        self,
        name: str,
        content: str,
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        service = self.get_service()
        metadata: Dict[str, Any] = {"name": name}
        if parent_id:
            metadata["parents"] = [parent_id]

        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")),
            mimetype="text/plain",
            resumable=False,
        )

        return service.files().create(
            body=metadata,
            media_body=media,
            fields="id, name, mimeType",
        ).execute()

    def upload_local_file(
        self,
        local_path: str,
        remote_name: Optional[str] = None,
        parent_id: Optional[str] = None,
        file_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        service = self.get_service()
        media = MediaFileUpload(local_path, resumable=True)
        fields = "id, name, mimeType, size, md5Checksum, modifiedTime"

        if file_id:
            return service.files().update(
                fileId=file_id,
                media_body=media,
                fields=fields,
            ).execute()

        metadata: Dict[str, Any] = {"name": remote_name or os.path.basename(local_path)}
        if parent_id:
            metadata["parents"] = [parent_id]

        return service.files().create(
            body=metadata,
            media_body=media,
            fields=fields,
        ).execute()

    def download_file(self, file_id: str, destination_path: str) -> str:
        service = self.get_service()
        request = service.files().get_media(fileId=file_id)

        with open(destination_path, "wb") as destination_file:
            downloader = MediaIoBaseDownload(destination_file, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

        return destination_path

    def download_file_by_name(
        self,
        folder_id: str,
        file_name: str,
        destination_path: str,
    ) -> str:
        files = self.list_files_in_folder(folder_id)
        match = next((item for item in files if item.get("name") == file_name), None)
        if not match:
            raise FileNotFoundError(f"File '{file_name}' not found in folder '{folder_id}'.")
        return self.download_file(match["id"], destination_path)

    def delete_file(self, file_id: str) -> None:
        """Move a Drive file to the trash (recoverable for 30 days).

        This mirrors Google Drive for Desktop: a local delete moves the cloud
        copy to Drive trash, not a permanent deletion.
        """
        service = self.get_service()
        service.files().update(fileId=file_id, body={"trashed": True}).execute()

    def delete_file_permanently(self, file_id: str) -> None:
        service = self.get_service()
        service.files().delete(fileId=file_id).execute()
