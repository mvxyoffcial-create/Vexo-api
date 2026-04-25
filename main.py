import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import subprocess
import sys
import time
import traceback
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse as StarletteJSON

# ─────────────────────────── CONFIG ───────────────────────────
load_dotenv()

MONGO_URI = "mongodb+srv://Zerobothost:zero8907@cluster0.szwdcyb.mongodb.net/?appName=Cluster0"
JWT_SECRET = "rashmibabyy"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7  # 7 days

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "natravelsoffcail@gmail.com"
SMTP_PASS = "qpha qkbn rytr ncvu"

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8000")
NVIDIA_API_BASE = "https://nemotron-3-nano-nvidia.vercel.app/chat"

APPS_DIR = Path("./vexo_apps")
APPS_DIR.mkdir(exist_ok=True)

SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))

# ─────────────────────────── LOGGING ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("vexo")

# ─────────────────────────── APP INIT ─────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Vexo Platform",
    description="Cloud platform to deploy and manage Python apps with AI assistance",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# ─────────────────────────── OAUTH ────────────────────────────
oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ─────────────────────────── DB ───────────────────────────────
mongo_client: AsyncIOMotorClient = None
db = None


async def get_db():
    return db


# ─────────────────────────── SECURITY ─────────────────────────
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


def create_jwt(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=JWT_EXPIRE_HOURS))
    payload["exp"] = expire
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    database=Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_jwt(credentials.credentials)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Token error: {str(e)}")

    user = await database["users"].find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ─────────────────────────── EMAIL ────────────────────────────
def send_email_sync(to: str, subject: str, html_body: str):
    """Send email via SMTP (runs in thread pool)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Vexo Platform <{SMTP_USER}>"
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())
        logger.info(f"Email sent to {to}: {subject}")
    except Exception as e:
        logger.error(f"Email send failed to {to}: {e}")


async def send_email(to: str, subject: str, html_body: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_email_sync, to, subject, html_body)


def verification_email_html(name: str, link: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: 'Courier New', monospace; background:#0a0a0f; color:#e0e0ff; margin:0; padding:40px;">
      <div style="max-width:560px; margin:auto; border:1px solid #2a2a4a; border-radius:12px; padding:40px; background:#12121f;">
        <div style="text-align:center; margin-bottom:32px;">
          <span style="font-size:32px; font-weight:900; letter-spacing:4px; color:#7c6fff;">VEXO</span>
          <p style="color:#6060a0; font-size:12px; margin:4px 0 0;">CLOUD PLATFORM</p>
        </div>
        <h2 style="color:#a0a0ff; margin-bottom:8px;">Verify your email</h2>
        <p style="color:#8080b0; line-height:1.6;">Hi {name}, click the button below to verify your account and start deploying.</p>
        <div style="text-align:center; margin:32px 0;">
          <a href="{link}" style="background:linear-gradient(135deg,#7c6fff,#a06fff); color:#fff; padding:14px 32px;
             border-radius:8px; text-decoration:none; font-weight:700; letter-spacing:1px; font-size:14px;">
            ✓ VERIFY EMAIL
          </a>
        </div>
        <p style="color:#404060; font-size:12px; text-align:center;">Link expires in 24 hours. If you didn't create a Vexo account, ignore this email.</p>
        <hr style="border:none; border-top:1px solid #2a2a4a; margin:24px 0;">
        <p style="color:#30304a; font-size:11px; text-align:center;">{BACKEND_URL}</p>
      </div>
    </body>
    </html>
    """


