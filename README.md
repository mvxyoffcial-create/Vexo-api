# VEXO Platform

> Cloud platform to deploy & manage Python apps from GitHub, with built-in AI assistant (NVIDIA Nemotron)

---

## 🚀 Features

- **Deploy from GitHub** — clone, venv, install, run
- **AI Chat** (NVIDIA Nemotron) with MongoDB memory
- **Error Fixer** — paste logs → get fix
- **Project Generator** — describe → get code
- **Log Analyzer** — smart log parsing
- **Google OAuth** — sign in with Google
- **Email Verification** — sent to user's inbox
- **Password Reset** — token-based, expires in 1h
- **Live Log Streaming** — SSE endpoint
- **Rate Limiting** — abuse protection

---

## 📁 File Structure

```
vexo/
├── main.py            ← entire backend (single file)
├── requirements.txt
├── Procfile           ← for Koyeb
├── .env.example       ← copy to .env and fill in
└── README.md
```

---

## ⚙️ Local Setup

```bash
# 1. Clone this project
git clone https://github.com/YOU/vexo
cd vexo

# 2. Create virtual env
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env with your values

# 5. Run
uvicorn main:app --reload --port 8000
```

Open: http://localhost:8000

---

## 🌐 Koyeb Deployment

1. Push this repo to GitHub
2. Go to [koyeb.com](https://koyeb.com) → New Service → GitHub
3. Select your repo
4. Set:
   - **Build command**: `pip install -r requirements.txt`
   - **Run command**: `uvicorn main:app --host 0.0.0.0 --port 8000`
   - **Health check**: `/health`
5. Add all environment variables from `.env.example`
6. Deploy ✅

---

## 🔐 Google OAuth Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project → APIs & Services → Credentials
3. Create **OAuth 2.0 Client ID** (Web application)
4. Add Authorized redirect URI:
   ```
   https://YOUR_APP.koyeb.app/auth/google/callback
   ```
5. Copy Client ID and Secret to `.env`

---

## 📧 Gmail App Password Setup

1. Go to myaccount.google.com → Security
2. Enable 2-Step Verification
3. Search "App passwords" → Generate one for "Mail"
4. Use that 16-char password as `SMTP_PASS`

---

## 📡 API Endpoints

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Register with email+password |
| GET | `/auth/verify-email?token=` | Verify email (link from inbox) |
| POST | `/auth/login` | Login → get JWT |
| GET | `/auth/google` | Start Google OAuth flow |
| GET | `/auth/google/callback` | Google OAuth callback |
| POST | `/auth/forgot-password` | Send reset email |
| POST | `/auth/reset-password` | Reset with token |
| GET | `/auth/resend-verification?email=` | Resend verification |
| GET | `/auth/me` | Get current user |

### Deployment
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/deploy` | Deploy GitHub repo |
| GET | `/apps` | List your apps |
| GET | `/apps/{id}` | App details |
| GET | `/apps/{id}/logs` | Tail logs |
| GET | `/apps/{id}/stream-logs` | Live log SSE stream |
| DELETE | `/apps/{id}` | Stop app |

### AI
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ai/chat` | Chat with AI assistant |
| GET | `/ai/history` | Get chat history |
| DELETE | `/ai/history` | Clear chat history |
| POST | `/ai/fix-error` | Fix app error with logs |
| POST | `/ai/generate-project` | Generate full project |
| POST | `/ai/analyze-logs` | Analyze logs with AI |

---

## 🗄️ MongoDB Collections

| Collection | Purpose |
|------------|---------|
| `users` | User accounts, tokens |
| `deployments` | App deployments |
| `ai_chats` | AI conversation history |

---

## 🔒 Security Notes

- JWT tokens expire in 7 days
- Email verification required before deploying
- Rate limiting on all sensitive endpoints
- Password hashed with bcrypt
- Reset tokens expire in 1 hour
- Verification tokens expire in 24 hours

---

## 📞 Support

Visit `/docs` for interactive Swagger UI.

