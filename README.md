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

## Requirements

- **Windows 10 / 11** — it captures your speaker output via **WASAPI loopback**
  (`PyAudioWPatch`), which is Windows-only. It will not run on macOS or Linux.
- A free **Groq** API key (the app asks you for it on first launch — see below).
  A **Deepgram** key is optional, for instant captions.
- **Python 3.10+** is needed **only if you run from source**. The downloadable app
  below bundles everything — no Python required.

## Download & run (no Python needed)

Get it from the **[Releases](https://github.com/noobster97/meeting-assistant/releases/latest)** page.

**Recommended — the installer:**
1. Download **`MeetingAssistant-Setup.exe`**.
2. Run it — installs in seconds, **no admin needed**, adds Start Menu + Desktop shortcuts.
3. **First launch** shows a small setup window — click *"Get a free Groq key"* (opens your
   browser, no credit card), paste the key, hit **Save & Start**. Remembered for next time.

**Or the portable zip** (no install): download **`MeetingAssistant-windows-x64.zip`** →
right-click → **Extract All** → double-click **`MeetingAssistant.exe`**.

> **"Windows protected your PC"?** That's Windows SmartScreen warning about an app it
> doesn't recognize yet (this one isn't code-signed — a signing certificate costs a yearly fee).
> Click **More info → Run anyway**.

**Is it safe?** The full source is right here in this repo, and every release lists
**VirusTotal scan links + SHA-256 checksums** so you can verify the download yourself.

## Run from source (developers)

```bat
:: 1. get the code
git clone https://github.com/noobster97/meeting-assistant.git
cd meeting-assistant

:: 2. (optional but recommended) isolate the dependencies
python -m venv venv
venv\Scripts\activate

:: 3. install dependencies
pip install -r requirements.txt

:: 4. add your keys  —  copy the template, then edit .env
copy .env.example .env
notepad .env

:: 5. run it
run.bat
::   ...or, to see logs in a console:  python meeting_assistant.py
```

**Your keys go in `.env`** (never commit this file — it's already git-ignored):
- `GROQ_API_KEY=...` — **required**. Free, no credit card: https://console.groq.com/keys
- `DEEPGRAM_API_KEY=...` — *optional*, for instant word-by-word captions. Free $200
  credit at https://console.deepgram.com. Leave it blank to use Groq transcription
  (free-forever, ~1.5s behind). The app auto-falls-back to Groq if this key is missing.

## Build a standalone .exe (optional)

Prefer a double-click app with no Python? Build a one-folder exe — see
[Rebuilding the exe](#rebuilding-the-exe-after-code-changes) below. After building, copy
your `.env` into `dist\MeetingAssistant\` next to the new `MeetingAssistant.exe`, then run
that exe (or make a Desktop shortcut to it). The built `dist\` folder is git-ignored, so it
never ships in the repo — each user builds their own or runs from source.

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
assessment where AI help isn't allowed. Also: your `.env` holds your API keys — keep it to
yourself (it's git-ignored, so it won't be committed) and never paste keys into a shared
copy or screenshot.

---

## License

[MIT](LICENSE) © noobster97 — free to use, modify, and share.