def password_reset_email_html(name: str, link: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: 'Courier New', monospace; background:#0a0a0f; color:#e0e0ff; margin:0; padding:40px;">
      <div style="max-width:560px; margin:auto; border:1px solid #2a2a4a; border-radius:12px; padding:40px; background:#12121f;">
        <div style="text-align:center; margin-bottom:32px;">
          <span style="font-size:32px; font-weight:900; letter-spacing:4px; color:#7c6fff;">VEXO</span>
          <p style="color:#6060a0; font-size:12px; margin:4px 0 0;">CLOUD PLATFORM</p>
        </div>
        <h2 style="color:#ffa06f; margin-bottom:8px;">Reset your password</h2>
        <p style="color:#8080b0; line-height:1.6;">Hi {name}, we received a request to reset your password.</p>
        <div style="text-align:center; margin:32px 0;">
          <a href="{link}" style="background:linear-gradient(135deg,#ff6f6f,#ffa06f); color:#fff; padding:14px 32px;
             border-radius:8px; text-decoration:none; font-weight:700; letter-spacing:1px; font-size:14px;">
            🔑 RESET PASSWORD
          </a>
        </div>
        <p style="color:#404060; font-size:12px; text-align:center;">Link expires in 1 hour. If you didn't request this, ignore this email.</p>
        <hr style="border:none; border-top:1px solid #2a2a4a; margin:24px 0;">
        <p style="color:#30304a; font-size:11px; text-align:center;">{BACKEND_URL}</p>
      </div>
    </body>
    </html>
    """


# ─────────────────────────── SCHEMAS ──────────────────────────
class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=6)


class DeployRequest(BaseModel):
    repo_url: str
    branch: str = "main"
    app_name: str = Field(..., min_length=2, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    start_command: str = "python main.py"
    env_vars: Dict[str, str] = {}


class AIChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class AIFixErrorRequest(BaseModel):
    app_id: str
    logs: Optional[str] = None


class AIGenerateProjectRequest(BaseModel):
    description: str = Field(..., min_length=5, max_length=500)


class AIAnalyzeLogsRequest(BaseModel):
    logs: str = Field(..., min_length=1, max_length=10000)


# ─────────────────────────── AI HELPERS ───────────────────────
async def call_nvidia_ai(prompt: str) -> str:
    """Call the NVIDIA Nemotron API and return response text."""
    encoded = urllib.parse.quote(prompt)
    url = f"{NVIDIA_API_BASE}?prompt={encoded}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            # Try common response fields
            for field in ["response", "text", "content", "message", "answer", "result"]:
                if field in data:
                    return str(data[field])
            # If it's just a string
            if isinstance(data, str):
                return data
            return str(data)
    except httpx.TimeoutException:
        return "⚠️ AI response timed out. Please try again."
    except Exception as e:
        logger.error(f"NVIDIA API error: {e}")
        return f"⚠️ AI service temporarily unavailable: {str(e)}"


def build_context_prompt(system: str, history: List[dict], user_message: str) -> str:
    """Build a formatted prompt with chat history for context."""
    parts = [system, "\n\n--- Conversation History ---"]
    for msg in history[-10:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        parts.append(f"{role}: {msg['content']}")
    parts.append(f"\n--- Current Message ---\nUser: {user_message}\nAssistant:")
    return "\n".join(parts)


# ─────────────────────────── DEPLOYMENT ───────────────────────
running_processes: Dict[str, subprocess.Popen] = {}


async def clone_and_run(
    app_id: str,
    repo_url: str,
    branch: str,
    start_command: str,
    env_vars: dict,
    user_id: str,
    database,
):
    """Clone GitHub repo, create venv, install deps, run app."""
    app_dir = APPS_DIR / app_id
    log_file = app_dir / "vexo.log"

    def write_log(line: str):
        with open(log_file, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {line}\n")

    try:
        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {"status": "cloning", "updated_at": datetime.now(timezone.utc)}},
        )
        write_log(f"Cloning {repo_url} branch={branch}")

        # Clone
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(app_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {result.stderr}")
        write_log("Clone successful")

        # Create venv
        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {"status": "installing"}},
        )
        venv_dir = app_dir / ".venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        write_log("Virtual env created")

        # Install requirements
        req_file = app_dir / "requirements.txt"
        if req_file.exists():
            pip = venv_dir / "bin" / "pip"
            result = subprocess.run(
                [str(pip), "install", "-r", str(req_file)],
                capture_output=True, text=True, timeout=300, cwd=str(app_dir),
            )
            write_log(f"pip install: {result.stdout[-500:] if result.stdout else 'done'}")
            if result.returncode != 0:
                write_log(f"pip error: {result.stderr[-500:]}")

        # Start app
        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
        )
        write_log(f"Starting: {start_command}")

        # Build env
        proc_env = os.environ.copy()
        proc_env.update(env_vars)
        python_bin = venv_dir / "bin" / "python"

        cmd_parts = start_command.replace("python ", f"{python_bin} ", 1).split()

        with open(log_file, "a") as log_f:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=str(app_dir),
                env=proc_env,
                stdout=log_f,
                stderr=log_f,
            )
        running_processes[app_id] = proc
        write_log(f"Process started PID={proc.pid}")
        logger.info(f"App {app_id} started PID={proc.pid}")

    except Exception as e:
        error_msg = traceback.format_exc()
        write_log(f"DEPLOYMENT ERROR: {error_msg}")
        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {"status": "failed", "error": str(e)}},
        )
        logger.error(f"Deploy {app_id} failed: {e}")


# ─────────────────────────── STARTUP ──────────────────────────
@app.on_event("startup")
async def startup():
    global mongo_client, db
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client["vexo"]
    # Indexes
    await db["users"].create_index("email", unique=True)
    await db["users"].create_index("verification_token", sparse=True)
    await db["users"].create_index("reset_token", sparse=True)
    await db["deployments"].create_index("app_id", unique=True)
    await db["deployments"].create_index("user_id")
    await db["ai_chats"].create_index("user_id")
    logger.info("✅ Vexo platform started — MongoDB connected")


@app.on_event("shutdown")
async def shutdown():
    for app_id, proc in running_processes.items():
        proc.terminate()
    if mongo_client:
        mongo_client.close()
    logger.info("Vexo shutdown complete")


# ──────────────────────── HEALTH ──────────────────────────────
@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "platform": "Vexo", "version": "1.0.0", "time": datetime.now(timezone.utc).isoformat()}


# ═══════════════════════════════════════════════════════════════
#                        AUTH ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/auth/register", tags=["auth"])
@limiter.limit("5/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    database=Depends(get_db),
):
    existing = await database["users"].find_one({"email": body.email})
    if existing:
        raise HTTPException(400, "Email already registered")

    user_id = str(uuid.uuid4())
    verification_token = secrets.token_urlsafe(32)

    user_doc = {
        "_id": user_id,
        "name": body.name,
        "email": body.email,
        "password_hash": hash_password(body.password),
        "is_verified": False,
        "verification_token": verification_token,
        "verification_token_expires": datetime.now(timezone.utc) + timedelta(hours=24),
        "auth_provider": "email",
        "avatar": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "reset_token": None,
        "reset_token_expires": None,
    }
    await database["users"].insert_one(user_doc)

    verify_link = f"{BACKEND_URL}/auth/verify-email?token={verification_token}"
    background_tasks.add_task(
        send_email,
        body.email,
        "Verify your Vexo account",
        verification_email_html(body.name, verify_link),
    )

    return {
        "message": "Registration successful. Check your email to verify your account.",
        "user_id": user_id,
    }


@app.get("/auth/verify-email", tags=["auth"])
async def verify_email(token: str, database=Depends(get_db)):
    user = await database["users"].find_one({"verification_token": token})
    if not user:
        return HTMLResponse(
            "<h2 style='font-family:monospace;color:red'>Invalid or expired verification link.</h2>",
            status_code=400,
        )

    expires = user.get("verification_token_expires")
    if expires and datetime.now(timezone.utc) > expires.replace(tzinfo=timezone.utc):
        return HTMLResponse(
            "<h2 style='font-family:monospace;color:red'>Verification link has expired. Please register again.</h2>",
            status_code=400,
        )

    await database["users"].update_one(
        {"_id": user["_id"]},
        {
            "$set": {"is_verified": True, "updated_at": datetime.now(timezone.utc)},
            "$unset": {"verification_token": "", "verification_token_expires": ""},
        },
    )
    # Redirect to frontend with success
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head><meta http-equiv="refresh" content="3;url={FRONTEND_URL}/login?verified=1"></head>
    <body style="font-family:'Courier New',monospace;background:#0a0a0f;color:#7c6fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;">
      <div style="text-align:center;border:1px solid #2a2a4a;padding:48px;border-radius:12px;background:#12121f;">
        <div style="font-size:48px">✓</div>
        <h2 style="color:#a0ffa0;margin:16px 0">Email Verified!</h2>
        <p style="color:#6060a0">Your Vexo account is now active. Redirecting to login...</p>
      </div>
    </body>
    </html>
    """)


