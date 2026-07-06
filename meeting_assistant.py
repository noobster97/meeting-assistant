"""
Meeting Assistant — listens to the OTHER people in a Zoom / Google Meet call,
transcribes what they say, and drafts a short answer you can read out.

Ears  : Groq Whisper (cloud, fast) — audio chunks are sent to Groq for transcription
Brain : Groq / Llama (free API key, fast) — drafts the short spoken reply
UI    : a small always-on-top window

Speed + templates:
- Answers STREAM in (words appear as they're written).
- Length control: One-liner / Short / A bit more.
- Instant quick replies (no AI call, zero lag): Repeat? / Give me a sec / Agree.

Nothing is saved to disk.
"""

import os
import io
import sys
import json
import time
import wave
import queue
import threading
import subprocess
import webbrowser
from datetime import datetime

import numpy as np
import tkinter as tk
import customtkinter as ctk
from dotenv import load_dotenv

import pyaudiowpatch as pyaudio
from groq import Groq

try:
    import websocket  # websocket-client — Deepgram live streaming
    HAVE_WS = True
except Exception:
    HAVE_WS = False

# folder the app runs from (works both as .py and as a PyInstaller .exe)
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    import keyboard  # global hotkeys (optional — the buttons always work)
    HAVE_KEYBOARD = True
except Exception:
    HAVE_KEYBOARD = False


# ─────────────────────────────── Settings ───────────────────────────────
STT_MODEL = "whisper-large-v3-turbo" # Groq cloud transcription (fast). "distil-whisper-large-v3-en" = fastest English.
GROQ_MODEL = "llama-3.1-8b-instant"  # fast. Use "llama-3.3-70b-versatile" for smarter (slower).
WINDOW_SECONDS = 1.5                 # audio chunk length sent to transcribe (lower = less delay, more API calls)
MAX_BACKLOG = 2                      # if more chunks than this are waiting, drop stale ones to stay realtime
SILENCE_RMS = 0.008                  # skip near-silent chunks (saves API calls + avoids false text)

# ── Transcription engine ──────────────────────────────────────────────
USE_DEEPGRAM = True                  # True = Deepgram live (instant, word-by-word). False = Groq chunks (free-forever, ~1.5s).
DEEPGRAM_MODEL = "nova-2"            # Deepgram streaming model

ANSWER_CONTEXT_SECONDS = 30          # how much recent talk to send when you ask
MAX_TOKENS = 60                      # hard cap on answer length (keeps it short + fast)
HOTKEY = "ctrl+space"                # press this to draft an answer
CHUNK = 1024

# Length templates — pick one in the window (default = Short).
LENGTHS = [
    ("One-liner",  "Answer in ONE punchy sentence, max 10 words. No filler."),
    ("Short",      "Answer in one short sentence, max 20 words. No filler."),
    ("A bit more", "Answer in two short, punchy sentences."),
]
DEFAULT_LENGTH = 1  # index into LENGTHS

# Instant quick replies — NO AI call, appear immediately. Edit these freely.
QUICK_REPLIES = [
    ("Repeat?",       "Sorry, could you please repeat that?"),
    ("Give me a sec", "That's a good question — give me a moment to think."),
    ("Agree",         "Yes, I agree with that."),
]

SUMMARY_MODEL = "llama-3.3-70b-versatile"  # smarter model for the end-of-meeting report

SUMMARY_PROMPT = (
    "You are a meeting assistant. You get the raw transcript of a work meeting "
    "(auto-captured, so it may contain small errors). Produce a clear, skimmable report "
    "in Markdown with EXACTLY these four sections. Under each, use short bullet points; "
    "write 'None' if there is nothing.\n\n"
    "## 📌 Summary\nWhat the meeting was about + key decisions (3-6 bullets).\n\n"
    "## ✅ Action Items\nThings that must be DONE. Start each with a verb. Add who/when if said.\n\n"
    "## ⚠️ Alerts / Deadlines\nUrgent items, deadlines, risks, blockers.\n\n"
    "## 📝 Notes / Info\nUseful context worth keeping.\n\n"
    "Keep it tight. Do NOT invent anything that is not in the transcript."
)

