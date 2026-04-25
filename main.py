"""
╔══════════════════════════════════════════════════════════════╗
║                    VEXO PLATFORM - main.py                   ║
║         Full Backend: Auth, Deploy, AI, Google OAuth         ║
║             Email Verification + Password Reset              ║
╚══════════════════════════════════════════════════════════════╝

Requirements (requirements.txt):
    fastapi==0.111.0
    uvicorn[standard]==0.29.0
    motor==3.3.2
    pymongo==4.6.3
    python-jose[cryptography]==3.3.0
    bcrypt==4.1.3
    python-multipart==0.0.9
    httpx==0.27.0
    python-dotenv==1.0.1
    aiofiles==23.2.1
    gitpython==3.1.43
    slowapi==0.1.9
    authlib==1.3.0
    email-validator==2.1.1
    Jinja2==3.1.4
    anthropic==0.25.0

Environment Variables (.env):
    MONGO_URI=mongodb+srv://...
    JWT_SECRET=your_super_secret_key_here
    GOOGLE_CLIENT_ID=your_google_client_id
    GOOGLE_CLIENT_SECRET=your_google_client_secret
    GOOGLE_REDIRECT_URI=https://your-app.koyeb.app/auth/google/callback
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=your@gmail.com
    SMTP_PASS=your_app_password
    BACKEND_URL=https://your-app.koyeb.app
    FRONTEND_URL=https://your-app.koyeb.app
    ANTHROPIC_API_KEY=your_anthropic_api_key

Koyeb Deployment:
    - Build command: pip install -r requirements.txt
    - Run command:   uvicorn main:app --host 0.0.0.0 --port 8080
    - Health check:  /health
"""

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
import bcrypt as _bcrypt_lib
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

# Claude AI (replaces broken NVIDIA API)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

APPS_DIR = Path("./vexo_apps")
APPS_DIR.mkdir(exist_ok=True)

# Vexo itself runs on 8080; deployed user apps start from 9000
VEXO_PORT = "8080"
APP_BASE_PORT = "9000"
APP_MAX_PORT  = "9999"

SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))

# ── Port allocator (in-memory; survives process restarts via DB) ──
_allocated_ports: set = set()   # populated on startup from DB


def allocate_port() -> int:
    """Return the next free port in the user-app range."""
    for port in range(APP_BASE_PORT, APP_MAX_PORT + 1):
        if port not in _allocated_ports:
            _allocated_ports.add(port)
            return port
    raise RuntimeError("No free ports available in range 9000-9999")

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
    version="2.0.0",
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
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    pw_bytes = hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")
    return _bcrypt_lib.hashpw(pw_bytes, _bcrypt_lib.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pw_bytes = hashlib.sha256(plain.encode("utf-8")).hexdigest().encode("utf-8")
        return _bcrypt_lib.checkpw(pw_bytes, hashed.encode("utf-8"))
    except Exception:
        return False


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
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Vexo Platform <{SMTP_USER}>"
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT)) as server:
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
    return f"""<!DOCTYPE html><html><body style="font-family:'Courier New',monospace;background:#0a0a0f;color:#e0e0ff;margin:0;padding:40px;">
<div style="max-width:560px;margin:auto;border:1px solid #2a2a4a;border-radius:12px;padding:40px;background:#12121f;">
  <div style="text-align:center;margin-bottom:32px;">
    <span style="font-size:32px;font-weight:900;letter-spacing:4px;color:#7c6fff;">VEXO</span>
    <p style="color:#6060a0;font-size:12px;margin:4px 0 0;">CLOUD PLATFORM</p>
  </div>
  <h2 style="color:#a0a0ff;margin-bottom:8px;">Verify your email</h2>
  <p style="color:#8080b0;line-height:1.6;">Hi {name}, click below to verify your account.</p>
  <div style="text-align:center;margin:32px 0;">
    <a href="{link}" style="background:linear-gradient(135deg,#7c6fff,#a06fff);color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;letter-spacing:1px;font-size:14px;">✓ VERIFY EMAIL</a>
  </div>
  <p style="color:#404060;font-size:12px;text-align:center;">Link expires in 24 hours.</p>
</div></body></html>"""


