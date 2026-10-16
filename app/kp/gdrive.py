"""КП на Google Drive: PPTX -> Google Презентация в папке «КП 90 градусов», ссылка и PDF из Slides.

Доступ — OAuth владельца с правом drive.file: бот видит только файлы и папки, которые создал сам.
Файлы (не в git): data/kp/google_oauth_client.json (ключ клиента из Google Cloud),
data/kp/google_token.json (вход владельца), data/kp/google_drive.json (id папки, кому открыт доступ).

    .venv/bin/python app/kp/gdrive.py login            # один раз: вход в браузере
    .venv/bin/python app/kp/gdrive.py share a@gmail.com # открыть папку сотруднику (редактор)
    .venv/bin/python app/kp/gdrive.py test FILE.pptx    # загрузить, получить ссылку и PDF
"""
import io
import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "data/kp/google_oauth_client.json"
TOKEN = ROOT / "data/kp/google_token.json"
STATE = ROOT / "data/kp/google_drive.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "КП 90 градусов"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDES_MIME = "application/vnd.google-apps.presentation"


def ready():
    return TOKEN.exists()


def login():
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    TOKEN.chmod(0o600)


def service():
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def folder(svc):
    """Папка «КП 90 градусов» в корне Drive владельца; создаётся при первом КП."""
    st = _state()
    if st.get("folder_id"):
        try:
            f = svc.files().get(fileId=st["folder_id"], fields="id,trashed").execute()
            if not f.get("trashed"):
                return st["folder_id"]
        except Exception:
            pass
    f = svc.files().create(body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
                           fields="id").execute()
    st["folder_id"] = f["id"]
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return f["id"]


def share(email):
    """Открыть папку с КП сотруднику (редактор), без письма-уведомления."""
    svc = service()
    fid = folder(svc)
    svc.permissions().create(fileId=fid, body={"type": "user", "role": "writer", "emailAddress": email},
                             sendNotificationEmail=False).execute()
    st = _state()
    st.setdefault("shared_with", [])
    if email not in st["shared_with"]:
        st["shared_with"].append(email)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def upload(pptx: Path):
    """PPTX -> Google Презентация в папке КП. -> (file_id, ссылка)."""
    svc = service()
    body = {"name": pptx.stem, "mimeType": SLIDES_MIME, "parents": [folder(svc)]}
    media = MediaFileUpload(str(pptx), mimetype=PPTX_MIME, resumable=True)
    f = svc.files().create(body=body, media_body=media, fields="id,webViewLink").execute()
    return f["id"], f["webViewLink"]


def export_pdf(file_id: str, dest: Path) -> Path:
    """PDF из Google Презентации — шрифты Google внутри, смотрится одинаково везде."""
    req = service().files().export_media(fileId=file_id, mimeType="application/pdf")
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    dest.write_bytes(buf.getvalue())
    return dest


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login()
        print("Вход выполнен, токен сохранён:", TOKEN)
    elif cmd == "share":
        share(sys.argv[2])
        print("Доступ к папке открыт:", sys.argv[2])
    elif cmd == "test":
        p = Path(sys.argv[2])
        fid, link = upload(p)
        print("Ссылка:", link)
        print("PDF:", export_pdf(fid, p.with_suffix(".gslides.pdf")))
    else:
        print(__doc__)