@app.post("/auth/login", tags=["auth"])
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, database=Depends(get_db)):
    user = await database["users"].find_one({"email": body.email})
    if not user:
        raise HTTPException(401, "Invalid credentials")

    if user.get("auth_provider") == "google":
        raise HTTPException(401, "This account uses Google sign-in. Use 'Sign in with Google'.")

    if not verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(401, "Invalid credentials")

    if not user.get("is_verified"):
        raise HTTPException(403, "Please verify your email before logging in.")

    token = create_jwt({"sub": user["_id"], "email": user["email"], "name": user["name"]})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["_id"],
            "name": user["name"],
            "email": user["email"],
            "avatar": user.get("avatar"),
            "auth_provider": user.get("auth_provider", "email"),
        },
    }


@app.post("/auth/forgot-password", tags=["auth"])
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    database=Depends(get_db),
):
    user = await database["users"].find_one({"email": body.email})
    # Always return success to prevent email enumeration
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}

    if user.get("auth_provider") == "google":
        return {"message": "This account uses Google sign-in. No password to reset."}

    reset_token = secrets.token_urlsafe(32)
    await database["users"].update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "reset_token": reset_token,
                "reset_token_expires": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        },
    )

    reset_link = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    background_tasks.add_task(
        send_email,
        body.email,
        "Reset your Vexo password",
        password_reset_email_html(user["name"], reset_link),
    )
    return {"message": "If that email exists, a reset link has been sent."}


@app.post("/auth/reset-password", tags=["auth"])
@limiter.limit("5/minute")
async def reset_password(
    request: Request, body: ResetPasswordRequest, database=Depends(get_db)
):
    user = await database["users"].find_one({"reset_token": body.token})
    if not user:
        raise HTTPException(400, "Invalid or expired reset token")

    expires = user.get("reset_token_expires")
    if expires and datetime.now(timezone.utc) > expires.replace(tzinfo=timezone.utc):
        raise HTTPException(400, "Reset token has expired. Request a new one.")

    await database["users"].update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(body.new_password),
                "updated_at": datetime.now(timezone.utc),
            },
            "$unset": {"reset_token": "", "reset_token_expires": ""},
        },
    )
    return {"message": "Password reset successfully. You can now log in."}


@app.get("/auth/resend-verification", tags=["auth"])
@limiter.limit("2/minute")
async def resend_verification(
    request: Request,
    email: str,
    background_tasks: BackgroundTasks,
    database=Depends(get_db),
):
    user = await database["users"].find_one({"email": email})
    if not user or user.get("is_verified"):
        return {"message": "If applicable, a new verification link has been sent."}

    new_token = secrets.token_urlsafe(32)
    await database["users"].update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "verification_token": new_token,
                "verification_token_expires": datetime.now(timezone.utc) + timedelta(hours=24),
            }
        },
    )
    verify_link = f"{BACKEND_URL}/auth/verify-email?token={new_token}"
    background_tasks.add_task(
        send_email,
        email,
        "Verify your Vexo account",
        verification_email_html(user["name"], verify_link),
    )
    return {"message": "Verification email sent."}


# ─────────────────── GOOGLE OAUTH ─────────────────────────────