def password_reset_email_html(name: str, link: str) -> str:
    return f"""<!DOCTYPE html><html><body style="font-family:'Courier New',monospace;background:#0a0a0f;color:#e0e0ff;margin:0;padding:40px;">
<div style="max-width:560px;margin:auto;border:1px solid #2a2a4a;border-radius:12px;padding:40px;background:#12121f;">
  <div style="text-align:center;margin-bottom:32px;">
    <span style="font-size:32px;font-weight:900;letter-spacing:4px;color:#7c6fff;">VEXO</span>
  </div>
  <h2 style="color:#ffa06f;margin-bottom:8px;">Reset your password</h2>
  <p style="color:#8080b0;line-height:1.6;">Hi {name}, click below to reset your password.</p>
  <div style="text-align:center;margin:32px 0;">
    <a href="{link}" style="background:linear-gradient(135deg,#ff6f6f,#ffa06f);color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;letter-spacing:1px;font-size:14px;">🔑 RESET PASSWORD</a>
  </div>
  <p style="color:#404060;font-size:12px;text-align:center;">Link expires in 1 hour.</p>
</div></body></html>"""


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
    # branch is now OPTIONAL — auto-detected from GitHub if not provided
    branch: Optional[str] = None
    app_name: str = Field(..., min_length=2, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    # start_command REMOVED — buildpack auto-detection handles this
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


# ─────────────────── GITHUB BRANCH AUTO-DETECT ────────────────
def normalize_repo_url(repo_url: str) -> str:
    """Normalize GitHub URL to https format."""
    repo_url = repo_url.strip()
    if repo_url.startswith("git@github.com:"):
        repo_url = repo_url.replace("git@github.com:", "https://github.com/")
    if not repo_url.startswith("http"):
        repo_url = "https://github.com/" + repo_url.lstrip("/")
    if repo_url.endswith(".git"):
        repo_url = repo_url[:-4]
    return repo_url


async def detect_default_branch(repo_url: str) -> str:
    """
    Auto-detect the default branch of a GitHub repo via the GitHub API.
    Falls back to 'main', then 'master' if API call fails.
    """
    # Extract owner/repo from URL
    # e.g. https://github.com/owner/repo
    match = re.search(r"github\.com[/:]([^/]+)/([^/\s]+?)(?:\.git)?$", repo_url)
    if not match:
        logger.warning(f"Could not parse GitHub URL: {repo_url}, defaulting to 'main'")
        return "main"

    owner, repo = match.group(1), match.group(2)
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                api_url,
                headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "Vexo-Platform/2.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                branch = data.get("default_branch", "main")
                logger.info(f"Auto-detected branch '{branch}' for {owner}/{repo}")
                return branch
            else:
                logger.warning(f"GitHub API returned {resp.status_code} for {owner}/{repo}")
    except Exception as e:
        logger.warning(f"Branch auto-detect failed: {e}")

    # Fallback: try 'main', then 'master' by attempting a git ls-remote
    for fallback in ["main", "master"]:
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", repo_url, fallback],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and fallback in result.stdout:
                logger.info(f"Fallback branch detected: {fallback}")
                return fallback
        except Exception:
            pass

    return "main"


# ─────────────────── BUILDPACK AUTO-DETECT ────────────────────
def detect_start_command(app_dir: Path, app_port: int = 8080) -> str:
    """
    Buildpack-style auto-detection of the start command.
    Priority order:
      1. Procfile  (web: ...)
      2. app.py    → uvicorn app:app or python app.py
      3. main.py   → uvicorn main:app or python main.py
      4. bot.py / run.py / server.py / start.py / index.py
      5. src/main.py
      6. Any *.py with if __name__ == '__main__'
      7. Default: python main.py
    """
    # 1. Check Procfile
    procfile = app_dir / "Procfile"
    if procfile.exists():
        for line in procfile.read_text().splitlines():
            line = line.strip()
            if line.startswith("web:"):
                cmd = line[4:].strip()
                # Replace any hardcoded port with the assigned app_port
                cmd = re.sub(r"--port\s+\d+", f"--port {app_port}", cmd)
                logger.info(f"Buildpack: Procfile web → {cmd}")
                return cmd

    # 2. FastAPI/Flask app.py heuristics
    app_py = app_dir / "app.py"
    if app_py.exists():
        content = app_py.read_text(errors="replace")
        if "FastAPI" in content or "flask" in content.lower():
            m = re.search(r"^(\w+)\s*=\s*(?:FastAPI|Flask)\(", content, re.MULTILINE)
            var = m.group(1) if m else "app"
            cmd = f"uvicorn app:{var} --host 0.0.0.0 --port {app_port}"
        else:
            cmd = "python app.py"
        logger.info(f"Buildpack: app.py detected → {cmd}")
        return cmd

    # 3. main.py
    main_py = app_dir / "main.py"
    if main_py.exists():
        content = main_py.read_text(errors="replace")
        if "FastAPI" in content or "flask" in content.lower():
            m = re.search(r"^(\w+)\s*=\s*(?:FastAPI|Flask)\(", content, re.MULTILINE)
            var = m.group(1) if m else "app"
            cmd = f"uvicorn main:{var} --host 0.0.0.0 --port {app_port}"
        else:
            cmd = "python main.py"
        logger.info(f"Buildpack: main.py detected → {cmd}")
        return cmd

    # 4. Common entry point files
    for fname, default_cmd in [
        ("bot.py",    "python bot.py"),
        ("run.py",    "python run.py"),
        ("server.py", "python server.py"),
        ("start.py",  "python start.py"),
        ("index.py",  "python index.py"),
    ]:
        if (app_dir / fname).exists():
            logger.info(f"Buildpack: {fname} detected → {default_cmd}")
            return default_cmd

    # 5. Nested src/main.py
    if (app_dir / "src" / "main.py").exists():
        return "python src/main.py"

    # 6. Scan for any .py with __main__
    for pyfile in sorted(app_dir.glob("*.py")):
        try:
            txt = pyfile.read_text(errors="replace")
            if '__name__ == "__main__"' in txt or "__name__ == '__main__'" in txt:
                cmd = f"python {pyfile.name}"
                logger.info(f"Buildpack: __main__ found in {pyfile.name} → {cmd}")
                return cmd
        except Exception:
            pass

    logger.info("Buildpack: No entry point found, defaulting to python main.py")
    return "python main.py"


# ─────────────────────────── AI HELPERS ───────────────────────
async def call_claude_ai(prompt: str, system: str = "") -> str:
    """
    Call Claude claude-sonnet-4-20250514 via Anthropic API.
    Replaces the broken NVIDIA Nemotron endpoint.
    """
    if not ANTHROPIC_API_KEY:
        return "⚠️ AI unavailable: ANTHROPIC_API_KEY not set in environment variables."

    messages = [{"role": "user", "content": prompt}]
    body: Dict[str, Any] = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2048,
        "messages": messages,
    }
    if system:
        body["system"] = system

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            # Extract text from content blocks
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block["text"]
            return "⚠️ AI returned an empty response."
    except httpx.TimeoutException:
        return "⚠️ AI response timed out. Please try again."
    except httpx.HTTPStatusError as e:
        logger.error(f"Claude API HTTP error: {e.response.status_code} {e.response.text}")
        return f"⚠️ AI service error ({e.response.status_code}). Please try again."
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"⚠️ AI service temporarily unavailable: {str(e)}"


