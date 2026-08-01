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
import uuid
import yaml
import stripe

from absl import flags
from fastapi import FastAPI, File, Form, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from components.canvas_state_service import CanvasStateService
from storage.database import DatabaseManager
from pricing.pricing_controller import PricingController
from components.theater_manager import TheaterManager, TheaterMetadata, ensure_theaters_root, extract_asset_package
from utils.config_loader import get_app_config, get_theater_config, save_theater_config, get_theater_default_config
from utils.email_service import send_password_reset_email

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

config = get_app_config()

app = FastAPI()

# Theater workspace and database instances
theater_manager = TheaterManager()

if FLAGS.testing_use_local_database:
    db = DatabaseManager.from_local("deployer.db")
else:
    db = DatabaseManager.from_live()


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
    payment_method: Optional[str] = "card_mock"
    card_number: Optional[str] = None
    card_exp: Optional[str] = None
    card_cvc: Optional[str] = None
    card_name: Optional[str] = None
    checkout_mode: Optional[bool] = False

class AddAllowedOratorRequest(BaseModel):
    target_user_id: int

class RequestBatonRequest(BaseModel):
    target_user_id: int
    timeout_seconds: Optional[int] = 30

class SaveTheaterConfigRequest(BaseModel):
    config_yaml: str

class MicSensitivityRequest(BaseModel):
    mic_sensitivity: float


canvas_states = CanvasStateService(theater_manager)


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
            theater_id: join_key
            for theater_id, join_key in grants.items()
            if isinstance(theater_id, str) and isinstance(join_key, str)
        }
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _grant_canvas_access(response: Response, request: Request, theater_id: str, join_key: str) -> None:
    """Store a verified join key outside the URL for subsequent canvas requests."""
    grants = _canvas_access_grants(request)
    grants[theater_id] = join_key
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
    """Return True when the request can access the theater's agent websocket."""
    if not deployment:
        return False

    active_orator_id = deployment.get("active_orator_id")
    if current_user:
        if active_orator_id is not None:
            if current_user["id"] == active_orator_id:
                return True
        elif current_user["id"] == deployment["user_id"]:
            return True

    candidate_key = join_key or _canvas_access_grants(request).get(deployment["theater_id"])
    return _valid_join_key(deployment["join_key"], candidate_key)




def _require_canvas_access(
    request: Request, theater_id: str, join_key: Optional[str] = None
) -> dict:
    """Require ownership or a verified join-key grant before serving theater content."""
    _safe_path_param(theater_id, "theater_id")
    deployment = db.get_deployment(theater_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Active theater not found.")

    current_user = get_current_user(request)
    if can_access_agent_websocket(request, deployment, current_user=current_user, join_key=join_key):
        return deployment

    raise HTTPException(status_code=403, detail="A valid join key is required to access this theater.")

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

@app.post("/api/auth/mic-sensitivity")
def update_mic_sensitivity_endpoint(req: MicSensitivityRequest, request: Request):
    if req.mic_sensitivity < 0.0 or req.mic_sensitivity > 1.0:
        raise HTTPException(status_code=400, detail="mic_sensitivity must be between 0.0 and 1.0.")
    user = get_current_user(request)
    if user:
        db.update_user_mic_sensitivity(user["id"], req.mic_sensitivity)
        return {"status": "ok", "authenticated": True, "mic_sensitivity": req.mic_sensitivity}
    return {"status": "ok", "authenticated": False, "mic_sensitivity": req.mic_sensitivity}

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
    "starter": {"credits": 100.0, "amount_usd": 5.00, "name": "Starter Pack", "price_id" : "price_1TzbWlRjBSgVFVM6b6ByPtVn"},
    "pro": {"credits": 400.0, "amount_usd": 18.00, "name": "Pro Pack", "price_id" : "price_1TzbisRjBSgVFVM6Jmyk0IcL"},
    "ultra": {"credits": 1000.0, "amount_usd": 40.00, "name": "Ultra Pack", "price_id" : "price_1TzbjORjBSgVFVM6cWJZy8xb"},
}

