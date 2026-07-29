import asyncio
import base64
import html
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import List, Optional

from absl import flags
from fastapi import FastAPI, File, Form, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from components.canvas_state_service import CanvasStateService
from deployer.database import DatabaseManager
from deployer.deployer import LocalDeployer, SessionMetadata, extract_asset_package
from deployer.session_manager import SessionManager
from utils.config_loader import get_config
from utils.email_service import send_password_reset_email
from utils.session_paths import ensure_sessions_root

flags.DEFINE_boolean(
    "allow_mock_payments",
    False,
    "Whether to allow mock/simulated credit purchases when live gateway key is unconfigured."
)

flags.DEFINE_boolean(
    "testing_use_local_database",
    False,
    "If true, use the local SQLite database for authentication and deployments."
)

FLAGS = flags.FLAGS
sys.argv = FLAGS(sys.argv, known_only=True)

logger = logging.getLogger(__name__)


def _format_about_inline(text: str) -> str:
    """Render the small, safe Markdown subset used by ABOUT.md."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)

    def link(match: re.Match) -> str:
        label, url = match.groups()
        if re.match(r"^(https?://|mailto:)", url):
            return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
        return label

    return re.sub(r"\[([^]]+)\]\(([^)]+)\)", link, escaped)


def render_about_markdown(markdown_source: str) -> str:
    """Convert the headings, lists, and paragraphs in ABOUT.md to page markup."""
    blocks: List[str] = []
    list_items: List[str] = []
    list_tag: Optional[str] = None
    paragraph: List[str] = []

    def flush_list() -> None:
        nonlocal list_items, list_tag
        if list_items and list_tag:
            blocks.append(f"<{list_tag}>" + "".join(list_items) + f"</{list_tag}>")
        list_items = []
        list_tag = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(f"<p>{_format_about_inline(' '.join(paragraph))}</p>")
        paragraph = []

    for raw_line in markdown_source.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        unordered_item = re.match(r"^[-*]\s+(.+)$", line)
        ordered_item = re.match(r"^\d+\.\s+(.+)$", line)

        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_format_about_inline(heading.group(2))}</h{level}>")
        elif unordered_item or ordered_item:
            flush_paragraph()
            item_tag = "ul" if unordered_item else "ol"
            if list_tag and list_tag != item_tag:
                flush_list()
            list_tag = item_tag
            item_text = (unordered_item or ordered_item).group(1)
            list_items.append(f"<li>{_format_about_inline(item_text)}</li>")
        elif line == "":
            flush_paragraph()
            flush_list()
        elif line in {"---", "***", "___"}:
            flush_paragraph()
            flush_list()
            blocks.append("<hr>")
        else:
            paragraph.append(line)

    flush_paragraph()
    flush_list()
    return "\n".join(blocks)

config = get_config()

app = FastAPI()

# Deployer, Database, and Session Manager instances
local_deployer = LocalDeployer()
session_manager = SessionManager(deployer=local_deployer)

if FLAGS.testing_use_local_database:
    db = DatabaseManager.from_local("deployer.db")
else:
    db = DatabaseManager.from_live()

# Sessions folder (absolute path resolution)
sessions_folder = str(ensure_sessions_root())

# Playlists folder from config (absolute path resolution)
playlists_folder = str((Path(__file__).parent / config.get("music", {}).get("playlists_folder", "playlists")).resolve())
os.makedirs(playlists_folder, exist_ok=True)
app.mount("/playlists", StaticFiles(directory=playlists_folder), name="playlists")

# Reference library folder (absolute path resolution)
ref_library_folder = str((Path(__file__).parent / "reference_library").resolve())
os.makedirs(ref_library_folder, exist_ok=True)
app.mount("/reference_library", StaticFiles(directory=ref_library_folder), name="reference_library")

# Artwork used by the public join-page background carousel.
carousel_folder = str((Path(__file__).parent / "templates" / "carousel").resolve())
app.mount("/carousel", StaticFiles(directory=carousel_folder), name="carousel")

class ChatMessage(BaseModel):
    author: str
    text: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    username_or_email: str
    password: str

class ForgotPasswordRequest(BaseModel):
    username_or_email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class ResolveJoinKeyRequest(BaseModel):
    join_key: str

class BuyCreditsRequest(BaseModel):
    package_id: Optional[str] = None
    custom_credits: Optional[float] = None
    custom_usd: Optional[float] = None
    payment_method: Optional[str] = "card_mock"
    card_number: Optional[str] = None
    card_exp: Optional[str] = None
    card_cvc: Optional[str] = None
    card_name: Optional[str] = None

class AddAllowedOratorRequest(BaseModel):
    target_user_id: int

class RequestBatonRequest(BaseModel):
    target_user_id: int
    timeout_seconds: Optional[int] = 30


canvas_states = CanvasStateService(local_deployer)


_SAFE_PARAM_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.() \-]*$')

def _safe_path_param(value: str, label: str = "parameter") -> str:
    """Reject path components that could enable directory traversal."""
    if not value or not _SAFE_PARAM_RE.match(value) or '..' in value:
        raise HTTPException(status_code=400, detail=f"Invalid {label}.")
    return value

def get_current_user(request: Request | WebSocket, *, record_activity: bool = True) -> Optional[dict]:
    token = None
    if hasattr(request, "cookies") and request.cookies:
        token = request.cookies.get("auth_token")

    if not token and hasattr(request, "query_params") and request.query_params:
        token = request.query_params.get("auth_token") or request.query_params.get("token")

    if not token and hasattr(request, "scope"):
        headers = getattr(request, "scope", {}).get("headers", [])
        for key, value in headers:
            if key.lower() == b"cookie":
                try:
                    for part in value.decode("utf-8").split(";"):
                        name, _, cookie_value = part.partition("=")
                        if name.strip() == "auth_token":
                            token = cookie_value.strip()
                            break
                except Exception:
                    pass
                if token is not None:
                    break

    if token:
        user = db.validate_session_token(token, record_activity=record_activity)
        if user:
            return user

    if hasattr(request, "query_params") and request.query_params:
        uid_str = request.query_params.get("user_id") or request.query_params.get("user")
        if uid_str and uid_str.isdigit():
            user = db.get_user_by_id(int(uid_str))
            if user:
                return user

    return None



_CANVAS_ACCESS_COOKIE = "canvas_access"


def _canvas_access_grants(request: Request | WebSocket) -> dict[str, str]:
    """Return validly-shaped per-session join-key grants from the HttpOnly cookie."""
    if hasattr(request, "cookies"):
        encoded_grants = request.cookies.get(_CANVAS_ACCESS_COOKIE)
    else:
        encoded_grants = None
        for key, value in getattr(request, "scope", {}).get("headers", []):
            if key.lower() == b"cookie":
                for part in value.decode("utf-8").split(";"):
                    name, _, cookie_value = part.partition("=")
                    if name.strip() == _CANVAS_ACCESS_COOKIE:
                        encoded_grants = cookie_value.strip()
                        break
                if encoded_grants is not None:
                    break

    if not encoded_grants:
        return {}
    try:
        padding = "=" * (-len(encoded_grants) % 4)
        grants = json.loads(base64.urlsafe_b64decode(encoded_grants + padding).decode("utf-8"))
        return {
            session_id: join_key
            for session_id, join_key in grants.items()
            if isinstance(session_id, str) and isinstance(join_key, str)
        }
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _grant_canvas_access(response: Response, request: Request, session_id: str, join_key: str) -> None:
    """Store a verified join key outside the URL for subsequent canvas requests."""
    grants = _canvas_access_grants(request)
    grants[session_id] = join_key
    encoded_grants = base64.urlsafe_b64encode(
        json.dumps(grants, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    response.set_cookie(
        key=_CANVAS_ACCESS_COOKIE,
        value=encoded_grants,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )


def _valid_join_key(expected_key: str, candidate_key: Optional[str]) -> bool:
    return bool(candidate_key) and hmac.compare_digest(
        expected_key.strip().upper(), candidate_key.strip().upper()
    )


def can_access_agent_websocket(
    request: Request | WebSocket,
    deployment: dict,
    *,
    current_user: Optional[dict] = None,
    join_key: Optional[str] = None,
) -> bool:
    """Return True when the request can access the session's agent websocket."""
    if not deployment:
        return False

    active_orator_id = deployment.get("active_orator_id") or deployment["user_id"]

    if current_user and current_user["id"] == active_orator_id:
        return True

    candidate_key = join_key or _canvas_access_grants(request).get(deployment["session_id"])
    return _valid_join_key(deployment["join_key"], candidate_key)