def build_context_prompt(history: List[dict], user_message: str) -> str:
    """Build a formatted prompt with chat history."""
    parts = ["--- Conversation History ---"]
    for msg in history[-10:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        parts.append(f"{role}: {msg['content']}")
    parts.append(f"\n--- Current Message ---\nUser: {user_message}")
    return "\n".join(parts)


# ─────────────────────────── DEPLOYMENT ───────────────────────
running_processes: Dict[str, subprocess.Popen] = {}


def build_app_url(app_port: int) -> str:
    """
    Public URL where the user's deployed app is reachable.
    On Koyeb / any cloud with a single public hostname, each app
    gets a unique port. The host is derived from BACKEND_URL.
    e.g.  https://my-vexo.koyeb.app:9001
    """
    host = BACKEND_URL.rstrip("/")
    # Strip default port if present so we can append the app port cleanly
    host = re.sub(r":\d+$", "", host)
    return f"{host}:{app_port}"


def build_project_url(app_id: str) -> str:
    """Vexo dashboard URL for viewing the project (logs, status, etc.)"""
    return f"{BACKEND_URL}/apps/{app_id}"


async def clone_and_run(
    app_id: str,
    repo_url: str,
    branch: str,
    env_vars: dict,
    user_id: str,
    database,
):
    """
    Clone GitHub repo, auto-detect branch, auto-detect start command
    (buildpack), allocate a port, install deps, and run the app.
    Returns both app_url (live app) and project_url (Vexo dashboard).
    """
    app_dir = APPS_DIR / app_id
    log_file = app_dir / "vexo.log"

    def write_log(line: str):
        with open(log_file, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {line}\n")

    try:
        # ── Step 1: Auto-detect branch ──
        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {"status": "detecting_branch", "updated_at": datetime.now(timezone.utc)}},
        )
        write_log(f"🔍 Auto-detecting default branch for {repo_url} ...")
        detected_branch = await detect_default_branch(repo_url)

        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {
                "branch": detected_branch,
                "status": "cloning",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        write_log(f"✓ Branch: {detected_branch}")

        # ── Step 2: Clone ──
        write_log(f"📦 Cloning {repo_url} branch={detected_branch} ...")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", detected_branch, repo_url, str(app_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {result.stderr}")
        write_log("✓ Clone successful")

        # ── Step 3: Allocate a port for this app ──
        app_port = allocate_port()
        write_log(f"✓ Allocated port: {app_port}")

        # ── Step 4: Buildpack auto-detect start command (uses the port) ──
        start_command = detect_start_command(app_dir, app_port=app_port)
        write_log(f"✓ Buildpack start command: {start_command}")

        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {
                "start_command": start_command,
                "app_port": app_port,
                "status": "installing",
                "updated_at": datetime.now(timezone.utc),
            }},
        )

        # ── Step 5: Create venv ──
        venv_dir = app_dir / ".venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        write_log("✓ Virtual environment created")

        # ── Step 6: Install dependencies ──
        req_file = app_dir / "requirements.txt"
        if req_file.exists():
            pip = venv_dir / "bin" / "pip"
            result = subprocess.run(
                [str(pip), "install", "-r", str(req_file)],
                capture_output=True, text=True, timeout=300, cwd=str(app_dir),
            )
            write_log(f"pip: {result.stdout[-300:] if result.stdout else 'done'}")
            if result.returncode != 0:
                write_log(f"pip warning: {result.stderr[-300:]}")
        else:
            write_log("⚠️  No requirements.txt found — skipping pip install")

        # ── Step 7: Start app ──
        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
        )
        write_log(f"🚀 Starting: {start_command}")

        proc_env = os.environ.copy()
        proc_env.update(env_vars)
        proc_env["PORT"] = str(app_port)   # expose PORT env var too (Heroku-style)

        python_bin  = str(venv_dir / "bin" / "python")
        uvicorn_bin = str(venv_dir / "bin" / "uvicorn")
        cmd_str = start_command
        if cmd_str.startswith("python "):
            cmd_str = python_bin + cmd_str[6:]
        elif cmd_str.startswith("uvicorn "):
            cmd_str = uvicorn_bin + cmd_str[7:]

        cmd_parts = cmd_str.split()

        with open(log_file, "a") as log_f:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=str(app_dir),
                env=proc_env,
                stdout=log_f,
                stderr=log_f,
            )
        running_processes[app_id] = proc
        write_log(f"✓ Process started PID={proc.pid}")
        logger.info(f"App {app_id} started PID={proc.pid} port={app_port}")

        # ── Step 8: Store both URLs in DB ──
        app_url     = build_app_url(app_port)      # live running app
        project_url = build_project_url(app_id)    # Vexo dashboard for this project

        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {
                "app_url":     app_url,
                "project_url": project_url,
                "updated_at":  datetime.now(timezone.utc),
            }},
        )
        write_log(f"✅ Deployment complete!")
        write_log(f"   🌐 App URL      : {app_url}")
        write_log(f"   📋 Project URL  : {project_url}")
        write_log(f"   📄 Logs URL     : {project_url}/logs")

    except Exception as e:
        error_msg = traceback.format_exc()
        write_log(f"DEPLOYMENT ERROR: {error_msg}")
        await database["deployments"].update_one(
            {"app_id": app_id},
            {"$set": {"status": "failed", "error": str(e), "updated_at": datetime.now(timezone.utc)}},
        )
        logger.error(f"Deploy {app_id} failed: {e}")
        # Free the port so it can be reused
        try:
            _allocated_ports.discard(app_port)
        except NameError:
            pass