@app.get("/auth/google", tags=["auth"])
async def google_login(request: Request):
    redirect_uri = GOOGLE_REDIRECT_URI
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback", tags=["auth"])
async def google_callback(request: Request, database=Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(400, f"Google OAuth failed: {str(e)}")

    user_info = token.get("userinfo")
    if not user_info:
        # Fallback: fetch from userinfo endpoint
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {token['access_token']}"},
            )
            user_info = resp.json()

    email = user_info.get("email")
    name = user_info.get("name", email.split("@")[0])
    avatar = user_info.get("picture")
    google_id = user_info.get("sub")

    if not email:
        raise HTTPException(400, "Could not get email from Google")

    # Upsert user
    existing = await database["users"].find_one({"email": email})
    if existing:
        user_id = existing["_id"]
        await database["users"].update_one(
            {"_id": user_id},
            {
                "$set": {
                    "name": name,
                    "avatar": avatar,
                    "google_id": google_id,
                    "is_verified": True,
                    "auth_provider": "google",
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
    else:
        user_id = str(uuid.uuid4())
        await database["users"].insert_one(
            {
                "_id": user_id,
                "name": name,
                "email": email,
                "password_hash": None,
                "is_verified": True,
                "auth_provider": "google",
                "google_id": google_id,
                "avatar": avatar,
                "reset_token": None,
                "reset_token_expires": None,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )

    jwt_token = create_jwt({"sub": user_id, "email": email, "name": name})
    # Redirect to frontend with token
    redirect_url = f"{FRONTEND_URL}/auth/callback?token={jwt_token}&name={urllib.parse.quote(name)}"
    return RedirectResponse(url=redirect_url)


@app.get("/auth/me", tags=["auth"])
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["_id"],
        "name": current_user["name"],
        "email": current_user["email"],
        "avatar": current_user.get("avatar"),
        "is_verified": current_user.get("is_verified", False),
        "auth_provider": current_user.get("auth_provider", "email"),
        "created_at": current_user.get("created_at"),
    }


# ═══════════════════════════════════════════════════════════════
#                     DEPLOYMENT ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/deploy", tags=["deployment"])
@limiter.limit("10/minute")
async def deploy_app(
    request: Request,
    body: DeployRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    if not current_user.get("is_verified"):
        raise HTTPException(403, "Verify your email before deploying apps")

    app_id = str(uuid.uuid4())[:8]
    deployment_doc = {
        "app_id": app_id,
        "user_id": current_user["_id"],
        "app_name": body.app_name,
        "repo_url": body.repo_url,
        "branch": body.branch,
        "start_command": body.start_command,
        "env_vars": body.env_vars,
        "status": "queued",
        "error": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "started_at": None,
    }
    await database["deployments"].insert_one(deployment_doc)

    background_tasks.add_task(
        clone_and_run,
        app_id,
        body.repo_url,
        body.branch,
        body.start_command,
        body.env_vars,
        current_user["_id"],
        database,
    )

    return {
        "message": "Deployment queued",
        "app_id": app_id,
        "app_name": body.app_name,
        "status": "queued",
    }


@app.get("/apps", tags=["deployment"])
async def list_apps(
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    cursor = database["deployments"].find(
        {"user_id": current_user["_id"]},
        {"env_vars": 0},
    ).sort("created_at", -1)
    apps = []
    async for doc in cursor:
        doc["id"] = doc.pop("_id", doc.get("app_id"))
        apps.append(doc)
    return {"apps": apps}


@app.get("/apps/{app_id}", tags=["deployment"])
async def get_app(
    app_id: str,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    app_doc = await database["deployments"].find_one(
        {"app_id": app_id, "user_id": current_user["_id"]}, {"env_vars": 0}
    )
    if not app_doc:
        raise HTTPException(404, "App not found")
    app_doc["id"] = app_doc.pop("_id", app_doc.get("app_id"))
    return app_doc


@app.get("/apps/{app_id}/logs", tags=["deployment"])
async def get_app_logs(
    app_id: str,
    lines: int = 100,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    app_doc = await database["deployments"].find_one(
        {"app_id": app_id, "user_id": current_user["_id"]}
    )
    if not app_doc:
        raise HTTPException(404, "App not found")

    log_file = APPS_DIR / app_id / "vexo.log"
    if not log_file.exists():
        return {"app_id": app_id, "logs": "", "lines": 0}

    with open(log_file, "r", errors="replace") as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return {
        "app_id": app_id,
        "logs": "".join(tail),
        "lines": len(tail),
        "total_lines": len(all_lines),
    }


@app.delete("/apps/{app_id}", tags=["deployment"])
async def stop_app(
    app_id: str,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    app_doc = await database["deployments"].find_one(
        {"app_id": app_id, "user_id": current_user["_id"]}
    )
    if not app_doc:
        raise HTTPException(404, "App not found")

    if app_id in running_processes:
        running_processes[app_id].terminate()
        del running_processes[app_id]

    await database["deployments"].update_one(
        {"app_id": app_id},
        {"$set": {"status": "stopped", "updated_at": datetime.now(timezone.utc)}},
    )
    return {"message": f"App {app_id} stopped"}


# ═══════════════════════════════════════════════════════════════
#                       AI ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/ai/chat", tags=["ai"])
@limiter.limit("30/minute")
async def ai_chat(
    request: Request,
    body: AIChatRequest,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    user_id = current_user["_id"]

    # Load or create chat session
    chat_doc = await database["ai_chats"].find_one({"user_id": user_id})
    if not chat_doc:
        chat_doc = {
            "user_id": user_id,
            "messages": [],
            "created_at": datetime.now(timezone.utc),
        }

    history = chat_doc.get("messages", [])

    system_prompt = (
        "You are Vexo AI, an expert developer assistant inside the Vexo cloud platform. "
        "You help users deploy Python apps (Telegram bots, FastAPI APIs, scripts), fix errors, "
        "analyze logs, and generate project code. Be concise, practical, and friendly. "
        "Format code with proper Python syntax. Use emojis sparingly for UX."
    )

    prompt = build_context_prompt(system_prompt, history, body.message)
    ai_response = await call_nvidia_ai(prompt)

    # Update history (keep last 10 exchanges = 20 messages)
    history.append({"role": "user", "content": body.message})
    history.append({"role": "assistant", "content": ai_response})
    history = history[-20:]

    await database["ai_chats"].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "messages": history,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )

    return {
        "response": ai_response,
        "message_count": len(history),
    }


@app.get("/ai/history", tags=["ai"])
async def get_chat_history(
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    chat_doc = await database["ai_chats"].find_one({"user_id": current_user["_id"]})
    if not chat_doc:
        return {"messages": [], "message_count": 0}
    messages = chat_doc.get("messages", [])
    return {"messages": messages, "message_count": len(messages)}


@app.delete("/ai/history", tags=["ai"])
async def clear_chat_history(
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    await database["ai_chats"].delete_one({"user_id": current_user["_id"]})
    return {"message": "Chat history cleared"}


@app.post("/ai/fix-error", tags=["ai"])
@limiter.limit("15/minute")
async def ai_fix_error(
    request: Request,
    body: AIFixErrorRequest,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    # Get logs from file if not provided
    logs = body.logs
    if not logs:
        app_doc = await database["deployments"].find_one(
            {"app_id": body.app_id, "user_id": current_user["_id"]}
        )
        if not app_doc:
            raise HTTPException(404, "App not found")

        log_file = APPS_DIR / body.app_id / "vexo.log"
        if log_file.exists():
            with open(log_file, "r", errors="replace") as f:
                all_lines = f.readlines()
            logs = "".join(all_lines[-50:])  # last 50 lines
        else:
            logs = "No logs found"

    prompt = (
        "You are an expert Python developer and debugger. "
        "Analyze these error logs from a deployed Python application. "
        "Provide:\n"
        "1. Root cause analysis\n"
        "2. Step-by-step fix instructions\n"
        "3. Fixed code snippet if applicable\n"
        "4. How to prevent this error in the future\n\n"
        f"=== ERROR LOGS ===\n{logs[-3000:]}\n"
        "=== END LOGS ===\n\n"
        "Respond in a structured, clear format:"
    )

    response = await call_nvidia_ai(prompt)
    return {
        "app_id": body.app_id,
        "analysis": response,
        "logs_analyzed": logs[-500:],
    }


@app.post("/ai/generate-project", tags=["ai"])
@limiter.limit("10/minute")
async def ai_generate_project(
    request: Request,
    body: AIGenerateProjectRequest,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    prompt = (
        "You are a senior Python developer. Generate a complete, production-ready Python project based on this description:\n\n"
        f"PROJECT: {body.description}\n\n"
        "Generate EXACTLY this structure:\n\n"
        "## main.py\n"
        "```python\n"
        "[complete working main.py code]\n"
        "```\n\n"
        "## requirements.txt\n"
        "```\n"
        "[all required packages, one per line]\n"
        "```\n\n"
        "## .env.example\n"
        "```\n"
        "[environment variables needed]\n"
        "```\n\n"
        "## README.md\n"
        "[brief setup and run instructions]\n\n"
        "Make the code complete, functional, and ready to deploy on Vexo platform. "
        "Include proper error handling, logging, and comments."
    )

    response = await call_nvidia_ai(prompt)

    # Log the generation
    await database["ai_chats"].update_one(
        {"user_id": current_user["_id"]},
        {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "content": f"Generate project: {body.description}"},
                        {"role": "assistant", "content": response[:500] + "..."},
                    ]
                }
            }
        },
        upsert=True,
    )

    return {
        "description": body.description,
        "generated_code": response,
    }


@app.post("/ai/analyze-logs", tags=["ai"])
@limiter.limit("20/minute")
async def ai_analyze_logs(
    request: Request,
    body: AIAnalyzeLogsRequest,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    prompt = (
        "You are a DevOps and Python expert. Analyze these application logs:\n\n"
        f"=== LOGS ===\n{body.logs[-5000:]}\n=== END ===\n\n"
        "Provide a detailed analysis:\n"
        "🔍 **Issues Detected**: List any errors, warnings, or problems\n"
        "⚡ **Severity Level**: Critical / Warning / Info for each issue\n"
        "🛠️ **Suggested Fixes**: Concrete fix for each issue\n"
        "📊 **Performance Notes**: Any performance concerns\n"
        "✅ **Health Summary**: Overall app health assessment\n"
    )

    response = await call_nvidia_ai(prompt)
    return {
        "analysis": response,
        "log_lines": len(body.logs.splitlines()),
    }


# ═══════════════════════════════════════════════════════════════
#                     LIVE APP STATUS (SSE)
# ═══════════════════════════════════════════════════════════════

@app.get("/apps/{app_id}/stream-logs", tags=["deployment"])
async def stream_logs(
    app_id: str,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    """Server-Sent Events endpoint for live log streaming."""
    app_doc = await database["deployments"].find_one(
        {"app_id": app_id, "user_id": current_user["_id"]}
    )
    if not app_doc:
        raise HTTPException(404, "App not found")

    log_file = APPS_DIR / app_id / "vexo.log"

    async def event_generator():
        last_pos = 0
        for _ in range(300):  # max 5 min stream (1s intervals)
            if log_file.exists():
                with open(log_file, "r", errors="replace") as f:
                    f.seek(last_pos)
                    new_content = f.read()
                    last_pos = f.tell()
                if new_content:
                    for line in new_content.splitlines():
                        yield f"data: {json.dumps({'log': line})}\n\n"
            await asyncio.sleep(1)
        yield "data: {\"log\": \"[stream ended]\"}\n\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════
#                       LANDING PAGE
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def landing_page():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vexo — Cloud Platform for Python Apps</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#07070f;--surface:#0f0f1e;--border:#1e1e3f;
  --accent:#7c6fff;--accent2:#a06fff;--text:#e0e0ff;
  --muted:#5a5a8a;--green:#4fff9f;--red:#ff6f6f;
}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;overflow-x:hidden}
.mono{font-family:'Space Mono',monospace}

/* Grid background */
body::before{
  content:'';position:fixed;inset:0;
  background-image:linear-gradient(rgba(124,111,255,.04) 1px,transparent 1px),
    linear-gradient(90deg,rgba(124,111,255,.04) 1px,transparent 1px);
  background-size:60px 60px;pointer-events:none;z-index:0;
}

nav{
  position:fixed;top:0;left:0;right:0;z-index:100;
  display:flex;align-items:center;justify-content:space-between;
  padding:18px 48px;border-bottom:1px solid var(--border);
  background:rgba(7,7,15,.85);backdrop-filter:blur(12px);
}
.logo{font-family:'Space Mono',monospace;font-size:22px;font-weight:700;
  letter-spacing:5px;color:var(--accent);text-decoration:none}
.nav-links{display:flex;gap:12px}
.btn{
  padding:10px 22px;border-radius:6px;font-size:13px;font-weight:600;
  cursor:pointer;transition:all .2s;text-decoration:none;letter-spacing:.5px;
  font-family:'Space Mono',monospace;
}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--muted)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:var(--accent);border:1px solid var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent2);transform:translateY(-1px)}

hero{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:100vh;text-align:center;padding:120px 24px 80px;position:relative;z-index:1;
}
.badge{
  display:inline-flex;align-items:center;gap:8px;
  border:1px solid var(--border);border-radius:100px;
  padding:6px 16px;font-size:11px;letter-spacing:2px;color:var(--muted);
  font-family:'Space Mono',monospace;margin-bottom:32px;
}
.badge::before{content:'';width:6px;height:6px;border-radius:50%;
  background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

h1{
  font-size:clamp(40px,7vw,88px);font-weight:300;line-height:1.05;
  letter-spacing:-2px;margin-bottom:24px;
}
h1 strong{
  font-weight:700;background:linear-gradient(135deg,var(--accent),var(--accent2),#ff9fff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.sub{max-width:520px;color:var(--muted);font-size:17px;line-height:1.7;margin-bottom:48px}

.hero-btns{display:flex;gap:14px;flex-wrap:wrap;justify-content:center;margin-bottom:80px}
.btn-lg{padding:14px 32px;font-size:14px}

.terminal{
  background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:24px 28px;max-width:580px;width:100%;text-align:left;
  font-family:'Space Mono',monospace;font-size:13px;
}
.t-bar{display:flex;gap:6px;margin-bottom:18px}
.t-dot{width:10px;height:10px;border-radius:50%}
.t-r{background:#ff5f56}.t-y{background:#ffbd2e}.t-g{background:#27c93f}
.t-line{color:var(--muted);line-height:2}
.t-line .cmd{color:var(--accent)}
.t-line .out{color:var(--green)}
.t-line .dim{color:#333}

.features{
  max-width:1100px;margin:0 auto;padding:80px 24px;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;
  position:relative;z-index:1;
}
.card{
  background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:28px;transition:border-color .2s,transform .2s;
}
.card:hover{border-color:var(--accent);transform:translateY(-3px)}
.card-icon{font-size:28px;margin-bottom:16px}
.card h3{font-family:'Space Mono',monospace;font-size:14px;letter-spacing:1px;
  color:var(--accent);margin-bottom:10px}
.card p{color:var(--muted);font-size:14px;line-height:1.7}

footer{
  text-align:center;padding:40px;border-top:1px solid var(--border);
  color:var(--muted);font-family:'Space Mono',monospace;font-size:11px;letter-spacing:1px;
  position:relative;z-index:1;
}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">VEXO</a>
  <div class="nav-links">
    <a href="/docs" class="btn btn-ghost">API Docs</a>
    <a href="/docs" class="btn btn-primary">Get Started →</a>
  </div>
</nav>

<hero>
  <div class="badge">🟢 PLATFORM ONLINE</div>
  <h1>Deploy Python apps<br><strong>in seconds</strong></h1>
  <p class="sub">Push from GitHub. Vexo handles the rest — venv, dependencies, runtime, logs. With a built-in AI assistant to fix errors, generate code, and analyze issues.</p>
  <div class="hero-btns">
    <a href="/docs" class="btn btn-primary btn-lg">Start Deploying</a>
    <a href="/docs" class="btn btn-ghost btn-lg">View API →</a>
  </div>
  <div class="terminal">
    <div class="t-bar">
      <div class="t-dot t-r"></div>
      <div class="t-dot t-y"></div>
      <div class="t-dot t-g"></div>
    </div>
    <div class="t-line"><span class="cmd">POST</span> /deploy</div>
    <div class="t-line dim">{"repo_url": "github.com/you/bot", "branch": "main"}</div>
    <div class="t-line"></div>
    <div class="t-line"><span class="out">✓ Cloning repository...</span></div>
    <div class="t-line"><span class="out">✓ Creating virtual environment...</span></div>
    <div class="t-line"><span class="out">✓ Installing dependencies...</span></div>
    <div class="t-line"><span class="out">✓ App running → PID 3847</span></div>
    <div class="t-line"></div>
    <div class="t-line"><span class="cmd">GET</span> /apps/a1b2c3/logs <span class="dim">→ live tail</span></div>
  </div>
</hero>

<div class="features">
  <div class="card">
    <div class="card-icon">🚀</div>
    <h3>INSTANT DEPLOY</h3>
    <p>Point Vexo at any GitHub repo. It clones, builds a venv, installs requirements.txt, and runs your app automatically.</p>
  </div>
  <div class="card">
    <div class="card-icon">🤖</div>
    <h3>AI ASSISTANT</h3>
    <p>Powered by NVIDIA Nemotron. Ask it to fix errors, explain logs, generate full project code, or improve your codebase.</p>
  </div>
  <div class="card">
    <div class="card-icon">📊</div>
    <h3>LIVE LOGS</h3>
    <p>Stream logs in real-time via SSE. The AI log analyzer highlights errors and suggests fixes automatically.</p>
  </div>
  <div class="card">
    <div class="card-icon">🔐</div>
    <h3>SECURE AUTH</h3>
    <p>JWT authentication with Google OAuth, email verification, and password reset — all production-grade.</p>
  </div>
  <div class="card">
    <div class="card-icon">🧠</div>
    <h3>AI MEMORY</h3>
    <p>Vexo AI remembers your conversation. It builds context across messages to give smarter, more relevant help.</p>
  </div>
  <div class="card">
    <div class="card-icon">⚡</div>
    <h3>PROJECT GENERATOR</h3>
    <p>Describe what you want to build — "Telegram bot for movies" — and the AI generates main.py, requirements.txt, and docs.</p>
  </div>
</div>

<footer>VEXO PLATFORM · BUILT ON FASTAPI + MONGODB · AI BY NVIDIA NEMOTRON</footer>
</body>
</html>
""")


# ─────────────────── RESET PASSWORD PAGE ──────────────────────
@app.get("/reset-password", response_class=HTMLResponse, tags=["ui"])
async def reset_password_page(token: str = ""):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"><title>Reset Password — Vexo</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#07070f;color:#e0e0ff;font-family:'DM Sans',sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh;
  background-image:linear-gradient(rgba(124,111,255,.04) 1px,transparent 1px),
    linear-gradient(90deg,rgba(124,111,255,.04) 1px,transparent 1px);
  background-size:60px 60px;}}
.card{{background:#0f0f1e;border:1px solid #1e1e3f;border-radius:16px;padding:48px;width:100%;max-width:420px}}
.logo{{font-family:'Space Mono',monospace;font-size:20px;font-weight:700;letter-spacing:5px;color:#7c6fff;text-align:center;margin-bottom:32px}}
h2{{font-size:22px;margin-bottom:8px;color:#ffa06f}}
p{{color:#5a5a8a;font-size:14px;margin-bottom:28px;line-height:1.6}}
input{{width:100%;background:#07070f;border:1px solid #1e1e3f;color:#e0e0ff;
  padding:12px 16px;border-radius:8px;font-size:14px;margin-bottom:16px;
  font-family:'DM Sans',sans-serif;transition:border-color .2s}}
input:focus{{outline:none;border-color:#7c6fff}}
button{{width:100%;background:linear-gradient(135deg,#ff6f6f,#ffa06f);border:none;
  color:#fff;padding:13px;border-radius:8px;font-size:14px;font-weight:700;
  cursor:pointer;font-family:'Space Mono',monospace;letter-spacing:1px}}
.msg{{margin-top:16px;padding:12px;border-radius:8px;font-size:13px;text-align:center;display:none}}
.msg.ok{{background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f}}
.msg.err{{background:#1f0a0a;border:1px solid #ff6f6f;color:#ff6f6f}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">VEXO</div>
  <h2>🔑 Reset Password</h2>
  <p>Enter your new password below.</p>
  <input type="password" id="pwd" placeholder="New password (min 6 chars)">
  <input type="password" id="pwd2" placeholder="Confirm new password">
  <button onclick="doReset()">RESET PASSWORD</button>
  <div class="msg" id="msg"></div>
</div>
<script>
const token = "{token}";
async function doReset() {{
  const pwd = document.getElementById('pwd').value;
  const pwd2 = document.getElementById('pwd2').value;
  const msg = document.getElementById('msg');
  msg.style.display = 'none';
  if (pwd !== pwd2) {{ showMsg('Passwords do not match', false); return; }}
  if (pwd.length < 6) {{ showMsg('Password must be at least 6 characters', false); return; }}
  try {{
    const r = await fetch('/auth/reset-password', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{token, new_password: pwd}})
    }});
    const d = await r.json();
    if (r.ok) {{
      showMsg('✓ Password reset! Redirecting to login...', true);
      setTimeout(() => window.location.href = '/login?reset=1', 2000);
    }} else {{ showMsg(d.detail || 'Error occurred', false); }}
  }} catch(e) {{ showMsg('Network error', false); }}
}}
function showMsg(text, ok) {{
  const el = document.getElementById('msg');
  el.textContent = text; el.className = 'msg ' + (ok ? 'ok' : 'err');
  el.style.display = 'block';
}}
</script>
</body>
</html>
""")


@app.get("/login", response_class=HTMLResponse, tags=["ui"])
async def login_page(verified: int = 0, reset: int = 0):
    notice = ""
    if verified:
        notice = '<div style="background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f;padding:12px;border-radius:8px;margin-bottom:20px;font-size:13px;text-align:center">✓ Email verified! You can now log in.</div>'
    if reset:
        notice = '<div style="background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f;padding:12px;border-radius:8px;margin-bottom:20px;font-size:13px;text-align:center">✓ Password reset successfully!</div>'
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"><title>Login — Vexo</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#07070f;color:#e0e0ff;font-family:'DM Sans',sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh;
  background-image:linear-gradient(rgba(124,111,255,.04) 1px,transparent 1px),
    linear-gradient(90deg,rgba(124,111,255,.04) 1px,transparent 1px);
  background-size:60px 60px;}}
.card{{background:#0f0f1e;border:1px solid #1e1e3f;border-radius:16px;padding:48px;width:100%;max-width:420px}}
.logo{{font-family:'Space Mono',monospace;font-size:20px;font-weight:700;letter-spacing:5px;color:#7c6fff;text-align:center;margin-bottom:32px}}
h2{{font-size:22px;margin-bottom:24px}}
input{{width:100%;background:#07070f;border:1px solid #1e1e3f;color:#e0e0ff;
  padding:12px 16px;border-radius:8px;font-size:14px;margin-bottom:14px;
  font-family:'DM Sans',sans-serif;transition:border-color .2s}}
input:focus{{outline:none;border-color:#7c6fff}}
button.main{{width:100%;background:#7c6fff;border:none;color:#fff;padding:13px;
  border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;
  font-family:'Space Mono',monospace;letter-spacing:1px;margin-bottom:12px}}
button.google{{width:100%;background:#fff;border:none;color:#1a1a2e;padding:12px;
  border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;display:flex;
  align-items:center;justify-content:center;gap:10px}}
.divider{{text-align:center;color:#2a2a4a;font-size:12px;margin:16px 0;position:relative}}
.divider::before,.divider::after{{content:'';position:absolute;top:50%;width:42%;height:1px;background:#1e1e3f}}
.divider::before{{left:0}}.divider::after{{right:0}}
.links{{display:flex;justify-content:space-between;margin-top:20px;font-size:13px;color:#5a5a8a}}
.links a{{color:#7c6fff;text-decoration:none}}
.msg{{margin-top:14px;padding:12px;border-radius:8px;font-size:13px;text-align:center;display:none}}
.msg.ok{{background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f}}
.msg.err{{background:#1f0a0a;border:1px solid #ff6f6f;color:#ff6f6f}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">VEXO</div>
  {notice}
  <h2>Welcome back</h2>
  <input type="email" id="email" placeholder="Email">
  <input type="password" id="password" placeholder="Password">
  <button class="main" onclick="doLogin()">LOG IN</button>
  <div class="divider">OR</div>
  <button class="google" onclick="window.location.href='/auth/google'">
    <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.35-8.16 2.35-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
    Continue with Google
  </button>
  <div class="links">
    <a href="/register">Create account</a>
    <a href="#" onclick="forgotPwd()">Forgot password?</a>
  </div>
  <div class="msg" id="msg"></div>
</div>
<script>
// Handle OAuth callback token
const params = new URLSearchParams(window.location.search);
if (params.get('token')) {{
  localStorage.setItem('vexo_token', params.get('token'));
  window.location.href = '/dashboard';
}}
async function doLogin() {{
  const email = document.getElementById('email').value;
  const pass = document.getElementById('password').value;
  if (!email || !pass) {{ showMsg('Fill in all fields', false); return; }}
  try {{
    const r = await fetch('/auth/login', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email, password: pass}})
    }});
    const d = await r.json();
    if (r.ok) {{
      localStorage.setItem('vexo_token', d.access_token);
      showMsg('✓ Login successful!', true);
      setTimeout(() => window.location.href='/docs', 1000);
    }} else {{ showMsg(d.detail || 'Login failed', false); }}
  }} catch(e) {{ showMsg('Network error', false); }}
}}
async function forgotPwd() {{
  const email = document.getElementById('email').value;
  if (!email) {{ showMsg('Enter your email first', false); return; }}
  try {{
    const r = await fetch('/auth/forgot-password', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email}})
    }});
    const d = await r.json();
    showMsg(d.message, true);
  }} catch(e) {{ showMsg('Network error', false); }}
}}
function showMsg(t, ok) {{
  const el = document.getElementById('msg');
  el.textContent=t; el.className='msg '+(ok?'ok':'err'); el.style.display='block';
}}
</script>
</body>
</html>
""")


@app.get("/auth/callback", response_class=HTMLResponse, tags=["ui"])
async def oauth_callback_page():
    """Handles Google OAuth redirect to store token in localStorage."""
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head><title>Logging in... — Vexo</title></head>
<body style="background:#07070f;color:#7c6fff;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh">
<div style="text-align:center">
  <div style="font-size:32px;font-weight:900;letter-spacing:4px;margin-bottom:16px">VEXO</div>
  <p>Completing sign-in...</p>
</div>
<script>
const params = new URLSearchParams(window.location.search);
const token = params.get('token');
if (token) {
  localStorage.setItem('vexo_token', token);
  window.location.href = '/docs';
} else {
  window.location.href = '/login';
}
</script>
</body>
</html>
""")