SYSTEM_PROMPT = (
    "You help a user reply in a live work meeting. English is not their first language "
    "and they are not confident speaking it. You get the transcript of what the OTHER "
    "people said; write the user's spoken reply for them.\n"
    "Style: SHORT and PUNCHY. Simple everyday words, short sentences, confident and calm. "
    "Cut every filler word — no 'I think', 'well', 'basically', 'as an AI', no preamble, "
    "no restating their question. Lead with the point. Never pad to fill space. "
    "Output ONLY the words to say — nothing else."
)


# ───────────────────────── shared state / queues ────────────────────────
audio_queue = queue.Queue()        # np.float32 windows: capture -> transcribe
transcript_queue = queue.Queue()   # new transcript text -> UI
answer_queue = queue.Queue()       # answer events {type: start|delta|final|done} -> UI
status_queue = queue.Queue()       # status messages -> UI

history = []                        # list of (timestamp, text)
history_lock = threading.Lock()

stop_flag = threading.Event()
paused = threading.Event()          # set = listening paused (no audio sent)
groq_client = None
deepgram_key = None
deepgram_active = False
dg_ws_holder = {}                  # holds the live Deepgram websocket for the capture thread
current_length_instruction = LENGTHS[DEFAULT_LENGTH][1]


def resample_to_16k(audio_f32, orig_rate):
    """Whisper wants 16 kHz mono. Cheap linear resample — good enough for speech."""
    if orig_rate == 16000:
        return audio_f32
    duration = len(audio_f32) / orig_rate
    new_len = int(duration * 16000)
    if new_len <= 0:
        return audio_f32
    x_old = np.linspace(0.0, duration, num=len(audio_f32), endpoint=False)
    x_new = np.linspace(0.0, duration, num=new_len, endpoint=False)
    return np.interp(x_new, x_old, audio_f32).astype(np.float32)