# ─────────────────────────── STARTUP ──────────────────────────
@app.on_event("startup")
async def startup():
    global mongo_client, db
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client["vexo"]
    await db["users"].create_index("email", unique=True)
    await db["users"].create_index("verification_token", sparse=True)
    await db["users"].create_index("reset_token", sparse=True)
    await db["deployments"].create_index("app_id", unique=True)
    await db["deployments"].create_index("user_id")
    await db["ai_chats"].create_index("user_id")

    # Restore allocated ports from running/queued deployments in DB
    cursor = db["deployments"].find(
        {"status": {"$in": ["running", "installing", "cloning", "detecting_branch", "queued"]},
         "app_port": {"$exists": True}},
        {"app_port": 1},
    )
    async for doc in cursor:
        port = doc.get("app_port")
        if port:
            _allocated_ports.add(port)
    logger.info(f"✅ Vexo platform v2.0 started — port {VEXO_PORT} — Claude AI ready — {len(_allocated_ports)} ports restored")


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
    return {
        "status": "ok",
        "platform": "Vexo",
        "version": "2.0.0",
        "ai": "Claude claude-sonnet-4-20250514",
        "time": datetime.now(timezone.utc).isoformat(),
    }


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
    return HTMLResponse(f"""<!DOCTYPE html><html>
<head><meta http-equiv="refresh" content="3;url={FRONTEND_URL}/login?verified=1"></head>
<body style="font-family:'Courier New',monospace;background:#0a0a0f;color:#7c6fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;">
<div style="text-align:center;border:1px solid #2a2a4a;padding:48px;border-radius:12px;background:#12121f;">
  <div style="font-size:48px">✓</div>
  <h2 style="color:#a0ffa0;margin:16px 0">Email Verified!</h2>
  <p style="color:#6060a0">Your Vexo account is now active. Redirecting to login...</p>
</div></body></html>""")


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
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}

    if user.get("auth_provider") == "google":
        return {"message": "This account uses Google sign-in. No password to reset."}

    reset_token = secrets.token_urlsafe(32)
    await database["users"].update_one(
        {"_id": user["_id"]},
        {"$set": {
            "reset_token": reset_token,
            "reset_token_expires": datetime.now(timezone.utc) + timedelta(hours=1),
        }},
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
        {"$set": {
            "verification_token": new_token,
            "verification_token_expires": datetime.now(timezone.utc) + timedelta(hours=24),
        }},
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
    return await oauth.google.authorize_redirect(request, GOOGLE_REDIRECT_URI)


@app.get("/auth/google/callback", tags=["auth"])
async def google_callback(request: Request, database=Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(400, f"Google OAuth failed: {str(e)}")

    user_info = token.get("userinfo")
    if not user_info:
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

    existing = await database["users"].find_one({"email": email})
    if existing:
        user_id = existing["_id"]
        await database["users"].update_one(
            {"_id": user_id},
            {"$set": {
                "name": name,
                "avatar": avatar,
                "google_id": google_id,
                "is_verified": True,
                "auth_provider": "google",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
    else:
        user_id = str(uuid.uuid4())
        await database["users"].insert_one({
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
        })

    jwt_token = create_jwt({"sub": user_id, "email": email, "name": name})
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

    # Normalize repo URL
    repo_url = normalize_repo_url(body.repo_url)

    # Use user-specified branch or leave empty (auto-detect happens in background task)
    branch = body.branch or ""  # Will be auto-detected in clone_and_run

    deployment_doc = {
        "app_id": app_id,
        "user_id": current_user["_id"],
        "app_name": body.app_name,
        "repo_url": repo_url,
        "branch": branch or "auto-detecting...",
        "start_command": "auto-detecting...",
        "app_port": None,
        "env_vars": body.env_vars,
        "status": "queued",
        "error": None,
        "app_url":     None,                                   # set once running
        "project_url": f"{BACKEND_URL}/apps/{app_id}",        # available immediately
        "logs_url":    f"{BACKEND_URL}/apps/{app_id}/logs",   # available immediately
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "started_at": None,
    }
    await database["deployments"].insert_one(deployment_doc)

    background_tasks.add_task(
        clone_and_run,
        app_id,
        repo_url,
        branch,
        body.env_vars,
        current_user["_id"],
        database,
    )

    return {
        "message": "🚀 Deployment queued! Branch and start command will be auto-detected.",
        "app_id": app_id,
        "app_name": body.app_name,
        "repo_url": repo_url,
        "status": "queued",
        # ── URLs returned immediately ──────────────────────────────────────
        # app_url      → live running app (available once status=running)
        # project_url  → Vexo dashboard page for this deployment
        # logs_url     → tail logs (available immediately)
        # stream_url   → SSE live log stream
        # status_url   → poll deployment status
        "app_url":     None,   # set after app starts (check status_url)
        "project_url": f"{BACKEND_URL}/apps/{app_id}",
        "logs_url":    f"{BACKEND_URL}/apps/{app_id}/logs",
        "stream_url":  f"{BACKEND_URL}/apps/{app_id}/stream-logs",
        "status_url":  f"{BACKEND_URL}/apps/{app_id}",
        "note": "app_url will be set once your app is running. Poll status_url to check progress.",
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
        "app_id":      app_id,
        "app_name":    app_doc.get("app_name"),
        "status":      app_doc.get("status"),
        "logs":        "".join(tail),
        "lines":       len(tail),
        "total_lines": len(all_lines),
        # ── URLs ──
        "app_url":     app_doc.get("app_url"),        # live deployed app
        "project_url": app_doc.get("project_url"),    # Vexo dashboard
        "logs_url":    f"{BACKEND_URL}/apps/{app_id}/logs",
        "stream_url":  f"{BACKEND_URL}/apps/{app_id}/stream-logs",
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

    # Free the port so it can be reused by a new deployment
    freed_port = app_doc.get("app_port")
    if freed_port:
        _allocated_ports.discard(freed_port)

    await database["deployments"].update_one(
        {"app_id": app_id},
        {"$set": {"status": "stopped", "updated_at": datetime.now(timezone.utc)}},
    )
    return {
        "message": f"App {app_id} stopped",
        "app_id":      app_id,
        "app_name":    app_doc.get("app_name"),
        "project_url": app_doc.get("project_url"),
        "logs_url":    f"{BACKEND_URL}/apps/{app_id}/logs",
    }


# ═══════════════════════════════════════════════════════════════
#                       AI ROUTES  (now using Claude)
# ═══════════════════════════════════════════════════════════════

VEXO_SYSTEM = (
    "You are Vexo AI, an expert developer assistant inside the Vexo cloud platform. "
    "You help users deploy Python apps (Telegram bots, FastAPI APIs, scripts), fix errors, "
    "analyze logs, and generate project code. Be concise, practical, and friendly. "
    "Format code with proper Python syntax."
)


@app.post("/ai/chat", tags=["ai"])
@limiter.limit("30/minute")
async def ai_chat(
    request: Request,
    body: AIChatRequest,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    user_id = current_user["_id"]

    chat_doc = await database["ai_chats"].find_one({"user_id": user_id})
    history = chat_doc.get("messages", []) if chat_doc else []

    prompt = build_context_prompt(history, body.message)
    ai_response = await call_claude_ai(prompt, system=VEXO_SYSTEM)

    history.append({"role": "user", "content": body.message})
    history.append({"role": "assistant", "content": ai_response})
    history = history[-20:]

    await database["ai_chats"].update_one(
        {"user_id": user_id},
        {
            "$set": {"messages": history, "updated_at": datetime.now(timezone.utc)},
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )

    return {"response": ai_response, "message_count": len(history)}


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
            logs = "".join(all_lines[-50:])
        else:
            logs = "No logs found"

    prompt = (
        f"Analyze these error logs from a deployed Python application and provide:\n"
        f"1. Root cause analysis\n2. Step-by-step fix\n3. Fixed code snippet if applicable\n"
        f"4. Prevention advice\n\n=== LOGS ===\n{logs[-3000:]}\n=== END ==="
    )

    response = await call_claude_ai(prompt, system=VEXO_SYSTEM)
    return {"app_id": body.app_id, "analysis": response, "logs_analyzed": logs[-500:]}


@app.post("/ai/generate-project", tags=["ai"])
@limiter.limit("10/minute")
async def ai_generate_project(
    request: Request,
    body: AIGenerateProjectRequest,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    prompt = (
        f"Generate a complete, production-ready Python project:\n\n"
        f"PROJECT: {body.description}\n\n"
        f"Output EXACTLY:\n\n"
        f"## main.py\n```python\n[complete code]\n```\n\n"
        f"## requirements.txt\n```\n[packages]\n```\n\n"
        f"## .env.example\n```\n[env vars]\n```\n\n"
        f"## README.md\n[setup + run instructions]\n\n"
        f"Make it complete, functional, ready to deploy on Vexo. Include error handling and logging."
    )

    response = await call_claude_ai(prompt, system=VEXO_SYSTEM)

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

    return {"description": body.description, "generated_code": response}


@app.post("/ai/analyze-logs", tags=["ai"])
@limiter.limit("20/minute")
async def ai_analyze_logs(
    request: Request,
    body: AIAnalyzeLogsRequest,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    prompt = (
        f"Analyze these application logs:\n\n=== LOGS ===\n{body.logs[-5000:]}\n=== END ===\n\n"
        f"Provide:\n🔍 Issues Detected\n⚡ Severity (Critical/Warning/Info)\n"
        f"🛠️ Suggested Fixes\n📊 Performance Notes\n✅ Health Summary"
    )

    response = await call_claude_ai(prompt, system=VEXO_SYSTEM)
    return {"analysis": response, "log_lines": len(body.logs.splitlines())}


# ═══════════════════════════════════════════════════════════════
#                     LIVE LOG STREAMING (SSE)
# ═══════════════════════════════════════════════════════════════

@app.get("/apps/{app_id}/stream-logs", tags=["deployment"])
async def stream_logs(
    app_id: str,
    current_user: dict = Depends(get_current_user),
    database=Depends(get_db),
):
    app_doc = await database["deployments"].find_one(
        {"app_id": app_id, "user_id": current_user["_id"]}
    )
    if not app_doc:
        raise HTTPException(404, "App not found")

    log_file = APPS_DIR / app_id / "vexo.log"

    async def event_generator():
        last_pos = 0
        for _ in range(300):
            if log_file.exists():
                with open(log_file, "r", errors="replace") as f:
                    f.seek(last_pos)
                    new_content = f.read()
                    last_pos = f.tell()
                if new_content:
                    for line in new_content.splitlines():
                        yield f"data: {json.dumps({'log': line})}\n\n"
            await asyncio.sleep(1)
        yield 'data: {"log": "[stream ended]"}\n\n'

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════
#                        UI PAGES
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def landing_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vexo — Cloud Platform for Python Apps</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#07070f;--surface:#0f0f1e;--border:#1e1e3f;--accent:#7c6fff;--accent2:#a06fff;--text:#e0e0ff;--muted:#5a5a8a;--green:#4fff9f;--red:#ff6f6f;}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(124,111,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(124,111,255,.04) 1px,transparent 1px);background-size:60px 60px;pointer-events:none;z-index:0;}
nav{position:fixed;top:0;left:0;right:0;z-index:100;display:flex;align-items:center;justify-content:space-between;padding:18px 48px;border-bottom:1px solid var(--border);background:rgba(7,7,15,.85);backdrop-filter:blur(12px);}
.logo{font-family:'Space Mono',monospace;font-size:22px;font-weight:700;letter-spacing:5px;color:var(--accent);text-decoration:none}
.nav-links{display:flex;gap:12px}
.btn{padding:10px 22px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s;text-decoration:none;letter-spacing:.5px;font-family:'Space Mono',monospace;}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--muted)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:var(--accent);border:1px solid var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent2);transform:translateY(-1px)}
hero{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:120px 24px 80px;position:relative;z-index:1;}
.badge{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--border);border-radius:100px;padding:6px 16px;font-size:11px;letter-spacing:2px;color:var(--muted);font-family:'Space Mono',monospace;margin-bottom:32px;}
.badge::before{content:'';width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
h1{font-size:clamp(40px,7vw,88px);font-weight:300;line-height:1.05;letter-spacing:-2px;margin-bottom:24px;}
h1 strong{font-weight:700;background:linear-gradient(135deg,var(--accent),var(--accent2),#ff9fff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.sub{max-width:520px;color:var(--muted);font-size:17px;line-height:1.7;margin-bottom:48px}
.hero-btns{display:flex;gap:14px;flex-wrap:wrap;justify-content:center;margin-bottom:80px}
.btn-lg{padding:14px 32px;font-size:14px}
.terminal{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px 28px;max-width:600px;width:100%;text-align:left;font-family:'Space Mono',monospace;font-size:13px;}
.t-bar{display:flex;gap:6px;margin-bottom:18px}
.t-dot{width:10px;height:10px;border-radius:50%}
.t-r{background:#ff5f56}.t-y{background:#ffbd2e}.t-g{background:#27c93f}
.t-line{color:var(--muted);line-height:2}.t-line .cmd{color:var(--accent)}.t-line .out{color:var(--green)}.t-line .dim{color:#333}
.features{max-width:1100px;margin:0 auto;padding:80px 24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;position:relative;z-index:1;}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:28px;transition:border-color .2s,transform .2s;}
.card:hover{border-color:var(--accent);transform:translateY(-3px)}
.card-icon{font-size:28px;margin-bottom:16px}
.card h3{font-family:'Space Mono',monospace;font-size:14px;letter-spacing:1px;color:var(--accent);margin-bottom:10px}
.card p{color:var(--muted);font-size:14px;line-height:1.7}
footer{text-align:center;padding:40px;border-top:1px solid var(--border);color:var(--muted);font-family:'Space Mono',monospace;font-size:11px;letter-spacing:1px;position:relative;z-index:1;}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">VEXO</a>
  <div class="nav-links">
    <a href="/docs" class="btn btn-ghost">API Docs</a>
    <a href="/register" class="btn btn-primary">Get Started →</a>
  </div>
</nav>
<hero>
  <div class="badge">🟢 PLATFORM ONLINE · AI POWERED BY CLAUDE</div>
  <h1>Deploy Python apps<br><strong>in seconds</strong></h1>
  <p class="sub">Push from GitHub. Vexo auto-detects your branch and builds your app — no config needed. AI assistant powered by Claude helps you fix errors and generate code.</p>
  <div class="hero-btns">
    <a href="/docs" class="btn btn-primary btn-lg">Start Deploying</a>
    <a href="/docs" class="btn btn-ghost btn-lg">View API →</a>
  </div>
  <div class="terminal">
    <div class="t-bar"><div class="t-dot t-r"></div><div class="t-dot t-y"></div><div class="t-dot t-g"></div></div>
    <div class="t-line"><span class="cmd">POST</span> /deploy</div>
    <div class="t-line dim">{"repo_url": "github.com/you/bot", "app_name": "my-bot"}</div>
    <div class="t-line"></div>
    <div class="t-line"><span class="out">✓ Auto-detected branch: main</span></div>
    <div class="t-line"><span class="out">✓ Buildpack: python bot.py</span></div>
    <div class="t-line"><span class="out">✓ Installing requirements...</span></div>
    <div class="t-line"><span class="out">✓ App running → PID 3847</span></div>
    <div class="t-line"></div>
    <div class="t-line"><span class="cmd">logs_url:</span> <span class="out">https://your-app.koyeb.app/apps/a1b2/logs</span></div>
  </div>
</hero>
<div class="features">
  <div class="card"><div class="card-icon">🔍</div><h3>AUTO BRANCH DETECT</h3><p>No need to specify a branch. Vexo queries the GitHub API and picks your default branch automatically.</p></div>
  <div class="card"><div class="card-icon">🚀</div><h3>BUILDPACK DEPLOY</h3><p>Vexo reads your Procfile, detects FastAPI/Flask/bot entry points, and picks the right start command.</p></div>
  <div class="card"><div class="card-icon">🤖</div><h3>CLAUDE AI ASSISTANT</h3><p>Powered by Anthropic Claude. Ask it to fix errors, explain logs, generate full project code, or improve your codebase.</p></div>
  <div class="card"><div class="card-icon">📊</div><h3>LIVE LOGS + URL</h3><p>Every deployment gets a logs URL and project URL back immediately. Stream logs in real-time via SSE.</p></div>
  <div class="card"><div class="card-icon">🔐</div><h3>SECURE AUTH</h3><p>JWT authentication with Google OAuth, email verification, and password reset — all production-grade.</p></div>
  <div class="card"><div class="card-icon">⚡</div><h3>PROJECT GENERATOR</h3><p>Describe what you want — "Telegram bot for movies" — and Claude generates main.py, requirements.txt, and docs.</p></div>
</div>
<footer>VEXO PLATFORM v2.0 · FASTAPI + MONGODB · AI BY ANTHROPIC CLAUDE</footer>
</body></html>""")


@app.get("/reset-password", response_class=HTMLResponse, tags=["ui"])
async def reset_password_page(token: str = ""):
    return HTMLResponse(f"""<!DOCTYPE html><html>
<head><meta charset="UTF-8"><title>Reset Password — Vexo</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#07070f;color:#e0e0ff;font-family:'DM Sans',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background-image:linear-gradient(rgba(124,111,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(124,111,255,.04) 1px,transparent 1px);background-size:60px 60px;}}
.card{{background:#0f0f1e;border:1px solid #1e1e3f;border-radius:16px;padding:48px;width:100%;max-width:420px}}
.logo{{font-family:'Space Mono',monospace;font-size:20px;font-weight:700;letter-spacing:5px;color:#7c6fff;text-align:center;margin-bottom:32px}}
h2{{font-size:22px;margin-bottom:8px;color:#ffa06f}}
p{{color:#5a5a8a;font-size:14px;margin-bottom:28px;line-height:1.6}}
input{{width:100%;background:#07070f;border:1px solid #1e1e3f;color:#e0e0ff;padding:12px 16px;border-radius:8px;font-size:14px;margin-bottom:16px;font-family:'DM Sans',sans-serif;transition:border-color .2s}}
input:focus{{outline:none;border-color:#7c6fff}}
button{{width:100%;background:linear-gradient(135deg,#ff6f6f,#ffa06f);border:none;color:#fff;padding:13px;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;font-family:'Space Mono',monospace;letter-spacing:1px}}
.msg{{margin-top:16px;padding:12px;border-radius:8px;font-size:13px;text-align:center;display:none}}
.msg.ok{{background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f}}
.msg.err{{background:#1f0a0a;border:1px solid #ff6f6f;color:#ff6f6f}}
</style></head>
<body><div class="card">
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
    const r = await fetch('/auth/reset-password', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token,new_password:pwd}})}});
    const d = await r.json();
    if (r.ok) {{ showMsg('✓ Password reset! Redirecting...', true); setTimeout(() => window.location.href='/login?reset=1', 2000); }}
    else {{ showMsg(d.detail || 'Error', false); }}
  }} catch(e) {{ showMsg('Network error', false); }}
}}
function showMsg(t, ok) {{ const el=document.getElementById('msg'); el.textContent=t; el.className='msg '+(ok?'ok':'err'); el.style.display='block'; }}
</script></body></html>""")


@app.get("/login", response_class=HTMLResponse, tags=["ui"])
async def login_page(verified: int = 0, reset: int = 0):
    notice = ""
    if verified:
        notice = '<div style="background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f;padding:12px;border-radius:8px;margin-bottom:20px;font-size:13px;text-align:center">✓ Email verified! You can now log in.</div>'
    if reset:
        notice = '<div style="background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f;padding:12px;border-radius:8px;margin-bottom:20px;font-size:13px;text-align:center">✓ Password reset successfully!</div>'
    return HTMLResponse(f"""<!DOCTYPE html><html>
<head><meta charset="UTF-8"><title>Login — Vexo</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#07070f;color:#e0e0ff;font-family:'DM Sans',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background-image:linear-gradient(rgba(124,111,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(124,111,255,.04) 1px,transparent 1px);background-size:60px 60px;}}
.card{{background:#0f0f1e;border:1px solid #1e1e3f;border-radius:16px;padding:48px;width:100%;max-width:420px}}
.logo{{font-family:'Space Mono',monospace;font-size:20px;font-weight:700;letter-spacing:5px;color:#7c6fff;text-align:center;margin-bottom:32px}}
h2{{font-size:22px;margin-bottom:24px}}
input{{width:100%;background:#07070f;border:1px solid #1e1e3f;color:#e0e0ff;padding:12px 16px;border-radius:8px;font-size:14px;margin-bottom:14px;font-family:'DM Sans',sans-serif;transition:border-color .2s}}
input:focus{{outline:none;border-color:#7c6fff}}
button.main{{width:100%;background:#7c6fff;border:none;color:#fff;padding:13px;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;font-family:'Space Mono',monospace;letter-spacing:1px;margin-bottom:12px}}
button.google{{width:100%;background:#fff;border:none;color:#1a1a2e;padding:12px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px}}
.divider{{text-align:center;color:#2a2a4a;font-size:12px;margin:16px 0;position:relative}}
.divider::before,.divider::after{{content:'';position:absolute;top:50%;width:42%;height:1px;background:#1e1e3f}}
.divider::before{{left:0}}.divider::after{{right:0}}
.links{{display:flex;justify-content:space-between;margin-top:20px;font-size:13px;color:#5a5a8a}}
.links a{{color:#7c6fff;text-decoration:none}}
.msg{{margin-top:14px;padding:12px;border-radius:8px;font-size:13px;text-align:center;display:none}}
.msg.ok{{background:#0a1f12;border:1px solid #4fff9f;color:#4fff9f}}
.msg.err{{background:#1f0a0a;border:1px solid #ff6f6f;color:#ff6f6f}}
</style></head>
<body><div class="card">
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
  <div class="links"><a href="/register">Create account</a><a href="#" onclick="forgotPwd()">Forgot password?</a></div>
  <div class="msg" id="msg"></div>
</div>
<script>
const params = new URLSearchParams(window.location.search);
if (params.get('token')) {{ localStorage.setItem('vexo_token', params.get('token')); window.location.href = '/dashboard'; }}
async function doLogin() {{
  const email = document.getElementById('email').value;
  const pass = document.getElementById('password').value;
  if (!email || !pass) {{ showMsg('Fill in all fields', false); return
