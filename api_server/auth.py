"""Authentication API endpoints."""

from fastapi import Request, Response, HTTPException
from pydantic import BaseModel

from api_server.shared import app, db, get_current_user
from utils.config_loader import get_app_config
from utils.email_service import send_password_reset_email


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

class MicSensitivityRequest(BaseModel):
    mic_sensitivity: float


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
    app_cfg = get_app_config()
    use_ricky = app_cfg.get("audio", {}).get("use_ricky0123_vad", True)
    user = get_current_user(request)
    if not user:
        return {"authenticated": False, "user": None, "use_ricky0123_vad": use_ricky}
    return {"authenticated": True, "user": user, "use_ricky0123_vad": use_ricky}

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