def _require_canvas_access(
    request: Request, session_id: str, join_key: Optional[str] = None
) -> dict:
    """Require ownership or a verified join-key grant before serving session content."""
    _safe_path_param(session_id, "session_id")
    deployment = db.get_deployment(session_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Active session not found.")

    current_user = get_current_user(request)
    if can_access_agent_websocket(request, deployment, current_user=current_user, join_key=join_key):
        return deployment

    raise HTTPException(status_code=403, detail="A valid join key is required to access this session.")

# ========================================
# Authentication API Endpoints
# ========================================

@app.post("/api/auth/register")
def register_user(req: RegisterRequest, response: Response):
    try:
        user = db.register_user(req.username, req.email, req.password)
        token = db.create_auth_session(user["id"])
        response.set_cookie(key="auth_token", value=token, httponly=True, max_age=604800)
        return {"status": "ok", "user": user}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/login")
def login_user(req: LoginRequest, response: Response):
    user = db.authenticate_user(req.username_or_email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username/email or password.")
    token = db.create_auth_session(user["id"])
    response.set_cookie(key="auth_token", value=token, httponly=True, max_age=604800)
    return {"status": "ok", "user": user}

@app.post("/api/auth/logout")
def logout_user(request: Request, response: Response):
    token = request.cookies.get("auth_token")
    if token:
        db.invalidate_session_token(token)
    response.delete_cookie("auth_token")
    return {"status": "ok"}

@app.get("/api/auth/me")
def get_auth_me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": user}

@app.post("/api/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest, request: Request):
    token_user = db.create_password_reset_token(req.username_or_email)
    if not token_user:
        return {
            "status": "ok",
            "message": "If an account with that email or username exists, a password reset link has been sent."
        }

    token, user = token_user
    base_url = str(request.base_url).rstrip("/")
    reset_link = f"{base_url}/deploy?reset_token={token}"

    res = send_password_reset_email(user["email"], user["username"], reset_link)

    return {
        "status": "ok",
        "message": "If an account with that email or username exists, a password reset link has been sent.",
    }

@app.get("/api/auth/reset-password/validate")
def validate_reset_token(token: str):
    user = db.validate_password_reset_token(token)
    if not user:
        return {"valid": False, "detail": "Invalid or expired password reset link."}
    return {"valid": True, "username": user["username"]}

@app.post("/api/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    if not req.new_password or not req.new_password.strip():
        raise HTTPException(status_code=400, detail="New password cannot be empty.")
    success = db.reset_password_with_token(req.token, req.new_password)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")
    return {"status": "ok", "message": "Password updated successfully! You can now log in with your new password."}

# ========================================
# Payments & Credit Top-Up API Endpoints
# ========================================

CREDIT_PACKAGES = {
    "starter": {"credits": 50.0, "amount_usd": 5.00, "name": "Starter Pack"},
    "pro": {"credits": 200.0, "amount_usd": 18.00, "name": "Pro Pack"},
    "ultra": {"credits": 500.0, "amount_usd": 40.00, "name": "Ultra Pack"},
}

@app.post("/api/payments/buy-credits")
def buy_credits(req: BuyCreditsRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to purchase credits.")

    if req.package_id and req.package_id in CREDIT_PACKAGES:
        pkg = CREDIT_PACKAGES[req.package_id]
        credits_to_add = pkg["credits"]
        usd_amount = pkg["amount_usd"]
    elif req.custom_credits and req.custom_credits > 0 and req.custom_usd and req.custom_usd > 0:
        credits_to_add = req.custom_credits
        usd_amount = req.custom_usd
    elif req.package_id is None and req.custom_credits is None:
        pkg = CREDIT_PACKAGES["starter"]
        credits_to_add = pkg["credits"]
        usd_amount = pkg["amount_usd"]
    else:
        raise HTTPException(status_code=400, detail="Invalid package or credit amount specified.")

    payment_method = req.payment_method or "card_mock"

    if payment_method.startswith("card"):
        card_num = (req.card_number or "").replace(" ", "").replace("-", "")
        if not card_num:
            raise HTTPException(status_code=400, detail="Credit card number is required.")
        
        if card_num.endswith("0002") or card_num.endswith("0000"):
            raise HTTPException(status_code=400, detail="Your card was declined by the issuer.")
        if card_num.endswith("0051"):
            raise HTTPException(status_code=400, detail="Insufficient funds on credit card.")
        
        if not card_num.isdigit() or len(card_num) < 13 or len(card_num) > 19:
            raise HTTPException(status_code=400, detail="Invalid credit card number format.")
        
        if req.card_exp:
            exp_clean = req.card_exp.replace(" ", "")
            if "/" not in exp_clean or len(exp_clean) != 5:
                raise HTTPException(status_code=400, detail="Invalid card expiration format (must be MM/YY).")
        else:
            raise HTTPException(status_code=400, detail="Card expiration date (MM/YY) is required.")

        cvc_clean = (req.card_cvc or "").strip()
        if not cvc_clean or not cvc_clean.isdigit() or len(cvc_clean) < 3 or len(cvc_clean) > 4:
            raise HTTPException(status_code=400, detail="Invalid CVV / CVC code (must be 3 or 4 digits).")

        stripe_key = os.getenv("STRIPE_SECRET_KEY")
        allow_mock = bool(FLAGS.allow_mock_payments)

        if not stripe_key and not allow_mock:
            raise HTTPException(status_code=503, detail="Payment service unavailable")

    result = db.add_user_credits(user["id"], credits_to_add, usd_amount, payment_method)
    return {"status": "ok", "message": f"Successfully added {credits_to_add:.1f} credits!", **result}

@app.get("/api/payments/history")
def get_payment_history(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to view payment history.")
    transactions = db.get_user_transactions(user["id"])
    return {"status": "ok", "transactions": transactions}

# ========================================
# Session Asset Dynamic Routes
# ========================================

@app.get("/sessions/{session_id}/references/{filename}")
async def serve_session_reference(request: Request, session_id: str, filename: str):
    _require_canvas_access(request, session_id)
    _safe_path_param(session_id, "session_id")
    _safe_path_param(filename, "filename")
    file_path = session_manager.get_session_reference_dir(session_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Session reference file not found")
    return FileResponse(file_path)

@app.get("/sessions/{session_id}/playlists/{playlist_name}/{filename}")
async def serve_session_playlist_track(request: Request, session_id: str, playlist_name: str, filename: str):
    _require_canvas_access(request, session_id)
    _safe_path_param(session_id, "session_id")
    _safe_path_param(playlist_name, "playlist_name")
    _safe_path_param(filename, "filename")
    file_path = session_manager.get_session_playlists_dir(session_id) / playlist_name / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Session playlist track not found")
    return FileResponse(file_path)

@app.get("/sessions/{session_id}/output/{filename}")
async def serve_session_output(request: Request, session_id: str, filename: str):
    _require_canvas_access(request, session_id)
    _safe_path_param(session_id, "session_id")
    _safe_path_param(filename, "filename")
    file_path = session_manager.get_session_output_dir(session_id) / filename
    if not file_path.exists():
        # Check subdirectories of output directory (e.g. output/images/filename)
        sub_path = session_manager.get_session_output_dir(session_id) / "images" / filename
        if sub_path.exists():
            file_path = sub_path
        else:
            found = list(session_manager.get_session_output_dir(session_id).rglob(filename))
            if found:
                file_path = found[0]
            else:
                raise HTTPException(status_code=404, detail="Session output file not found")
    return FileResponse(file_path)

# ========================================
# Deployer & Session API Endpoints
# ========================================

@app.post("/api/sessions/resolve-join-key")
def resolve_join_key(req: ResolveJoinKeyRequest, request: Request, response: Response):
    dep = db.get_session_by_join_key(req.join_key)
    if not dep:
        raise HTTPException(status_code=404, detail="Invalid Join Key. No matching active session found.")

    meta = local_deployer.get_session(dep["session_id"])
    if not meta:
        db_meta = db.get_session_metadata_from_db(dep["session_id"])
        if not db_meta:
            raise HTTPException(status_code=404, detail="Session files no longer exist.")
        meta = SessionMetadata(**db_meta)

    _grant_canvas_access(response, request, meta.session_id, dep["join_key"])
    return {"status": "ok", "session_id": meta.session_id, "name": meta.name, "user_id": dep.get("user_id")}

@app.get("/api/sessions")
def list_sessions(request: Request):
    """List all deployed sessions from disk and database without eagerly reconstructing files."""
    current_user = get_current_user(request)
    current_user_id = current_user["id"] if current_user else None

    # Get sessions currently on disk
    disk_sessions = local_deployer.list_sessions()
    all_sessions_dict = {s.session_id: s.model_dump() for s in disk_sessions}

    # Add DB sessions that are not on disk yet (without writing files to disk!)
    for sid in db.get_all_exported_session_ids():
        if sid not in all_sessions_dict:
            db_meta = db.get_session_metadata_from_db(sid)
            if db_meta:
                all_sessions_dict[sid] = db_meta

    result = []
    for sid, s_dict in all_sessions_dict.items():
        dep = db.get_deployment(sid)
        owner_id = dep["user_id"] if dep else None
        is_owner = (current_user_id is not None and owner_id == current_user_id)

        s_dict["is_owner"] = is_owner
        
        # Hide join_key if not owner
        if not is_owner:
            s_dict["join_key"] = "🔒 Owner Only"
            
        result.append(s_dict)

    # Sort: owned sessions first, then by created_at desc
    result.sort(key=lambda x: (not x["is_owner"], x.get("created_at", "")), reverse=False)
    return result

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Retrieve metadata and mounted assets for a specific session."""
    _require_canvas_access(request, session_id)
    session_dir = local_deployer._get_session_dir(session_id)
    if not session_dir.exists() or not (session_dir / "session.json").exists():
        db.reconstruct_session_from_db(session_id, session_dir)

    meta = local_deployer.get_session(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")
    
    current_user = get_current_user(request, record_activity=False)
    dep = db.get_deployment(session_id)
    owner_id = dep["user_id"] if dep else None
    is_owner = (current_user is not None and owner_id == current_user["id"])

    # Analytics must not hold up the canvas reload, especially with a remote DB.
    client_ip = request.client.host if request.client else None
    asyncio.create_task(
        db.record_session_view_async(
            session_id,
            current_user["id"] if current_user else None,
            client_ip,
        )
    )

    meta_dict = meta.model_dump()
    meta_dict["is_owner"] = is_owner
    if not is_owner:
        meta_dict["join_key"] = "🔒 Owner Only"

    return {
        "metadata": meta_dict,
        "references": session_manager.get_session_references(session_id),
        "playlists": session_manager.get_session_playlists(session_id),
    }

@app.post("/api/sessions/create-and-deploy")
async def create_and_deploy_session(request: Request):
    """API endpoint to handle multi-file asset upload and deploy a session canvas instance."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to deploy sessions.")

    if user["credits"] < 5.0:
        raise HTTPException(status_code=402, detail="Insufficient credits (5.0 credits required).")

    form = await request.form()
    name = str(form.get("name", "Narratron Session"))
    style = str(form.get("style", "")).strip()

    reference_files = []
    playlists_data = {}

    for key, value in form.multi_items():
        filename = getattr(value, "filename", None)
        if filename:
            content = await value.read()
            if content:
                # Check for uploaded ZIP package
                if key in ("asset_zip", "asset_package") or filename.lower().endswith(".zip"):
                    try:
                        zip_refs, zip_playlists, zip_style = extract_asset_package(content)
                    except ValueError as ve:
                        raise HTTPException(status_code=400, detail=str(ve))
                    reference_files.extend(zip_refs)
                    for pl_name, tracks in zip_playlists.items():
                        if pl_name not in playlists_data:
                            playlists_data[pl_name] = []
                        playlists_data[pl_name].extend(tracks)
                    if zip_style and not style:
                        style = zip_style
                elif key in ("asset_folder_files", "asset_files"):
                    # Folder upload with relative path info
                    rel_path = filename.replace("\\", "/")
                    parts = [p for p in rel_path.split("/") if p]
                    clean_name = parts[-1] if parts else filename

                    if "references" in parts or (
                        clean_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
                        and "playlists" not in parts
                    ):
                        reference_files.append((clean_name, content))
                    elif "playlists" in parts:
                        idx = parts.index("playlists")
                        pl_name = parts[idx + 1] if idx + 1 < len(parts) - 1 else "default"
                        if clean_name.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")):
                            if pl_name not in playlists_data:
                                playlists_data[pl_name] = []
                            playlists_data[pl_name].append((clean_name, content))
                    elif clean_name.lower() == "style.txt" and not style:
                        try:
                            style = content.decode("utf-8").strip()
                        except Exception:
                            pass
                elif key == "reference_files":
                    reference_files.append((filename, content))
                elif key.startswith("playlist_"):
                    pl_name = key[len("playlist_"):]
                    if pl_name not in playlists_data:
                        playlists_data[pl_name] = []
                    playlists_data[pl_name].append((filename, content))

    metadata = local_deployer.create_session(
        name=name,
        reference_files=reference_files,
        playlists_data=playlists_data,
        style=style or None,
    )
    deployed_meta = local_deployer.deploy_session(metadata.session_id)

    # Record deployment & deduct credits
    db.record_deployment(deployed_meta.session_id, user["id"], deployed_meta.join_key, cost=5.0)

    res_dict = deployed_meta.model_dump()
    res_dict["is_owner"] = True
    asyncio.create_task(
        db.persist_canvas_session_async(
            canvas_states,
            local_deployer,
            deployed_meta.session_id,
            user["id"],
            deployed_meta.name,
        )
    )
    return {"status": "ok", "session_id": deployed_meta.session_id, "session": res_dict}

@app.post("/api/sessions/{session_id}/deploy")
def deploy_existing_session(session_id: str, request: Request):
    """Deploy an existing created session."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    session_dir = local_deployer._get_session_dir(session_id)
    if not session_dir.exists() or not (session_dir / "session.json").exists():
        db.reconstruct_session_from_db(session_id, session_dir)
    
    dep = db.get_deployment(session_id)
    if dep and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the session owner can deploy this session.")

    # Stop any other currently deployed sessions
    existing_sessions = local_deployer.list_sessions()
    for s in existing_sessions:
        if s.session_id != session_id and s.status == "deployed":
            try:
                local_deployer.stop_session(s.session_id)
            except Exception:
                pass

    meta = local_deployer.deploy_session(session_id)
    return {"status": "ok", "session": meta}

@app.delete("/api/sessions/{session_id}")
def destroy_session(session_id: str, request: Request):
    """Remove and clean up a local session instance. Requires owner login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to delete sessions.")

    dep = db.get_deployment(session_id)
    if dep and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Permission denied. Only the session owner can delete this session.")

    success = local_deployer.destroy_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or could not be removed")
    db.delete_deployment(session_id)
    return {"status": "ok", "session_id": session_id}

# ========================================
# Canvas & WebSocket Endpoints
# ========================================

@app.websocket("/ws/doodle")
async def websocket_endpoint(websocket: WebSocket, session_id: Optional[str] = None):
    if session_id:
        try:
            _require_canvas_access(websocket, session_id)
        except HTTPException:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    websocket.state.session_id = session_id
    current_user = get_current_user(websocket)
    cs = await canvas_states.connect_doodle_websocket(websocket, session_id, user=current_user)
    
    if session_id:
        baton_st = db.get_session_baton_state(session_id)
        if baton_st:
            await canvas_states.broadcast_baton_update(session_id, baton_st)

    try:
        while True:
            data = await websocket.receive_json()
            await canvas_states.apply_doodle_message(cs, data, sender=websocket)
    except WebSocketDisconnect:
        cs.unregister_websocket(websocket)
        if session_id:
            baton_st = db.get_session_baton_state(session_id)
            if baton_st:
                await canvas_states.broadcast_baton_update(session_id, baton_st)


# ========================================
# Baton Passing API Endpoints
# ========================================

@app.get("/api/sessions/{session_id}/baton")
async def get_session_baton_state(session_id: str, request: Request):
    _require_canvas_access(request, session_id)
    state = db.get_session_baton_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session baton state not found.")
    
    cs = canvas_states.get(session_id)
    state["active_viewers"] = cs.get_active_viewers()
    return state


@app.post("/api/sessions/{session_id}/baton/allowed_orators")
async def add_allowed_orator(session_id: str, req: AddAllowedOratorRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.add_allowed_orator(session_id, owner_id=user["id"], target_user_id=req.target_user_id)
        await canvas_states.broadcast_baton_update(session_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/sessions/{session_id}/baton/allowed_orators/{target_user_id}")
async def remove_allowed_orator(session_id: str, target_user_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.remove_allowed_orator(session_id, owner_id=user["id"], target_user_id=target_user_id)
        await canvas_states.broadcast_baton_update(session_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sessions/{session_id}/baton/request")
async def request_baton_pass(session_id: str, req: RequestBatonRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.request_baton(
            session_id,
            owner_id=user["id"],
            target_user_id=req.target_user_id,
            timeout_seconds=req.timeout_seconds or 30
        )
        await canvas_states.broadcast_baton_update(session_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sessions/{session_id}/baton/accept")
async def accept_baton_pass(session_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.accept_baton(session_id, target_user_id=user["id"])
        await canvas_states.broadcast_baton_update(session_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sessions/{session_id}/baton/decline")
async def decline_baton_pass(session_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.decline_baton(session_id, target_user_id=user["id"])
        await canvas_states.broadcast_baton_update(session_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sessions/{session_id}/baton/takeback")
async def take_back_baton(session_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.take_back_baton(session_id, owner_id=user["id"])
        await canvas_states.broadcast_baton_update(session_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sessions/{session_id}/save", status_code=202)
async def save_session_to_db(session_id: str, request: Request):
    """Save canvas session state and image assets to SQLite database on user demand."""
    meta = local_deployer.get_session(session_id)
    dep = db.get_deployment(session_id)
    user_id = dep["user_id"] if dep else None
    name = meta.name if meta else session_id

    asyncio.create_task(
        db.persist_canvas_session_async(
            canvas_states,
            local_deployer,
            session_id,
            user_id,
            name,
        )
    )
    return {"status": "queued", "session_id": session_id}

@app.get("/api/sessions/{session_id}/export-assets")
def export_session_assets(session_id: str, request: Request):
    """Package and export all session assets into a ZIP file."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    session_dir = local_deployer._get_session_dir(session_id)
    if not session_dir.exists():
        db.reconstruct_session_from_db(session_id, session_dir)

    # Ensure current displayed image is saved into the session directory
    cs = canvas_states.get(session_id)
    cs.export_session_data(session_dir=session_dir)

    import io
    import zipfile
    from fastapi.responses import Response

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        if session_dir.exists():
            for file_path in session_dir.rglob("*"):
                if file_path.is_file():
                    arc_name = file_path.relative_to(session_dir)
                    zip_file.write(file_path, arcname=str(arc_name).replace("\\", "/"))

    zip_buffer.seek(0)
    headers = {
        "Content-Disposition": f'attachment; filename="{session_id}_assets.zip"'
    }
    return Response(content=zip_buffer.getvalue(), media_type="application/zip", headers=headers)

@app.api_route("/api/orator/toggle_mic", methods=["GET", "POST"])
async def trigger_orator_mic_toggle(request: Request, session_id: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to control Orator microphone.")
    
    if session_id:
        dep = db.get_deployment(session_id)
        if dep and dep["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Permission denied. Only the session owner can control the Orator microphone.")

    count = await canvas_states.toggle_microphone(session_id)
    return {"status": "ok", "broadcasted_to": count}


@app.get("/api/orator/config")
def get_orator_config():
    return config.get("orator", {
        "hotkey": "<ctrl>+<shift>+[",
        "server_url": "http://127.0.0.1:8000/api/orator/toggle_mic"
    })

@app.get("/api/latest")
def get_latest_image(request: Request, session_id: Optional[str] = None):
    if session_id:
        _require_canvas_access(request, session_id)
        session_dir = local_deployer._get_session_dir(session_id)
        if not session_dir.exists():
            db.reconstruct_session_from_db(session_id, session_dir)
    return canvas_states.latest_state(session_id)

@app.get("/api/chat")
def get_chat(request: Request, session_id: Optional[str] = None):
    if session_id:
        _require_canvas_access(request, session_id)
    return canvas_states.chat_messages(session_id)

@app.post("/api/chat")
def post_chat(msg: ChatMessage, request: Request, session_id: Optional[str] = None):
    if session_id:
        _require_canvas_access(request, session_id)
    canvas_states.add_chat_message(msg.text, author=msg.author, session_id=session_id)
    return {"status": "ok"}

@app.get("/api/stats")
def get_stats_api():
    """Retrieve system stats summary (accounts, 7-day active users, session views)."""
    return db.get_stats_summary()

# ========================================
# Application Root Pages & Navigation
# ========================================

@app.get("/favicon.png", include_in_schema=False)
def read_favicon():
    """Serve the shared browser-tab icon."""
    return FileResponse(
        Path(__file__).parent / "templates" / "narratron favicon.png",
        media_type="image/png",
    )

@app.get("/", response_class=HTMLResponse)
@app.get("/join", response_class=HTMLResponse)
def read_join_splash():
    """Serve the public Join Splash Page."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "join_splash.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/deploy", response_class=HTMLResponse)
def read_deployer():
    """Serve the Session Creation & App Deployer Dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "session_creation.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/about", response_class=HTMLResponse)
def read_about():
    """Serve the About page from the repository's ABOUT.md source."""
    project_root = Path(__file__).parent
    about_content = render_about_markdown(
        (project_root / "ABOUT.md").read_text(encoding="utf-8")
    )
    template_path = project_root / "templates" / "about.html"
    return template_path.read_text(encoding="utf-8").replace(
        "<!-- ABOUT_CONTENT -->", about_content
    )

@app.get("/stats", response_class=HTMLResponse)
def read_stats():
    """Serve the System Stats Dashboard Page."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "stats.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/popout", response_class=HTMLResponse)
def read_popout(request: Request, session_id: Optional[str] = None, join_key: Optional[str] = None):
    """Serve the standalone Pop-out Panel interface for a session."""
    if session_id:
        deployment = _require_canvas_access(request, session_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, session_id, join_key)
            return response
    template_path = os.path.join(os.path.dirname(__file__), "templates", "popout.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/obs", response_class=HTMLResponse)
@app.get("/obs/{session_id}", response_class=HTMLResponse)
async def read_obs_canvas(
    request: Request,
    session_id: Optional[str] = None,
):
    """Serve the dedicated, UI-free Canvas interface specifically for OBS Browser Source."""
    if session_id:
        join_key = request.query_params.get("join_key")
        deployment = _require_canvas_access(request, session_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, session_id, join_key)
            return response
        session_dir = local_deployer._get_session_dir(session_id)
        if not session_dir.exists():
            db.reconstruct_session_from_db(session_id, session_dir)
        artifacts_dir = session_dir / "output" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        current_user = get_current_user(request, record_activity=False)
        client_ip = request.client.host if request.client else None
        asyncio.create_task(
            db.record_session_view_async(
                session_id,
                current_user["id"] if current_user else None,
                client_ip,
            )
        )

    template_path = os.path.join(os.path.dirname(__file__), "templates", "obs.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/canvas", response_class=HTMLResponse)
async def read_canvas(
    request: Request,
    session_id: Optional[str] = None,
    join_key: Optional[str] = None,
):
    """Serve the Canvas interface for a specific session."""
    if session_id:
        deployment = _require_canvas_access(request, session_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, session_id, join_key)
            return response

        session_dir = local_deployer._get_session_dir(session_id)
        if not session_dir.exists():
            db.reconstruct_session_from_db(session_id, session_dir)
        artifacts_dir = session_dir / "output" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Analytics must not delay the initial canvas render.
        current_user = get_current_user(request, record_activity=False)
        client_ip = request.client.host if request.client else None
        asyncio.create_task(
            db.record_session_view_async(
                session_id,
                current_user["id"] if current_user else None,
                client_ip,
            )
        )

    is_obs = request.query_params.get("obs") == "1" or request.query_params.get("obs") == "true"
    template_name = "obs.html" if is_obs else "index.html"
    template_path = os.path.join(os.path.dirname(__file__), "templates", template_name)
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()
