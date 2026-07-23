# AI Voice Sales Agent 📞 — Dee-Starix Techno

An outbound AI voice **sales consultant** built on **LiveKit**, **Sarvam AI**, **Groq**, and **Vobiz SIP trunking**.

It calls business prospects, opens with a sector-specific insight, diagnoses their operational pain in natural conversation, and closes on a demo — in **English, Hindi, or Kannada**, switching live mid-call.

## ✨ What it does

- **Sales-consultant persona** — diagnoses instead of pitching; permission-based opening; one question per turn; objection handling; closes on a next step, never a sale
- **8 built-in sector playbooks** — dental, real estate, diagnostic labs, automotive service, salons, coaching institutes, insurance/loans, hotels — each with real vocabulary, money math, and opening insights
- **Multilingual voice** — Sarvam `bulbul:v3` TTS + `saarika` STT; hot-swaps between English / Hindi / Kannada mid-call via a `set_language` tool
- **Ultra-fast LLM** — Groq running `llama-3.3-70b-versatile`
- **Web dashboard** — Next.js UI to dispatch calls with a target sector and per-call context
- **Call transfer** — hands off to a human number on request

---

## 🔑 Credentials you need (5 accounts)

| # | Service | Variables | Where to get them |
|---|---------|-----------|-------------------|
| 1 | **LiveKit Cloud** | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | [cloud.livekit.io](https://cloud.livekit.io) → Project → Settings → Keys |
| 2 | **Sarvam AI** | `SARVAM_API_KEY` | [dashboard.sarvam.ai](https://dashboard.sarvam.ai) |
| 3 | **Groq** | `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| 4 | **Deepgram** (optional, English-only fallback) | `DEEPGRAM_API_KEY` | [console.deepgram.com](https://console.deepgram.com) |
| 5 | **Vobiz** (SIP / telephony) | `VOBIZ_SIP_DOMAIN`, `VOBIZ_USERNAME`, `VOBIZ_PASSWORD`, `VOBIZ_OUTBOUND_NUMBER` | Your Vobiz account panel |

---

## 🛠️ Setup

### Prerequisites
- Python **3.10+**
- Node.js **18+**
- ~1 GB free RAM while running (the voice pipeline stalls under heavy memory pressure)

### 1. Clone & install (Python)

```powershell
git clone https://github.com/mrdiamondstar/AI-Voice.git
cd AI-Voice

python -m venv .venv
.venv\Scripts\activate          # Linux/Mac: source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. Configure credentials

```powershell
copy .env.example .env          # Linux/Mac: cp .env.example .env
```

Open `.env` and fill in **every** value from the credentials table above.

### 3. Create the SIP trunk (one-time)

Registers your Vobiz account as an outbound trunk inside LiveKit:

```powershell
python create_trunk.py
```

Copy the printed Trunk ID (`ST_xxxx`) into `.env` as both `VOBIZ_SIP_TRUNK_ID` and `OUTBOUND_TRUNK_ID`. Verify with:

```powershell
python list_trunks.py
```

### 4. Dashboard setup

```powershell
cd dashboard
npm install
copy .env.local.example .env.local   # fill in the same LiveKit creds + trunk ID
cd ..
```

---

## 🏃 Running (2 terminals, both must stay open)

**Terminal 1 — agent worker** (the brain; receives dispatches and speaks on calls):

```powershell
.venv\Scripts\activate
python agent.py start
```

Wait for `registered worker` in the logs before dispatching anything.

**Terminal 2 — dashboard:**

```powershell
cd dashboard
npm run dev
```

Open the printed URL (usually `http://localhost:3000`).

### Make a call

**From the dashboard:** enter the phone number with country code (`+91...`), pick a **Target sector** (or leave on Auto), optionally add context (business name, city, call volume), hit **Initiate Call**.

**From the CLI:**

```powershell
python make_call.py --to +91XXXXXXXXXX
```

---

## ⚙️ Customization

Everything lives in [config.py](config.py):

- `SYSTEM_PROMPT` — the consultant persona and behavioral rules
- `SECTOR_PLAYBOOKS` — per-sector vocabulary, pains, money math, objection handling; add your own sectors here (and to the dashboard dropdown in [CallDispatcher.tsx](dashboard/components/CallDispatcher.tsx))
- `AGENT_NAME` / `COMPANY_NAME` — identity (spell names phonetically for correct TTS pronunciation)
- `SARVAM_MODEL` / `SARVAM_VOICE` — voice engine (`bulbul:v3` with `pooja`/`rahul` recommended)
- `GROQ_MODEL` — LLM model

Restart `agent.py` after any change.

---

## 🔧 Troubleshooting

| Symptom | Cause & fix |
|---|---|
| **Call answers but silence** | The agent wasn't dispatched into the room. The dashboard routes handle this (`agentDispatchClient.createDispatch`); if you write your own dispatch code, remember `agent.py` uses **explicit dispatch** (`agent_name="outbound-caller"`) and never auto-joins rooms. |
| **`sip request timed out (408)`** | Call rang ~30s with no answer — not an error. Check the callee's phone, or the Vobiz call logs. |
| **`404 Not Found` on trunk** | `VOBIZ_SIP_TRUNK_ID` wrong. Run `python list_trunks.py`, fix `.env`. |
| **Agent speaks but stutters / connections time out** | Machine low on RAM. Close browser tabs; the VAD, WebRTC and TTS websockets all stall when the OS starts swapping. |
| **`model_decommissioned` (Groq)** | Update `GROQ_MODEL` in `config.py`/`.env` to a currently supported model. |
| **Emoji crash (`UnicodeEncodeError`) on Windows** | Already fixed — scripts force UTF-8 stdout. |
| **`Address already in use` (port 8081)** | Another `agent.py` is running. Kill it: `taskkill /F /IM python.exe` (Windows) or `pkill -f "python agent.py"`. |
| **Wrong language spoken** | STT starts on auto-detect; the agent locks the language via the `set_language` tool once the prospect chooses. Defaults live in `.env` (`SARVAM_LANGUAGE`, `SARVAM_VOICE`). |

---

## 📂 Project structure

```
agent.py                     # Worker: joins rooms, dials out, runs the voice pipeline
config.py                    # Persona, sector playbooks, model/voice settings
make_call.py                 # CLI dispatcher
create_trunk.py              # One-time SIP trunk creation
list_trunks.py               # List existing trunks
setup_trunk.py               # Update trunk credentials
transfer_call.md             # Notes on call transfer
dashboard/                   # Next.js web UI
  app/api/dispatch/route.ts  #   Single-call dispatch (room + SIP + agent dispatch)
  app/api/queue/route.ts     #   Bulk dialing
  components/CallDispatcher.tsx  # Call form (sector, context, voice)
```
