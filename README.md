# Meeting Assistant

A tiny always-on-top window for Zoom / Google Meet. It **listens to the other people**
in the call, shows what they say as text, drafts a **short answer you can read out loud**,
and can produce a **meeting report** (summary + action items + alerts) at the end.
Built for when your English is stronger written than spoken.

- **Ears:** **Deepgram live** (default) — instant, word-by-word captions, with a live preview
  line. Can fall back to **Groq Whisper** (free-forever, ~1.5s) by a toggle — see below.
- **Brain:** Groq / Llama — drafts short spoken replies + the end-of-meeting report.
- Nothing is saved unless you click *Save report*.

---

## Two ways to run

**A) The app (easiest — no Python needed)**
Double-click the **"Meeting Assistant"** shortcut on your Desktop, or run
**`dist\MeetingAssistant\MeetingAssistant.exe`** directly.
Keep the **`.env`** file (it holds your keys) inside `dist\MeetingAssistant\` next to the exe.
You can move the whole `MeetingAssistant` folder anywhere — just fix the shortcut's target
afterwards (right-click → Properties).

**B) From source (Python)**
```
pip install -r requirements.txt
run.bat          (or:  python meeting_assistant.py)
```

**Groq key** — already set in `.env`. Need a new one? https://console.groq.com/keys
(free, no credit card) → paste it as `GROQ_API_KEY=...`.

---

## How to use it in a call

1. **Earbuds/headphones?** Plug them in **before** launching (it grabs your default sound
   device at startup). It captures the call's *output*, so it never hears your own mic.
2. Join Zoom / Meet. As people talk, their words fill the **top box** (~1.5s behind).
3. When someone asks *you* something → press **`Ctrl + Space`** (or click **Answer**).
   The reply **streams** into the green box — read it out. (Buy a beat: *"Let me think…"*)
4. At the end, click **📋 Meeting Report** → get a Summary / Action Items / Alerts / Notes
   report you can **Save** (drops a `.md` file into the `reports\` folder) or **Copy**.

### Faster / shorter answers (templates)
- **Length buttons** — One-liner / Short / A bit more.
- **Quick replies** (instant, no AI): **Repeat?** / **Give me a sec** / **Agree** —
  click, or press **`Ctrl+1`** / **`Ctrl+2`** / **`Ctrl+3`**.

### Pausing
- **⏸ Stop** pauses listening without closing the app (and stops streaming audio to
  Deepgram, so it doesn't use credit). Click **▶ Resume** to start again.

---

## Speed & troubleshooting

- **How fast?** With **Deepgram** (default) it's near-instant — words appear in the grey
  *live preview* line as they're spoken, then commit to the transcript. With **Groq** it
  trails ~1.5s.
- **Switching engines:** use the **ENGINE** toggle at the top of the window
  (⚡ Deepgram = instant / Groq = free-forever, ~1.5s). It saves your choice and restarts
  automatically. If the Deepgram key is missing it auto-falls-back to Groq.
- **Delay growing over time (Groq mode)?** It auto-skips stale audio when the network lags,
  so it stays realtime instead of falling further behind.
- **Nothing appears?** Make sure sound is actually playing, and that you launched *after*
  plugging in your earbuds. Restart the app if you switched audio device.
- **Hotkeys dead?** Run as Administrator, or just use the on-screen buttons.
- **Speed vs accuracy knobs** (in `meeting_assistant.py`): `WINDOW_SECONDS` (lower = faster,
  more API calls), `STT_MODEL`, `GROQ_MODEL`. Rebuild the exe after changing (see below).

### Rebuilding the exe after code changes
```
pip install pyinstaller
pyinstaller --onedir --windowed --name MeetingAssistant ^
  --collect-all numpy --collect-all pyaudiowpatch --collect-all customtkinter ^
  --hidden-import websocket meeting_assistant.py
```
This builds a **one-folder** app at `dist\MeetingAssistant\` (more reliable than one-file —
no temp unpacking, faster start). Then copy `.env` into `dist\MeetingAssistant\` next to the
new `MeetingAssistant.exe`.

---

## ⚠️ Fair use

Great for standups, client calls, and team meetings. **Do not** use it in an interview or
assessment where AI help isn't allowed. Also: the `.env` holds your API key — don't share
the folder publicly.