def _is_mock_payment_mode(payment_method: Optional[str] = None) -> bool:
    """Return whether an explicitly enabled test environment may simulate payments."""
    if getattr(FLAGS, "allow_mock_payments", False):
        return True
    if getattr(FLAGS, "testing_use_local_database", False):
        return True
    return False


@app.post("/api/payments/buy-credits")
def buy_credits(req: BuyCreditsRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to purchase credits.")

    if req.package_id and req.package_id in CREDIT_PACKAGES:
        pkg = CREDIT_PACKAGES[req.package_id]
        credits_to_add = pkg["credits"]
        usd_amount = pkg["amount_usd"]
    elif req.package_id is None and req.custom_credits is None:
        pkg = CREDIT_PACKAGES["starter"]
        credits_to_add = pkg["credits"]
        usd_amount = pkg["amount_usd"]
    else:
        raise HTTPException(status_code=400, detail="Invalid package or credit amount specified.")

    payment_method = req.payment_method or "stripe_checkout"

    # Automated Mock Flow (for unit tests, mock flags, or unconfigured gateway)
    if _is_mock_payment_mode(payment_method):
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
            allow_mock = bool(FLAGS.allow_mock_payments) or bool(FLAGS.testing_use_local_database)

            if not stripe_key and not allow_mock:
                raise HTTPException(status_code=503, detail="Payment service unavailable")

        result = db.add_user_credits(user["id"], credits_to_add, usd_amount, payment_method)
        return {"status": "ok", "message": f"Successfully added {credits_to_add:.1f} credits!", **result}

    # Live purchases must use Stripe-hosted Checkout. Card numbers and CVCs
    # must never be sent through this application server.
    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Payment service unavailable")

    if not stripe:
        raise HTTPException(status_code=503, detail="Stripe library is not installed on the server.")

    stripe.api_key = stripe_key
    base_url = str(request.base_url).rstrip("/")

    try:
        session = stripe.checkout.Session.create(
                line_items=[{
                    "price": pkg["price_id"],
                    "quantity": 1,
                }],
                mode="payment",
                metadata={
                    "user_id": str(user["id"]),
                    "credits_to_add": str(credits_to_add),
                    "usd_amount": str(usd_amount),
                    "package_id": req.package_id or "custom",
                },
                success_url=f"{base_url}/deploy?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{base_url}/deploy?payment=cancelled",
        )
        return {
            "status": "ok", "mode": "stripe_checkout", "checkout_url": session.url,
            "session_id": session.id, "credits_added": credits_to_add,
            "message": "Stripe Checkout session created successfully."
        }
    except Exception as err:
        logger.error("Stripe Checkout Error: %s", err)
        raise HTTPException(status_code=400, detail="Unable to create Stripe Checkout session.")


@app.get("/api/payments/verify-session")
def verify_stripe_session(session_id: str, request: Request):
    """Verify completed Stripe Checkout session and credit user account."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if _is_mock_payment_mode():
        return {"status": "ok", "verified": True, "credits_added": 0.0, "user": user}

    if not stripe:
        raise HTTPException(status_code=500, detail="Stripe library not installed.")

    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Stripe key missing.")

    stripe.api_key = stripe_key
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status == "paid":
            meta = session.metadata or {}
            meta_user_id = int(meta.get("user_id", 0))
            credits_to_add = float(meta.get("credits_to_add", 0.0))
            usd_amount = float(meta.get("usd_amount", 0.0))

            if meta_user_id == user["id"] and credits_to_add > 0:
                result = db.add_stripe_session_credits(
                    user["id"], credits_to_add, usd_amount, session.id, "stripe_checkout"
                )
                return {"status": "ok", "verified": True, "credits_added": credits_to_add, **result}
            return {"status": "ok", "verified": True, "user": user}
        return {"status": "pending", "verified": False, "detail": "Payment not completed."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to verify Stripe session: {e}")


@app.post("/api/payments/webhook")
async def stripe_webhook(request: Request):
    """Stripe Webhook listener for asynchronous payment settlement."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    event = None
    if stripe and webhook_secret and sig_header:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook signature error: {e}")
    else:
        if not _is_mock_payment_mode():
            raise HTTPException(status_code=503, detail="Stripe webhook signing is not configured.")
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid webhook JSON payload.")

    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
    data_obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else getattr(getattr(event, "data", None), "object", {})

    if event_type == "checkout.session.completed":
        metadata = data_obj.get("metadata", {})
        user_id = metadata.get("user_id")
        credits_to_add = metadata.get("credits_to_add")
        usd_amount = metadata.get("usd_amount")

        session_id = data_obj.get("id") if isinstance(data_obj, dict) else getattr(data_obj, "id", None)
        if user_id and credits_to_add and session_id:
            db.add_stripe_session_credits(
                int(user_id), float(credits_to_add), float(usd_amount or 0.0), session_id, "stripe_webhook"
            )

    return {"status": "ok"}

@app.get("/api/payments/history")
def get_payment_history(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to view payment history.")
    transactions = db.get_user_transactions(user["id"])
    return {"status": "ok", "transactions": transactions}

@app.get("/api/pricing")
def get_pricing_rates(
    voice_minutes: Optional[float] = None,
    images_created: Optional[int] = None,
    gb_amount: Optional[float] = None,
    days: Optional[float] = 1.0,
    usd_amount: Optional[float] = None,
):
    """Retrieve current pricing rates and optionally calculate costs dynamically from PricingController."""
    if voice_minutes is not None and voice_minutes < 0:
        raise HTTPException(status_code=400, detail="voice_minutes must be non-negative.")
    if images_created is not None and images_created < 0:
        raise HTTPException(status_code=400, detail="images_created must be non-negative.")
    if gb_amount is not None and gb_amount < 0:
        raise HTTPException(status_code=400, detail="gb_amount must be non-negative.")
    if days is not None and days < 0:
        raise HTTPException(status_code=400, detail="days must be non-negative.")
    if usd_amount is not None and usd_amount < 0:
        raise HTTPException(status_code=400, detail="usd_amount must be non-negative.")

    pricing = getattr(db, "pricing_controller", None) or PricingController.from_env()
    rates = pricing.get_rates()

    calculation = {}
    if voice_minutes is not None or images_created is not None:
        vm = voice_minutes if voice_minutes is not None else 0.0
        ic = images_created if images_created is not None else 0
        calculation["usage_credits"] = pricing.calculate_usage_cost(voice_minutes=vm, images_created=ic)

    if gb_amount is not None:
        d = days if days is not None else 1.0
        calculation["storage_credits"] = pricing.calculate_storage_cost(gb_amount=gb_amount, days=d)

    if usd_amount is not None:
        calculation["usd_credits"] = pricing.credits_for_usd(usd_amount)

    if calculation:
        rates["calculation"] = calculation

    return rates

# ========================================
# Theater Asset Dynamic Routes
# ========================================

@app.get("/theaters/{theater_id}/references/{filename}")
async def serve_theater_reference(request: Request, theater_id: str, filename: str):
    _require_canvas_access(request, theater_id)
    _safe_path_param(theater_id, "theater_id")
    _safe_path_param(filename, "filename")
    file_path = theater_manager.theater(theater_id).references_dir() / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Theater reference file not found")
    return FileResponse(file_path)

@app.get("/theaters/{theater_id}/playlists/{playlist_name}/{filename}")
async def serve_theater_playlist_track(request: Request, theater_id: str, playlist_name: str, filename: str):
    _require_canvas_access(request, theater_id)
    _safe_path_param(theater_id, "theater_id")
    _safe_path_param(playlist_name, "playlist_name")
    _safe_path_param(filename, "filename")
    file_path = theater_manager.theater(theater_id).playlists_dir() / playlist_name / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Theater playlist track not found")
    return FileResponse(file_path)

@app.get("/theaters/{theater_id}/output/{filename}")
async def serve_theater_output(request: Request, theater_id: str, filename: str):
    _require_canvas_access(request, theater_id)
    _safe_path_param(theater_id, "theater_id")
    _safe_path_param(filename, "filename")
    file_path = theater_manager.theater(theater_id).output_dir() / filename
    if not file_path.exists():
        # Check subdirectories of output directory (e.g. output/images/filename)
        sub_path = theater_manager.theater(theater_id).output_dir() / "images" / filename
        if sub_path.exists():
            file_path = sub_path
        else:
            found = list(theater_manager.theater(theater_id).output_dir().rglob(filename))
            if found:
                file_path = found[0]
            else:
                raise HTTPException(status_code=404, detail="Theater output file not found")
    return FileResponse(file_path)

# ========================================
# Deployer & Theater API Endpoints
# ========================================

@app.post("/api/theaters/resolve-join-key")
def resolve_join_key(req: ResolveJoinKeyRequest, request: Request, response: Response):
    dep = db.get_theater_by_join_key(req.join_key)
    if not dep:
        raise HTTPException(status_code=404, detail="Invalid Join Key. No matching active theater found.")

    meta = theater_manager.get_theater(dep["theater_id"])
    if not meta:
        db_meta = db.get_theater_metadata_from_db(dep["theater_id"])
        if not db_meta:
            raise HTTPException(status_code=404, detail="Theater files no longer exist.")
        meta = TheaterMetadata(**db_meta)

    _grant_canvas_access(response, request, meta.theater_id, dep["join_key"])
    return {"status": "ok", "theater_id": meta.theater_id, "name": meta.name, "user_id": dep.get("user_id")}

@app.get("/api/theaters")
def list_theaters(request: Request):
    """List all deployed theaters from disk and database without eagerly reconstructing files."""
    current_user = get_current_user(request)
    current_user_id = current_user["id"] if current_user else None

    # Get theaters currently on disk
    disk_theaters = theater_manager.list_theaters()
    all_theaters_dict = {s.theater_id: s.model_dump() for s in disk_theaters}

    # Add DB theaters that are not on disk yet (without writing files to disk!)
    for sid in db.get_all_exported_theater_ids():
        if sid not in all_theaters_dict:
            db_meta = db.get_theater_metadata_from_db(sid)
            if db_meta:
                all_theaters_dict[sid] = db_meta

    # Fetch last_used timestamps map from DB
    activity_map = db.get_theaters_last_used()

    result = []
    for sid, s_dict in all_theaters_dict.items():
        dep = db.get_deployment(sid)
        owner_id = dep["user_id"] if dep else None
        is_owner = (current_user_id is not None and owner_id == current_user_id)

        s_dict["is_owner"] = is_owner
        last_used = activity_map.get(sid) or s_dict.get("created_at") or ""
        s_dict["last_used_at"] = last_used

        # Hide join_key if not owner
        if not is_owner:
            s_dict["join_key"] = "🔒 Owner Only"
            
        result.append(s_dict)

    # Sort: owned theaters first, then by last_used_at desc, then created_at desc
    result.sort(key=lambda x: (x["is_owner"], x.get("last_used_at", "") or x.get("created_at", "")), reverse=True)
    return result

@app.get("/api/theaters/default-config")
async def get_default_theater_config_endpoint(request: Request):
    """Return the source YAML used as the default for newly created theaters."""
    get_current_user(request)
    config_path = Path(__file__).resolve().parent / "theater_default.yaml"
    try:
        content = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="theater_default.yaml is unavailable.")
    except OSError as err:
        raise HTTPException(status_code=500, detail=f"Failed to read theater_default.yaml: {err}")
    return {"config_yaml": content}

@app.get("/api/theaters/{theater_id}")
async def get_theater(theater_id: str, request: Request):
    """Retrieve metadata and mounted assets for a specific theater."""
    _require_canvas_access(request, theater_id)
    theater_dir = theater_manager.theater(theater_id).directory()
    if not theater_dir.exists() or not (theater_dir / "theater.json").exists():
        db.reconstruct_theater_from_db(theater_id, theater_dir)

    meta = theater_manager.get_theater(theater_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Theater not found")
    
    current_user = get_current_user(request, record_activity=False)
    dep = db.get_deployment(theater_id)
    owner_id = dep["user_id"] if dep else None
    is_owner = (current_user is not None and owner_id == current_user["id"])

    # Analytics must not hold up the canvas reload, especially with a remote DB.
    client_ip = request.client.host if request.client else None
    asyncio.create_task(
        db.record_theater_view_async(
            theater_id,
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
        "references": theater_manager.get_theater_references(theater_id),
        "playlists": theater_manager.get_theater_playlists(theater_id),
    }

@app.get("/api/theaters/{theater_id}/config")
async def get_theater_config_endpoint(theater_id: str, request: Request):
    """Get raw theater.yaml configuration for a theater session."""
    _require_canvas_access(request, theater_id)
    _safe_path_param(theater_id, "theater_id")

    base_dir = theater_manager.base_dir
    theater_dir = theater_manager.theater(theater_id).directory()
    yaml_path = theater_dir / "theater.yaml"

    if not yaml_path.exists():
        get_theater_config(theater_id, base_dir=base_dir, db=db)

    if yaml_path.exists():
        try:
            content = yaml_path.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read theater.yaml: {e}")
    else:
        default_config = get_theater_default_config()
        content = yaml.safe_dump(default_config, default_flow_style=False)

    return {"theater_id": theater_id, "config_yaml": content}

@app.post("/api/theaters/{theater_id}/config")
async def save_theater_config_endpoint(theater_id: str, req: SaveTheaterConfigRequest, request: Request):
    """Save raw theater.yaml configuration directly to local theater directory and DB."""
    _require_canvas_access(request, theater_id)
    _safe_path_param(theater_id, "theater_id")

    try:
        config_data = yaml.safe_load(req.config_yaml)
        if config_data is None:
            config_data = {}
        if not isinstance(config_data, dict):
            raise HTTPException(status_code=400, detail="Invalid YAML: Root structure must be a mapping/object.")
    except yaml.YAMLError as err:
        raise HTTPException(status_code=400, detail=f"YAML Syntax Error: {err}")
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Failed to parse YAML: {err}")

    # Save to local theater directory
    base_dir = theater_manager.base_dir
    theater_dir = theater_manager.theater(theater_id).directory()
    theater_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = theater_dir / "theater.yaml"

    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(req.config_yaml)

    # Save to DB
    if db is not None and hasattr(db, "save_theater_config"):
        try:
            db.save_theater_config(theater_id, config_data)
        except Exception as e:
            logger.warning(f"[web_viewer_app] Warning: Failed to save DB config for {theater_id}: {e}")

    return {
        "status": "ok",
        "message": "theater.yaml saved directly to DB and theater directory. Restart your agent to apply changes.",
        "theater_id": theater_id
    }

@app.post("/api/theaters/format-yaml")
async def format_yaml_endpoint(req: SaveTheaterConfigRequest):
    """Validate and format a YAML string, returning pretty-printed YAML or syntax error details."""
    try:
        data = yaml.safe_load(req.config_yaml)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Invalid YAML: Root structure must be a mapping/object.")
        formatted = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        return {"status": "ok", "formatted_yaml": formatted}
    except yaml.YAMLError as err:
        raise HTTPException(status_code=400, detail=f"YAML Syntax Error: {err}")
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Failed to format YAML: {err}")

@app.post("/api/theaters/create-and-deploy")
async def create_and_deploy_theater(request: Request):
    """API endpoint to handle multi-file asset upload and deploy a theater."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to deploy theaters.")

    form = await request.form()
    name = str(form.get("name", "Narratron Theater"))
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
                        reference_files.append((rel_path, content))
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

    raw_config_yaml = form.get("theater_config_yaml")
    raw_config_param = form.get("theater_config")
    theater_config = None
    if raw_config_yaml:
        try:
            theater_config = yaml.safe_load(str(raw_config_yaml)) or {}
        except yaml.YAMLError as err:
            raise HTTPException(status_code=400, detail=f"Invalid theater YAML: {err}")
        if not isinstance(theater_config, dict):
            raise HTTPException(status_code=400, detail="Invalid theater YAML: root must be a mapping/object.")
    elif raw_config_param:
        try:
            theater_config = json.loads(raw_config_param) if isinstance(raw_config_param, str) else raw_config_param
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid theater configuration.")

    theater_id = f"theater_{uuid.uuid4().hex[:8]}"
    metadata = theater_manager.create_theater(
        name=name,
        theater_id=theater_id,
        reference_files=reference_files,
        playlists_data=playlists_data,
        theater_config=theater_config,
        style=style or None,
    )
    deployed_meta = theater_manager.deploy_theater(metadata.theater_id)

    # Record deployment & deduct credits (0.0 cost)
    db.record_deployment(deployed_meta.theater_id, user["id"], deployed_meta.join_key, cost=0.0, theater_config=theater_config)

    res_dict = deployed_meta.model_dump()
    res_dict["is_owner"] = True
    asyncio.create_task(
        db.persist_canvas_theater_async(
            canvas_states,
            theater_manager,
            deployed_meta.theater_id,
            user["id"],
            deployed_meta.name,
        )
    )
    return {"status": "ok", "theater_id": deployed_meta.theater_id, "theater": res_dict}

@app.post("/api/theaters/{theater_id}/deploy")
def deploy_existing_theater(theater_id: str, request: Request):
    """Deploy an existing created theater."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    theater_dir = theater_manager.theater(theater_id).directory()
    if not theater_dir.exists() or not (theater_dir / "theater.json").exists():
        db.reconstruct_theater_from_db(theater_id, theater_dir)
    
    dep = db.get_deployment(theater_id)
    if dep and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the theater owner can deploy this theater.")

    # Stop any other currently deployed theaters
    existing_theaters = theater_manager.list_theaters()
    for s in existing_theaters:
        if s.theater_id != theater_id and s.status == "deployed":
            try:
                theater_manager.stop_theater(s.theater_id)
            except Exception:
                pass

    meta = theater_manager.deploy_theater(theater_id)
    return {"status": "ok", "theater": meta}

@app.delete("/api/theaters/{theater_id}")
def destroy_theater(theater_id: str, request: Request):
    """Remove and clean up a local theater instance. Requires owner login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to delete theaters.")

    dep = db.get_deployment(theater_id)
    if dep and dep.get("user_id") is not None and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Permission denied. Only the theater owner can delete this theater.")

    disk_removed = theater_manager.destroy_theater(theater_id)
    db_deleted = db.delete_deployment(theater_id)
    canvas_states.states.pop(theater_id, None)

    if not (disk_removed or db_deleted):
        raise HTTPException(status_code=404, detail="Theater not found or could not be removed")

    return {"status": "ok", "theater_id": theater_id}

# ========================================
# Canvas & WebSocket Endpoints
# ========================================

@app.websocket("/ws/doodle")
async def websocket_endpoint(websocket: WebSocket, theater_id: Optional[str] = None):
    if theater_id:
        try:
            _require_canvas_access(websocket, theater_id)
        except HTTPException:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    websocket.state.theater_id = theater_id
    current_user = get_current_user(websocket)
    cs = await canvas_states.connect_doodle_websocket(websocket, theater_id, user=current_user)
    
    if theater_id:
        baton_st = db.get_theater_baton_state(theater_id)
        if baton_st:
            await canvas_states.broadcast_baton_update(theater_id, baton_st)

    try:
        while True:
            data = await websocket.receive_json()
            await canvas_states.apply_doodle_message(cs, data, sender=websocket)
    except WebSocketDisconnect:
        cs.unregister_websocket(websocket)
        if theater_id:
            baton_st = db.get_theater_baton_state(theater_id)
            if baton_st:
                await canvas_states.broadcast_baton_update(theater_id, baton_st)


# ========================================
# Baton Passing API Endpoints
# ========================================

@app.get("/api/theaters/{theater_id}/baton")
async def get_theater_baton_state(theater_id: str, request: Request):
    _require_canvas_access(request, theater_id)
    state = db.get_theater_baton_state(theater_id)
    if not state:
        raise HTTPException(status_code=404, detail="Theater baton state not found.")
    
    cs = canvas_states.get(theater_id)
    state["active_viewers"] = cs.get_active_viewers()
    return state


@app.post("/api/theaters/{theater_id}/baton/allowed_orators")
async def add_allowed_orator(theater_id: str, req: AddAllowedOratorRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.add_allowed_orator(theater_id, owner_id=user["id"], target_user_id=req.target_user_id)
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/theaters/{theater_id}/baton/allowed_orators/{target_user_id}")
async def remove_allowed_orator(theater_id: str, target_user_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.remove_allowed_orator(theater_id, owner_id=user["id"], target_user_id=target_user_id)
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/request")
async def request_baton_pass(theater_id: str, req: RequestBatonRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.request_baton(
            theater_id,
            owner_id=user["id"],
            target_user_id=req.target_user_id,
            timeout_seconds=req.timeout_seconds or 30
        )
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/accept")
async def accept_baton_pass(theater_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.accept_baton(theater_id, target_user_id=user["id"])
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/decline")
async def decline_baton_pass(theater_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.decline_baton(theater_id, target_user_id=user["id"])
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/takeback")
async def take_back_baton(theater_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = db.take_back_baton(theater_id, owner_id=user["id"])
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/save", status_code=202)
async def save_theater_to_db(theater_id: str, request: Request):
    """Save canvas theater state and image assets to SQLite database on user demand."""
    meta = theater_manager.get_theater(theater_id)
    dep = db.get_deployment(theater_id)
    user_id = dep["user_id"] if dep else None
    name = meta.name if meta else theater_id

    asyncio.create_task(
        db.persist_canvas_theater_async(
            canvas_states,
            theater_manager,
            theater_id,
            user_id,
            name,
        )
    )
    return {"status": "queued", "theater_id": theater_id}

@app.get("/api/theaters/{theater_id}/export-assets")
def export_theater_assets(theater_id: str, request: Request):
    """Package and export all theater assets into a ZIP file."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    theater_dir = theater_manager.theater(theater_id).directory()
    if not theater_dir.exists():
        db.reconstruct_theater_from_db(theater_id, theater_dir)

    # Ensure current displayed image is saved into the theater directory
    cs = canvas_states.get(theater_id)
    cs.export_theater_data(theater_dir=theater_dir)

    import io
    import zipfile
    from fastapi.responses import Response

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        if theater_dir.exists():
            for file_path in theater_dir.rglob("*"):
                if file_path.is_file():
                    arc_name = file_path.relative_to(theater_dir)
                    zip_file.write(file_path, arcname=str(arc_name).replace("\\", "/"))

    zip_buffer.seek(0)
    headers = {
        "Content-Disposition": f'attachment; filename="{theater_id}_assets.zip"'
    }
    return Response(content=zip_buffer.getvalue(), media_type="application/zip", headers=headers)

@app.api_route("/api/orator/toggle_mic", methods=["GET", "POST"])
async def trigger_orator_mic_toggle(request: Request, theater_id: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to control Orator microphone.")
    
    if theater_id:
        dep = db.get_deployment(theater_id)
        if dep and dep["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Permission denied. Only the theater owner can control the Orator microphone.")

    count = await canvas_states.toggle_microphone(theater_id)
    return {"status": "ok", "broadcasted_to": count}


@app.get("/api/latest")
def get_latest_image(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
        theater_dir = theater_manager.theater(theater_id).directory()
        if not theater_dir.exists():
            db.reconstruct_theater_from_db(theater_id, theater_dir)
    return canvas_states.latest_state(theater_id)

@app.get("/api/chat")
def get_chat(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    return canvas_states.chat_messages(theater_id)

@app.post("/api/chat")
def post_chat(msg: ChatMessage, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)

    # Detect /suggest prefix and route to the suggestion engine
    stripped = msg.text.strip()
    if stripped.lower().startswith("/suggest"):
        suggestion_text = stripped[len("/suggest"):].strip()
        if not suggestion_text:
            raise HTTPException(status_code=400, detail="Suggestion text must not be empty after /suggest.")
        canvas_states.add_suggestion(msg.author, suggestion_text, theater_id=theater_id)
        return {"status": "ok", "type": "suggestion"}

    canvas_states.add_chat_message(msg.text, author=msg.author, theater_id=theater_id)
    return {"status": "ok"}

# ========================================
# Viewer Collaboration / Suggestion API
# ========================================

class SuggestionUpvoteRequest(BaseModel):
    voter: str
    target_author: str

class SuggestionWithdrawRequest(BaseModel):
    author: str

class ViewerCollabToggleRequest(BaseModel):
    enabled: bool

@app.get("/api/suggestions")
def get_suggestions(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    return canvas_states.get_suggestions(theater_id)

@app.post("/api/suggestions/upvote")
def upvote_suggestion(req: SuggestionUpvoteRequest, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    success = canvas_states.upvote_suggestion(req.voter, req.target_author, theater_id=theater_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot upvote: suggestion not found or self-vote attempted.")
    return {"status": "ok"}

@app.post("/api/suggestions/withdraw")
def withdraw_suggestion(req: SuggestionWithdrawRequest, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    removed = canvas_states.withdraw_suggestion(req.author, theater_id=theater_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No active suggestion found for this author.")
    return {"status": "ok"}

@app.post("/api/theaters/{theater_id}/collab")
def toggle_viewer_collab(theater_id: str, req: ViewerCollabToggleRequest, request: Request):
    deployment = _require_canvas_access(request, theater_id)
    user = get_current_user(request)
    active_orator_id = deployment.get("active_orator_id") or deployment.get("user_id")
    if not user or user["id"] != active_orator_id:
        raise HTTPException(status_code=403, detail="Only the active orator can change Viewer Collab Mode.")
    canvas_states.set_viewer_collab_enabled(req.enabled, theater_id=theater_id)
    return {"status": "ok", "viewer_collab_enabled": req.enabled}

@app.get("/api/stats")
def get_stats_api():
    """Retrieve system stats summary (accounts, 7-day active users, theater views)."""
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
    """Serve the Theater Creation & App Deployer Dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "theater_creation.html")
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
def read_popout(request: Request, theater_id: Optional[str] = None, join_key: Optional[str] = None):
    """Serve the standalone Pop-out Panel interface for a theater."""
    if theater_id:
        deployment = _require_canvas_access(request, theater_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, theater_id, join_key)
            return response
    template_path = os.path.join(os.path.dirname(__file__), "templates", "popout.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/obs", response_class=HTMLResponse)
@app.get("/obs/{theater_id}", response_class=HTMLResponse)
async def read_obs_canvas(
    request: Request,
    theater_id: Optional[str] = None,
):
    """Serve the dedicated, UI-free Canvas interface specifically for OBS Browser Source."""
    if theater_id:
        join_key = request.query_params.get("join_key")
        deployment = _require_canvas_access(request, theater_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, theater_id, join_key)
            return response
        theater_dir = theater_manager.theater(theater_id).directory()
        if not theater_dir.exists():
            db.reconstruct_theater_from_db(theater_id, theater_dir)
        artifacts_dir = theater_dir / "output" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        current_user = get_current_user(request, record_activity=False)
        client_ip = request.client.host if request.client else None
        asyncio.create_task(
            db.record_theater_view_async(
                theater_id,
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
    theater_id: Optional[str] = None,
    join_key: Optional[str] = None,
):
    """Serve the Canvas interface for a specific theater."""
    if theater_id:
        deployment = _require_canvas_access(request, theater_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, theater_id, join_key)
            return response

        theater_dir = theater_manager.theater(theater_id).directory()
        if not theater_dir.exists():
            db.reconstruct_theater_from_db(theater_id, theater_dir)
        artifacts_dir = theater_dir / "output" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Analytics must not delay the initial canvas render.
        current_user = get_current_user(request, record_activity=False)
        client_ip = request.client.host if request.client else None
        asyncio.create_task(
            db.record_theater_view_async(
                theater_id,
                current_user["id"] if current_user else None,
                client_ip,
            )
        )

    is_obs = request.query_params.get("obs") == "1" or request.query_params.get("obs") == "true"
    template_name = "obs.html" if is_obs else "canvas.html"
    template_path = os.path.join(os.path.dirname(__file__), "templates", template_name)
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()
