"""Google Photos uploader — APPENDONLY scope only.

Uploads NEW media to a dedicated album. By Google's API design this cannot touch, edit, or delete any
of the user's existing photos — it can only add new items to an album the app created. Used to push
approved enhanced photos and highlight reels after explicit review-queue approval.

Without OAuth client creds, ``enabled()`` is False and the Review Queue exports files to a folder
instead (the user uploads manually).
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings

SCOPES = ["https://www.googleapis.com/auth/photoslibrary.appendonly"]
_TOKEN = "google-photos-token.json"
_API = "https://photoslibrary.googleapis.com/v1"


def enabled() -> bool:
    return settings.uploads_enabled


def _credentials():
    """Installed-app OAuth flow; caches the token under data/. Raises if creds missing."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not enabled():
        raise RuntimeError("Photos uploads disabled — set PHOTOS_OAUTH_CLIENT_ID/SECRET in .env")

    token_path = settings.data_dir / _TOKEN
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": settings.photos_client_id,
                    "client_secret": settings.photos_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


def _session():
    import google.auth.transport.requests as greq

    creds = _credentials()
    authed = greq.AuthorizedSession(creds)
    return authed


def _find_album(session, title: str) -> str | None:
    """Look up an existing album by title (paginated) so repeated uploads share one album instead of
    minting a new "Photo Curator (curated)" copy every call."""
    page_token = None
    while True:
        params = {"pageSize": 50}
        if page_token:
            params["pageToken"] = page_token
        resp = session.get(f"{_API}/albums", params=params)
        resp.raise_for_status()
        data = resp.json()
        for album in data.get("albums", []):
            if album.get("title") == title:
                return album["id"]
        page_token = data.get("nextPageToken")
        if not page_token:
            return None


def _ensure_album(session, title: str) -> str:
    existing = _find_album(session, title)
    if existing:
        return existing
    resp = session.post(f"{_API}/albums", json={"album": {"title": title}})
    resp.raise_for_status()
    return resp.json()["id"]


def upload_files(paths: list[Path], album_title: str | None = None) -> dict:
    """Upload each file as a new media item into ``album_title`` (created/reused as needed). Returns the
    album id/title plus a per-file result (``product_url`` on success, ``error`` on failure)."""
    session = _session()
    title = album_title or settings.photos_album
    album_id = _ensure_album(session, title)

    tokens = []
    for p in paths:
        with open(p, "rb") as f:
            up = session.post(
                f"{_API}/uploads",
                headers={"Content-type": "application/octet-stream",
                         "X-Goog-Upload-Protocol": "raw",
                         "X-Goog-Upload-File-Name": p.name},
                data=f.read(),
            )
            up.raise_for_status()
            tokens.append((p.name, up.text))

    uploaded = []
    for name, token in tokens:
        batch = session.post(
            f"{_API}/mediaItems:batchCreate",
            json={"albumId": album_id,
                  "newMediaItems": [{"description": name,
                                     "simpleMediaItem": {"uploadToken": token}}]},
        )
        batch.raise_for_status()
        result = batch.json()["newMediaItemResults"][0]
        status = result.get("status", {})
        item = {"name": name}
        if "mediaItem" in result:
            item["product_url"] = result["mediaItem"].get("productUrl")
        else:
            item["error"] = status.get("message", "upload failed")
        uploaded.append(item)
    return {"album_id": album_id, "album_title": title, "uploaded": uploaded}