def to_wav_bytes(audio_f32, rate=16000):
    """Pack a float32 mono chunk into an in-memory 16-bit WAV for the Groq API."""
    pcm = np.clip(audio_f32, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


# ─────────────────────────── audio capture ──────────────────────────────
def capture_loop():
    """Grab whatever is playing out of your speakers (the other people's voices)."""
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        status_queue.put("ERROR: WASAPI not available on this PC.")
        return

    speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if not speakers.get("isLoopbackDevice", False):
        found = None
        for lb in p.get_loopback_device_info_generator():
            if speakers["name"] in lb["name"]:
                found = lb
                break
        if found is None:
            status_queue.put("ERROR: no loopback device found. Is anything playing?")
            return
        speakers = found

    rate = int(speakers["defaultSampleRate"])
    channels = int(speakers["maxInputChannels"])
    index = speakers["index"]
    frames_per_window = int(rate * WINDOW_SECONDS)

    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            frames_per_buffer=CHUNK,
            input=True,
            input_device_index=index,
        )
    except Exception as e:
        status_queue.put(f"ERROR opening audio: {e}")
        return

    if deepgram_active:
        status_queue.put("Connecting to Deepgram…")
    else:
        status_queue.put(f"Listening… ({speakers['name'][:40]})")

    buf = np.empty((0,), dtype=np.float32)          # Groq: fills a full window
    send_buf = np.empty((0,), dtype=np.float32)     # Deepgram: ~100ms stream packets
    send_len = int(16000 * 0.1)
    last_ka = 0.0                                    # last Deepgram KeepAlive while paused
    while not stop_flag.is_set():
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
        except Exception:
            continue

        if paused.is_set():
            # stop feeding audio; keep the Deepgram socket alive so it doesn't churn
            if deepgram_active:
                ws = dg_ws_holder.get("ws")
                now = time.time()
                if ws is not None and now - last_ka > 5:
                    try:
                        ws.send(json.dumps({"type": "KeepAlive"}))
                    except Exception:
                        pass
                    last_ka = now
            buf = np.empty((0,), dtype=np.float32)
            send_buf = np.empty((0,), dtype=np.float32)
            continue

        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)

        if deepgram_active:
            send_buf = np.concatenate([send_buf, resample_to_16k(samples, rate)])
            if len(send_buf) >= send_len:
                pcm = (np.clip(send_buf, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                send_buf = np.empty((0,), dtype=np.float32)
                ws = dg_ws_holder.get("ws")
                if ws is not None:
                    try:
                        ws.send(pcm, websocket.ABNF.OPCODE_BINARY)
                    except Exception:
                        pass
        else:
            buf = np.concatenate([buf, samples])
            if len(buf) >= frames_per_window:
                audio_queue.put(resample_to_16k(buf, rate))
                buf = np.empty((0,), dtype=np.float32)

    stream.stop_stream()
    stream.close()
    p.terminate()


# ─────────────────────────── transcription ──────────────────────────────
def transcribe_loop():
    status_queue.put("Listening… (Groq transcription)")
    while not stop_flag.is_set():
        try:
            audio = audio_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        # if we've fallen behind (slow network), skip stale chunks to stay realtime
        while audio_queue.qsize() > MAX_BACKLOG:
            try:
                audio = audio_queue.get_nowait()
            except queue.Empty:
                break
        if groq_client is None:
            continue
        # skip near-silent chunks — saves API calls and avoids hallucinated text
        if float(np.sqrt(np.mean(audio ** 2))) < SILENCE_RMS:
            continue
        try:
            result = groq_client.audio.transcriptions.create(
                file=("chunk.wav", to_wav_bytes(audio)),
                model=STT_MODEL,
                language="en",
                response_format="text",
                temperature=0.0,
            )
            text = (result if isinstance(result, str)
                    else getattr(result, "text", "")).strip()
        except Exception as e:
            status_queue.put(f"Transcription error: {e}")
            time.sleep(0.5)
            continue
        if text:
            with history_lock:
                history.append((time.time(), text))
            transcript_queue.put({"type": "final", "text": text})


# ────────────────────── transcription (Deepgram live) ───────────────────
def deepgram_loop():
    url = (
        "wss://api.deepgram.com/v1/listen"
        f"?model={DEEPGRAM_MODEL}&encoding=linear16&sample_rate=16000&channels=1"
        "&language=en&interim_results=true&smart_format=true&punctuate=true"
        "&endpointing=300&utterance_end_ms=1000&vad_events=true"
    )

    def on_open(ws):
        status_queue.put("Listening… (Deepgram live)")

    def on_message(ws, message):
        try:
            data = json.loads(message)
            alt = data.get("channel", {}).get("alternatives", [{}])[0]
            text = (alt.get("transcript") or "").strip()
        except Exception:
            return
        if not text:
            return
        if data.get("is_final"):
            with history_lock:
                history.append((time.time(), text))
            transcript_queue.put({"type": "final", "text": text})
        else:
            transcript_queue.put({"type": "interim", "text": text})

    def on_error(ws, err):
        # transient socket blips get auto-reconnected below; keep the message calm
        status_queue.put("Deepgram reconnecting…")

    def on_close(ws, *a):
        if not stop_flag.is_set():
            status_queue.put("Deepgram reconnecting…")

    while not stop_flag.is_set():
        try:
            ws = websocket.WebSocketApp(
                url,
                header=[f"Authorization: Token {deepgram_key}"],
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            dg_ws_holder["ws"] = ws
            # no ping_interval: we stream audio continuously, so the socket never idles.
            # (the old 5s ping/pong timeout was causing false disconnects + lag.)
            ws.run_forever()
        except Exception:
            pass
        if stop_flag.is_set():
            break
        time.sleep(0.4)  # brief pause before reconnect


# ─────────────────────────── answer drafting ────────────────────────────
def trigger_answer():
    with history_lock:
        cutoff = time.time() - ANSWER_CONTEXT_SECONDS
        recent = [txt for (ts, txt) in history if ts >= cutoff]
    context = " ".join(recent).strip()
    if not context:
        status_queue.put("Nothing heard yet — wait for them to speak.")
        return
    status_queue.put("Thinking…")
    threading.Thread(
        target=ask_groq, args=(context, current_length_instruction), daemon=True
    ).start()


def ask_groq(context, length_instruction):
    if groq_client is None:
        answer_queue.put({"type": "final",
                          "text": "[No Groq API key. Put GROQ_API_KEY in .env — see README.]"})
        return
    answer_queue.put({"type": "start"})
    try:
        stream = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.4,
            max_tokens=MAX_TOKENS,
            stream=True,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content":
                    f'They said:\n"""\n{context}\n"""\n\n{length_instruction} '
                    f"Write only the words I should say."},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                answer_queue.put({"type": "delta", "text": delta})
    except Exception as e:
        answer_queue.put({"type": "delta", "text": f"[Error contacting Groq: {e}]"})
    answer_queue.put({"type": "done"})


def quick_reply(text):
    """Instant canned reply — no AI call, zero lag."""
    answer_queue.put({"type": "final", "text": text})
    status_queue.put("Ready — read it out.")


# ─────────────────────── engine choice (persisted) ──────────────────────
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


def load_engine_choice():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("use_deepgram", USE_DEEPGRAM))
    except Exception:
        return USE_DEEPGRAM


def save_engine_choice(use_dg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"use_deepgram": bool(use_dg)}, f)
    except Exception:
        pass


def restart_app():
    """Relaunch the app so the new engine choice takes effect."""
    stop_flag.set()
    try:
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)])
    except Exception:
        pass
    os._exit(0)


# ──────────────────────────────── UI ────────────────────────────────────
# palette
BG       = "#0f1117"
CARD     = "#171a22"
CARD2    = "#1d212b"
TXT      = "#e7e9ef"
MUTED    = "#828a9c"
ACCENT   = "#5b8cff"
ACCENT_H = "#4a76e0"
GREEN_BG = "#14211a"
GREEN_TX = "#d9f5d2"


def build_ui():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    root.title("Meeting Assistant")
    root.geometry("500x820")
    root.minsize(470, 690)
    root.configure(fg_color=BG)
    root.wm_attributes("-topmost", True)

    F = "Segoe UI"

    # ── header ──
    header = ctk.CTkFrame(root, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(14, 2))
    ctk.CTkLabel(header, text="🎙  Meeting Assistant", font=(F, 17, "bold"),
                 text_color=TXT).pack(side="left")
    ontop_var = tk.BooleanVar(value=True)
    ctk.CTkSwitch(header, text="On top", variable=ontop_var, onvalue=True, offvalue=False,
                  command=lambda: root.wm_attributes("-topmost", ontop_var.get()),
                  font=(F, 11), text_color=MUTED, progress_color=ACCENT,
                  width=40).pack(side="right")

    status_var = tk.StringVar(value="Starting…")
    ctk.CTkLabel(root, textvariable=status_var, font=(F, 11), text_color=ACCENT,
                 anchor="w").pack(fill="x", padx=18, pady=(0, 8))

    # ── engine switch ──
    eng_row = ctk.CTkFrame(root, fg_color="transparent")
    eng_row.pack(fill="x", padx=16, pady=(0, 8))
    ctk.CTkLabel(eng_row, text="ENGINE", font=(F, 10, "bold"),
                 text_color=MUTED).pack(side="left", padx=(2, 8))

    def on_engine(value):
        want_dg = value.startswith("⚡")
        if want_dg == deepgram_active:
            return
        save_engine_choice(want_dg)
        status_var.set("Switching engine — restarting…")
        root.after(350, restart_app)

    engine_seg = ctk.CTkSegmentedButton(
        eng_row, values=["⚡ Deepgram", "Groq"], command=on_engine,
        font=(F, 12), selected_color=ACCENT, selected_hover_color=ACCENT_H,
        fg_color=CARD, unselected_color=CARD)
    engine_seg.pack(side="left", fill="x", expand=True)
    engine_seg.set("⚡ Deepgram" if deepgram_active else "Groq")

    # ── transcript ──
    ctk.CTkLabel(root, text="THEY'RE SAYING", font=(F, 10, "bold"),
                 text_color=MUTED, anchor="w").pack(fill="x", padx=18, pady=(4, 2))
    transcript = ctk.CTkTextbox(root, height=120, wrap="word", font=(F, 12),
                                fg_color=CARD, text_color="#c8ccd8",
                                border_width=0, corner_radius=12)
    transcript.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    transcript.configure(state="disabled")
    _tb = transcript._textbox          # underlying tk.Text — lets us show a live last line
    _tb.tag_config("live", foreground="#6f7688")   # dim = not final yet

    def clear_live():
        r = _tb.tag_ranges("live")
        if r:
            _tb.delete(r[0], r[1])

    # ── answer ──
    ctk.CTkLabel(root, text="YOUR ANSWER — READ THIS OUT", font=(F, 10, "bold"),
                 text_color="#7bb87b", anchor="w").pack(fill="x", padx=18, pady=(0, 2))
    answer = ctk.CTkTextbox(root, height=100, wrap="word", font=(F, 15),
                            fg_color=GREEN_BG, text_color=GREEN_TX,
                            border_width=0, corner_radius=12)
    answer.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    answer.configure(state="disabled")

    # ── length + quick replies ──
    def on_length(value):
        global current_length_instruction
        for label, instr in LENGTHS:
            if label == value:
                current_length_instruction = instr

    len_row = ctk.CTkFrame(root, fg_color="transparent")
    len_row.pack(fill="x", padx=16, pady=(0, 6))
    ctk.CTkLabel(len_row, text="LENGTH", font=(F, 10, "bold"),
                 text_color=MUTED).pack(side="left", padx=(2, 8))
    length_seg = ctk.CTkSegmentedButton(
        len_row, values=[l for l, _ in LENGTHS], command=on_length, font=(F, 11),
        selected_color=ACCENT, selected_hover_color=ACCENT_H,
        fg_color=CARD, unselected_color=CARD)
    length_seg.pack(side="left", fill="x", expand=True)
    length_seg.set(LENGTHS[DEFAULT_LENGTH][0])

    quick_row = ctk.CTkFrame(root, fg_color="transparent")
    quick_row.pack(fill="x", padx=16, pady=(0, 8))
    ctk.CTkLabel(quick_row, text="QUICK", font=(F, 10, "bold"),
                 text_color=MUTED).pack(side="left", padx=(2, 8))
    for label, text in QUICK_REPLIES:
        ctk.CTkButton(quick_row, text=label, command=lambda t=text: quick_reply(t),
                      font=(F, 11), fg_color=CARD2, hover_color="#2a2f3d",
                      text_color=TXT, corner_radius=8, height=30,
                      width=90).pack(side="left", padx=3)

    def do_copy():
        root.clipboard_clear()
        root.clipboard_append(answer.get("1.0", "end").strip())
        status_var.set("Answer copied.")

    def do_clear():
        with history_lock:
            history.clear()
        for w in (transcript, answer):
            w.configure(state="normal"); w.delete("1.0", "end"); w.configure(state="disabled")
        status_var.set("Cleared. Listening…")

    def open_summary():
        with history_lock:
            full = "\n".join(txt for _, txt in history).strip()
        if not full:
            status_var.set("No transcript yet to summarize.")
            return
        if groq_client is None:
            status_var.set("No Groq key — can't make a report.")
            return

        win = ctk.CTkToplevel(root)
        win.title("Meeting Report")
        win.geometry("580x640")
        win.configure(fg_color=BG)
        win.after(200, lambda: win.wm_attributes("-topmost", ontop_var.get()))
        ctk.CTkLabel(win, text="📋  Meeting Report", font=(F, 15, "bold"),
                     text_color=TXT).pack(anchor="w", padx=18, pady=(14, 6))
        box = ctk.CTkTextbox(win, wrap="word", font=(F, 12), fg_color=CARD,
                             text_color=TXT, border_width=0, corner_radius=12)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        box.insert("end", "Generating report…")
        sq = queue.Queue()

        def worker():
            sq.put(("clear", None))
            try:
                stream = groq_client.chat.completions.create(
                    model=SUMMARY_MODEL, temperature=0.3, max_tokens=1400, stream=True,
                    messages=[{"role": "system", "content": SUMMARY_PROMPT},
                              {"role": "user",
                               "content": f'Meeting transcript:\n"""\n{full}\n"""'}])
                for ch in stream:
                    d = ch.choices[0].delta.content or ""
                    if d:
                        sq.put(("delta", d))
            except Exception as e:
                sq.put(("delta", f"[Error: {e}]"))
            sq.put(("done", None))

        def poll_sum():
            try:
                while True:
                    kind, val = sq.get_nowait()
                    if kind == "clear":
                        box.delete("1.0", "end")
                    elif kind == "delta":
                        box.insert("end", val); box.see("end")
            except queue.Empty:
                pass
            if win.winfo_exists():
                win.after(80, poll_sum)

        def save():
            folder = os.path.join(APP_DIR, "reports")
            os.makedirs(folder, exist_ok=True)
            fn = os.path.join(folder, "meeting-" + datetime.now().strftime("%Y%m%d-%H%M") + ".md")
            with open(fn, "w", encoding="utf-8") as f:
                f.write(box.get("1.0", "end").strip() + "\n")
            status_var.set("Saved: reports\\" + os.path.basename(fn))

        def copy_report():
            root.clipboard_clear()
            root.clipboard_append(box.get("1.0", "end").strip())
            status_var.set("Report copied.")

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(bar, text="💾  Save report", command=save, font=(F, 12, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_H, corner_radius=10,
                      height=36).pack(side="left")
        ctk.CTkButton(bar, text="Copy", command=copy_report, font=(F, 12),
                      fg_color=CARD2, hover_color="#2a2f3d", corner_radius=10,
                      height=36, width=70).pack(side="left", padx=8)
        ctk.CTkButton(bar, text="Close", command=win.destroy, font=(F, 12),
                      fg_color=CARD2, hover_color="#2a2f3d", corner_radius=10,
                      height=36, width=70).pack(side="right")
        threading.Thread(target=worker, daemon=True).start()
        win.after(80, poll_sum)

    # ── primary answer button ──
    ctk.CTkButton(root, text="💬   Answer     (Ctrl + Space)", command=trigger_answer,
                  font=(F, 14, "bold"), fg_color=ACCENT, hover_color=ACCENT_H,
                  text_color="#ffffff", corner_radius=12, height=46).pack(
        fill="x", padx=16, pady=(0, 8))

    # ── utility row ──
    def toggle_pause():
        if paused.is_set():
            paused.clear()
            pause_btn.configure(text="⏸  Stop", fg_color="#7a2f33", hover_color="#8c383c")
            status_var.set("Listening…")
        else:
            paused.set()
            pause_btn.configure(text="▶  Resume", fg_color="#2f6d3a", hover_color="#3a7d45")
            status_var.set("⏸ Paused — not listening")

    util = ctk.CTkFrame(root, fg_color="transparent")
    util.pack(fill="x", padx=16, pady=(0, 8))
    pause_btn = ctk.CTkButton(util, text="⏸  Stop", command=toggle_pause, font=(F, 12, "bold"),
                              fg_color="#7a2f33", hover_color="#8c383c", corner_radius=10,
                              height=34)
    pause_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
    ctk.CTkButton(util, text="Copy", command=do_copy, font=(F, 12),
                  fg_color=CARD2, hover_color="#2a2f3d", corner_radius=10,
                  height=34).pack(side="left", fill="x", expand=True, padx=4)
    ctk.CTkButton(util, text="Clear", command=do_clear, font=(F, 12),
                  fg_color=CARD2, hover_color="#2a2f3d", corner_radius=10,
                  height=34).pack(side="left", fill="x", expand=True, padx=(4, 0))

    # ── report button ──
    ctk.CTkButton(root, text="📋   Meeting Report   ·   summary · to-dos · alerts",
                  command=open_summary, font=(F, 13, "bold"), fg_color=CARD,
                  hover_color=CARD2, text_color=TXT, corner_radius=12,
                  height=42, border_width=1, border_color="#2b3040").pack(
        fill="x", padx=16, pady=(0, 8))

    hint = ("Ctrl+Space = answer    ·    Ctrl+1/2/3 = quick replies"
            if HAVE_KEYBOARD else "Hotkeys off — use the buttons")
    ctk.CTkLabel(root, text=hint, font=(F, 10), text_color="#5a6072",
                 anchor="w").pack(fill="x", padx=18, pady=(0, 10))

    def poll():
        try:
            while True:
                ev = transcript_queue.get_nowait()
                transcript.configure(state="normal")
                clear_live()                                       # drop the old live line
                if ev.get("type") == "interim":
                    _tb.insert("end-1c", ev["text"], ("live",))    # dim, still forming
                else:                                              # final: commit it solid
                    _tb.insert("end-1c", ev["text"] + "\n")
                _tb.see("end")
                transcript.configure(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                ev = answer_queue.get_nowait()
                kind = ev["type"]
                if kind == "start":
                    answer.configure(state="normal"); answer.delete("1.0", "end")
                    answer.configure(state="disabled")
                elif kind == "delta":
                    answer.configure(state="normal"); answer.insert("end", ev["text"])
                    answer.see("end"); answer.configure(state="disabled")
                elif kind == "final":
                    answer.configure(state="normal"); answer.delete("1.0", "end")
                    answer.insert("end", ev["text"]); answer.configure(state="disabled")
                elif kind == "done":
                    status_var.set("Listening…")
        except queue.Empty:
            pass
        try:
            while True:
                status_var.set(status_queue.get_nowait())
        except queue.Empty:
            pass
        root.after(80, poll)

    def on_close():
        stop_flag.set()
        root.after(150, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(80, poll)
    return root


# ─────────────────────── first-run key setup ────────────────────────────
def save_env(groq_key, dg_key):
    """Write the API keys to .env next to the app (created if missing).
    So a non-technical user never has to open a text file."""
    lines = [
        "# Meeting Assistant keys - keep this file private. Do not share or commit it.",
        f"GROQ_API_KEY={groq_key or ''}",
        f"DEEPGRAM_API_KEY={dg_key or ''}",
    ]
    try:
        with open(os.path.join(APP_DIR, ".env"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True
    except Exception:
        return False


def first_run_setup(existing_groq="", existing_dg=""):
    """Shown on first launch when no Groq key is found: ask for the key in a
    friendly window, save it to .env, and return (groq_key, dg_key).
    No terminal, no editing files by hand."""
    result = {"groq": existing_groq or "", "dg": existing_dg or ""}

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    setup = ctk.CTk()
    setup.title("Meeting Assistant — Setup")
    setup.geometry("470x430")
    setup.minsize(470, 430)
    setup.configure(fg_color=BG)
    setup.wm_attributes("-topmost", True)
    F = "Segoe UI"

    ctk.CTkLabel(setup, text="🎙  Welcome to Meeting Assistant", font=(F, 17, "bold"),
                 text_color=TXT).pack(anchor="w", padx=22, pady=(20, 4))
    ctk.CTkLabel(setup, text="One quick step: paste your free Groq API key.\n"
                 "It's free (no credit card) and is saved on your PC only.",
                 font=(F, 12), text_color=MUTED, justify="left").pack(anchor="w", padx=22)

    ctk.CTkButton(setup, text="🔑  Get a free Groq key  (opens browser)  →",
                  command=lambda: webbrowser.open("https://console.groq.com/keys"),
                  font=(F, 12, "bold"), fg_color=CARD2, hover_color="#2a2f3d",
                  text_color=TXT, corner_radius=8, height=34).pack(
        anchor="w", padx=22, pady=(12, 4))

    ctk.CTkLabel(setup, text="GROQ API KEY   (required)", font=(F, 10, "bold"),
                 text_color=MUTED).pack(anchor="w", padx=22, pady=(10, 2))
    groq_entry = ctk.CTkEntry(setup, width=420, font=(F, 12), fg_color=CARD,
                              placeholder_text="gsk_...")
    groq_entry.pack(padx=22)
    if existing_groq:
        groq_entry.insert(0, existing_groq)

    ctk.CTkLabel(setup, text="DEEPGRAM API KEY   (optional — for instant captions)",
                 font=(F, 10, "bold"), text_color=MUTED).pack(anchor="w", padx=22, pady=(12, 2))
    dg_entry = ctk.CTkEntry(setup, width=420, font=(F, 12), fg_color=CARD,
                            placeholder_text="leave blank to use free Groq transcription")
    dg_entry.pack(padx=22)
    if existing_dg:
        dg_entry.insert(0, existing_dg)

    msg = tk.StringVar(value="")
    ctk.CTkLabel(setup, textvariable=msg, font=(F, 11),
                 text_color="#e0a24b").pack(anchor="w", padx=22, pady=(10, 0))

    def save_and_start():
        g = groq_entry.get().strip()
        d = dg_entry.get().strip()
        if not g:
            msg.set("Please paste your Groq key first — or click Skip.")
            return
        if not save_env(g, d):
            msg.set("Couldn't write .env (is the folder read-only?). Try Skip.")
            return
        os.environ["GROQ_API_KEY"] = g
        os.environ["DEEPGRAM_API_KEY"] = d
        result["groq"], result["dg"] = g, d
        setup.quit()
        setup.destroy()

    def skip():
        result["groq"] = groq_entry.get().strip()
        result["dg"] = dg_entry.get().strip()
        setup.quit()
        setup.destroy()

    row = ctk.CTkFrame(setup, fg_color="transparent")
    row.pack(fill="x", padx=22, pady=(18, 12))
    ctk.CTkButton(row, text="Save & Start", command=save_and_start, font=(F, 13, "bold"),
                  fg_color=ACCENT, hover_color=ACCENT_H, text_color="#ffffff",
                  corner_radius=10, height=40).pack(side="left")
    ctk.CTkButton(row, text="Skip", command=skip, font=(F, 12),
                  fg_color=CARD2, hover_color="#2a2f3d", corner_radius=10,
                  height=40, width=80).pack(side="right")

    groq_entry.focus()
    setup.protocol("WM_DELETE_WINDOW", skip)
    setup.mainloop()
    return result["groq"], result["dg"]


# ──────────────────────────────── main ──────────────────────────────────
def main():
    global groq_client, deepgram_key, deepgram_active
    load_dotenv(os.path.join(APP_DIR, ".env"))
    key = os.getenv("GROQ_API_KEY")
    dg = os.getenv("DEEPGRAM_API_KEY")

    # First run (or key missing): ask for it in a friendly window, not a text file.
    if not key:
        key, dg = first_run_setup(key, dg)

    if key:
        groq_client = Groq(api_key=key)
    else:
        status_queue.put("No GROQ_API_KEY — answers/report won't work. See README.")

    deepgram_key = dg or os.getenv("DEEPGRAM_API_KEY")
    want_dg = load_engine_choice()
    deepgram_active = bool(want_dg and HAVE_WS and deepgram_key)
    if want_dg and not deepgram_active:
        status_queue.put("Deepgram unavailable — falling back to Groq transcription.")

    threading.Thread(target=capture_loop, daemon=True).start()
    if deepgram_active:
        threading.Thread(target=deepgram_loop, daemon=True).start()
    else:
        threading.Thread(target=transcribe_loop, daemon=True).start()

    if HAVE_KEYBOARD:
        try:
            keyboard.add_hotkey(HOTKEY, trigger_answer)
            for i, (_, text) in enumerate(QUICK_REPLIES):
                keyboard.add_hotkey(f"ctrl+{i + 1}", lambda t=text: quick_reply(t))
        except Exception:
            status_queue.put("Couldn't register hotkeys — use the buttons instead.")

    root = build_ui()
    root.mainloop()
    stop_flag.set()


if __name__ == "__main__":
    main()
