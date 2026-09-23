"""ESC Control - desktop app for the Pico BLHeli_S throttle firmware.

    python esc_control.py [--port COM5]

Requires:  pip install pyserial
"""
import sys
import re
import time
import queue
import threading
import itertools

import serial
import serial.tools.list_ports

BAUD = 115200
PING_INTERVAL = 0.1            # heartbeat; the Pico cuts throttle if it hears nothing for 1 s
PICO_VID = 0x2E8A
HARD_MIN, HARD_MAX = 800, 2200  # absolute pulse limits enforced by the firmware

C = dict(
    bg="#12151a", panel="#1b1f26", panel2="#252b36", line="#333b49",
    fg="#e8ebf0", dim="#8b94a3", accent="#4da3ff",
    ok="#3fb950", warn="#e3b341", bad="#f0524f", console="#0d1014",
)

STATUS_RE = re.compile(
    r"pulse=(\d+)us \((\d+)%\) target=(\d+)us map: 0%=(\d+)us 1%=(\d+)us 100%=(\d+)us "
    r"slew=(\d+)%/20ms timeout=(\w+) failsafe=(\w+)(?: cfg=(\w+))?"
)

GUI_HELP = """Local commands:
  ports               list serial ports
  connect [PORT]      connect (no PORT = the one selected above)
  disconnect          stop motor and close the port
  clear               wipe this console
  quit / exit         stop motor and close the app
Anything else goes straight to the Pico. Try:  help | status | s 20 | us 1100 | stop | save"""


# ----------------------------------------------------------------------------- helpers

def list_ports():
    """Serial ports as (device, description), Pico first."""
    ports = list(serial.tools.list_ports.comports())
    ports.sort(key=lambda p: 0 if p.vid == PICO_VID else 1)
    return [(p.device, p.description) for p in ports]


def parse_status(line):
    m = STATUS_RE.search(line)
    if not m:
        return None
    g = m.groups()
    return dict(pulse=int(g[0]), pct=int(g[1]), target=int(g[2]),
                mn=int(g[3]), st=int(g[4]), mx=int(g[5]), slew=int(g[6]),
                timeout=g[7], failsafe=g[8], cfg=g[9])


def pct_to_us(p, mn, st, mx):
    """Same mapping as the firmware: 0 % -> off pulse, 1..100 % -> start..max."""
    return mn if p <= 0 else st + (mx - st) * (p - 1) // 99


def cal_valid(mn, st, mx):
    return HARD_MIN <= mn < st and st <= mx - 50 and mx <= HARD_MAX


def safe_order(cur, new):
    """Order in which to send min/start/max so every intermediate state is valid."""
    changed = [k for k in ("mn", "st", "mx") if new[k] != cur[k]]
    for perm in itertools.permutations(changed):
        s = dict(mn=cur["mn"], st=cur["st"], mx=cur["mx"])
        ok = True
        for k in perm:
            s[k] = new[k]
            if not cal_valid(s["mn"], s["st"], s["mx"]):
                ok = False
                break
        if ok:
            return list(perm)
    return changed


# ----------------------------------------------------------------------------- serial link

class Link:
    """Serial connection + reader thread + heartbeat thread."""

    def __init__(self, on_line):
        self.on_line = on_line
        self.ser = None
        self._stop = threading.Event()
        self._wlock = threading.Lock()

    def connect(self, port):
        self.disconnect()
        self.ser = serial.Serial(port, BAUD, timeout=0.1)
        self._stop = threading.Event()
        threading.Thread(target=self._reader, args=(self.ser, self._stop), daemon=True).start()
        threading.Thread(target=self._heartbeat, args=(self._stop,), daemon=True).start()

    def disconnect(self):
        ser = self.ser
        if not ser:
            return
        try:
            self.send("debug off")
            self.send("stop")
            time.sleep(0.1)
        except Exception:
            pass
        self._stop.set()
        self.ser = None
        try:
            ser.close()
        except Exception:
            pass

    def send(self, text):
        ser = self.ser
        if not ser:
            raise IOError("not connected")
        with self._wlock:
            ser.write((text.strip() + "\n").encode())

    def _reader(self, ser, stop):
        buf = b""
        while not stop.is_set():
            try:
                data = ser.read(256)
            except Exception as e:
                if not stop.is_set():
                    self.on_line(f"[link lost: {e}]")
                    self.ser = None
                    stop.set()
                return
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self.on_line(raw.decode(errors="replace").rstrip("\r"))

    def _heartbeat(self, stop):
        while not stop.is_set():
            try:
                self.send("ping")
            except Exception:
                return
            time.sleep(PING_INTERVAL)


# ----------------------------------------------------------------------------- GUI

def run_gui(port=None):
    import tkinter as tk
    from tkinter import ttk, messagebox

    try:  # crisp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("ESC Control")
    root.configure(bg=C["bg"])
    scale = root.winfo_fpixels("1i") / 96.0

    def S(px):
        return int(round(px * scale))

    root.geometry(f"{S(1040)}x{S(860)}")
    root.minsize(S(940), S(760))

    FONT = ("Segoe UI", 10)
    FONT_B = ("Segoe UI", 10, "bold")
    FONT_S = ("Segoe UI", 9)
    FONT_H = ("Segoe UI", 12, "bold")
    MONO = ("Consolas", 10)

    # ---- ttk theme (combobox, scrollbar)
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TCombobox", fieldbackground=C["panel2"], background=C["panel2"],
                    foreground=C["fg"], arrowcolor=C["fg"], bordercolor=C["line"],
                    lightcolor=C["panel2"], darkcolor=C["panel2"], padding=S(4))
    style.map("TCombobox", fieldbackground=[("readonly", C["panel2"])],
              foreground=[("readonly", C["fg"])],
              selectbackground=[("readonly", C["panel2"])],
              selectforeground=[("readonly", C["fg"])])
    style.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["console"],
                    bordercolor=C["console"], arrowcolor=C["dim"], lightcolor=C["panel2"],
                    darkcolor=C["panel2"])
    root.option_add("*TCombobox*Listbox.background", C["panel2"])
    root.option_add("*TCombobox*Listbox.foreground", C["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#06121f")

    # ---- widget factories
    def make_button(parent, text, command, kind="normal", font=None, padx=12, pady=6):
        colors = {
            "normal": (C["panel2"], C["fg"], C["line"]),
            "accent": (C["accent"], "#06121f", "#7cb9ff"),
            "danger": (C["bad"], "#ffffff", "#ff7a77"),
            "ghost": (C["panel"], C["dim"], C["panel2"]),
        }
        bg, fg, hover = colors[kind]
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                      activebackground=hover, activeforeground=fg, relief="flat", bd=0,
                      padx=S(padx), pady=S(pady), cursor="hand2", font=font or FONT,
                      highlightthickness=0)
        b.bind("<Enter>", lambda e: b.config(bg=hover))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def make_entry(parent, var, width=8):
        return tk.Entry(parent, textvariable=var, width=width, bg=C["panel2"], fg=C["fg"],
                        insertbackground=C["fg"], relief="flat", font=FONT, justify="right",
                        highlightthickness=1, highlightbackground=C["line"],
                        highlightcolor=C["accent"])

    def make_card(parent, title):
        outer = tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        tk.Label(outer, text=title, bg=C["panel"], fg=C["dim"], font=FONT_B, anchor="w"
                 ).pack(fill="x", padx=S(18), pady=(S(14), S(4)))
        inner = tk.Frame(outer, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=S(18), pady=(0, S(16)))
        return outer, inner

    # ---- state
    q = queue.Queue()
    link = Link(lambda l: q.put(l))
    pico = dict(pulse=None, pct=0, target=None, mn=1000, st=1187, mx=1830, slew=1,
                timeout="1000ms", failsafe="ok", cfg=None)
    state = dict(throttle=0, last_sent=None, sync=False, have_status=False, last_rx=0.0,
                 banner_kind="", history=[], hidx=0, shown_conn=None, hint_shown=False,
                 ramp_dir=0, ramp_job=None)

    # ========================================================================= header
    header = tk.Frame(root, bg=C["bg"])
    header.pack(fill="x", padx=S(20), pady=(S(16), S(10)))

    title_box = tk.Frame(header, bg=C["bg"])
    title_box.pack(side="left")
    tk.Label(title_box, text="ESC Control", bg=C["bg"], fg=C["fg"],
             font=("Segoe UI", 16, "bold")).pack(anchor="w")
    tk.Label(title_box, text="Raspberry Pi Pico \u2192 BLHeli_S", bg=C["bg"], fg=C["dim"],
             font=FONT_S).pack(anchor="w")

    conn_box = tk.Frame(header, bg=C["bg"])
    conn_box.pack(side="right")
    dot = tk.Canvas(conn_box, width=S(14), height=S(14), bg=C["bg"], highlightthickness=0)
    dot_id = dot.create_oval(S(2), S(2), S(12), S(12), fill=C["dim"], outline="")
    dot.pack(side="left", padx=(0, S(6)))
    conn_lbl = tk.Label(conn_box, text="Disconnected", bg=C["bg"], fg=C["dim"], font=FONT,
                        width=18, anchor="w")
    conn_lbl.pack(side="left", padx=(0, S(10)))
    port_var = tk.StringVar()
    port_box = ttk.Combobox(conn_box, textvariable=port_var, width=10, state="readonly", font=FONT)
    port_box.pack(side="left", padx=(0, S(6)))
    refresh_btn = make_button(conn_box, "\u21bb", lambda: refresh_ports(), kind="normal", padx=9)
    refresh_btn.pack(side="left", padx=(0, S(6)))
    conn_btn = make_button(conn_box, "Connect", lambda: toggle_connect(), kind="accent", font=FONT_B)
    conn_btn.pack(side="left")

    # ========================================================================= main cards
    main = tk.Frame(root, bg=C["bg"])
    main.pack(fill="x", padx=S(20))
    main.columnconfigure(0, weight=3, uniform="c")
    main.columnconfigure(1, weight=2, uniform="c")

    # ---------- throttle card
    t_outer, t_in = make_card(main, "THROTTLE")
    t_outer.grid(row=0, column=0, sticky="nsew", padx=(0, S(8)))

    banner = tk.Label(t_in, text="", bg=C["panel"], fg=C["warn"], font=FONT_S, anchor="w",
                      justify="left", wraplength=S(520))
    banner.pack(fill="x")

    readout = tk.Frame(t_in, bg=C["panel"])
    readout.pack(pady=(S(2), 0))
    pct_lbl = tk.Label(readout, text="0", bg=C["panel"], fg=C["fg"], font=("Segoe UI", 54, "bold"))
    pct_lbl.pack(side="left")
    tk.Label(readout, text="%", bg=C["panel"], fg=C["dim"], font=("Segoe UI", 20, "bold")
             ).pack(side="left", anchor="s", pady=(0, S(14)), padx=(S(4), 0))
    detail_lbl = tk.Label(t_in, text="not connected", bg=C["panel"], fg=C["dim"], font=FONT)
    detail_lbl.pack()

    class ThrottleSlider(tk.Canvas):
        def __init__(self, master, on_change):
            super().__init__(master, height=S(78), bg=C["panel"], highlightthickness=0)
            self.on_change = on_change
            self.value = 0
            self.actual = None
            self.bind("<Configure>", lambda e: self.draw())
            self.bind("<Button-1>", self._mouse)
            self.bind("<B1-Motion>", self._mouse)
            self.bind("<MouseWheel>", self._wheel)

        def _geo(self):
            pad = S(22)
            return pad, max(1, self.winfo_width() - 2 * pad)

        def _x(self, v):
            pad, w = self._geo()
            return pad + w * v / 100.0

        def _mouse(self, e):
            pad, w = self._geo()
            self.on_change(max(0, min(100, int(round((e.x - pad) / w * 100)))))

        def _wheel(self, e):
            self.on_change(self.value + (1 if e.delta > 0 else -1))

        def draw(self):
            self.delete("all")
            if self.winfo_width() < 20:
                return
            pad, _ = self._geo()
            end = self.winfo_width() - pad
            cy = S(30)
            self.create_line(pad, cy, end, cy, width=S(10), capstyle="round", fill=C["line"])
            level = self.actual if self.actual is not None else self.value
            if level > 0:
                self.create_line(pad, cy, self._x(level), cy, width=S(10), capstyle="round",
                                 fill=C["accent"] if self.actual is not None else C["dim"])
            for t in (0, 25, 50, 75, 100):
                x = self._x(t)
                self.create_line(x, cy + S(17), x, cy + S(23), fill=C["dim"])
                self.create_text(x, cy + S(35), text=str(t), fill=C["dim"], font=FONT_S)
            x = self._x(self.value)
            r = S(12)
            self.create_oval(x - r, cy - r, x + r, cy + r, fill=C["fg"], outline=C["accent"], width=S(3))

    slider = ThrottleSlider(t_in, lambda v: set_throttle(v, user=True))
    slider.pack(fill="x", pady=(S(6), S(4)))

    step_row = tk.Frame(t_in, bg=C["panel"])
    step_row.pack(pady=(S(2), S(8)))
    for delta, txt in ((-5, "\u22125"), (-1, "\u22121"), (1, "+1"), (5, "+5")):
        make_button(step_row, txt, lambda d=delta: set_throttle(state["throttle"] + d, user=True),
                    padx=14).pack(side="left", padx=S(3))
    tk.Frame(step_row, bg=C["panel"], width=S(18)).pack(side="left")
    for p in (0, 25, 50, 75, 100):
        make_button(step_row, f"{p}", lambda v=p: set_throttle(v, user=True), padx=11
                    ).pack(side="left", padx=S(3))

    ramp_row = tk.Frame(t_in, bg=C["panel"])
    ramp_row.pack(fill="x", pady=(S(0), S(6)))

    ramp_rate_var = tk.StringVar(value="20")

    ramp_dn_btn = make_button(ramp_row, "\u25bc  HOLD TO RAMP DOWN", None, kind="normal",
                              font=FONT_B, pady=10)
    ramp_dn_btn.pack(side="left", fill="x", expand=True, padx=(0, S(4)))
    ramp_dn_btn.bind("<ButtonPress-1>", lambda e: start_ramp(-1))
    ramp_dn_btn.bind("<ButtonRelease-1>", lambda e: stop_ramp())
    ramp_dn_btn.bind("<Leave>", lambda e: stop_ramp() if e.state & 0x100 else None)

    ramp_up_btn = make_button(ramp_row, "\u25b2  HOLD TO RAMP UP", None, kind="normal",
                              font=FONT_B, pady=10)
    ramp_up_btn.pack(side="left", fill="x", expand=True, padx=(S(4), S(8)))
    ramp_up_btn.bind("<ButtonPress-1>", lambda e: start_ramp(1))
    ramp_up_btn.bind("<ButtonRelease-1>", lambda e: stop_ramp())
    ramp_up_btn.bind("<Leave>", lambda e: stop_ramp() if e.state & 0x100 else None)

    tk.Label(ramp_row, text="rate", bg=C["panel"], fg=C["dim"], font=FONT_S).pack(side="left")
    make_entry(ramp_row, ramp_rate_var, width=4).pack(side="left", padx=(S(4), S(2)))
    tk.Label(ramp_row, text="%/s", bg=C["panel"], fg=C["dim"], font=FONT_S).pack(side="left")

    stop_btn = make_button(t_in, "STOP   (Space / Esc)", lambda: do_stop(), kind="danger",
                           font=("Segoe UI", 12, "bold"), pady=10)
    stop_btn.pack(fill="x", pady=(S(4), S(6)))
    tk.Label(t_in, text="\u2190 \u2192 \u00b11   \u2191 \u2193 \u00b15   PgUp/PgDn \u00b110   mouse wheel on slider \u00b11   hold R/F to ramp",
             bg=C["panel"], fg=C["dim"], font=FONT_S).pack()

    # ---------- calibration card
    c_outer, c_in = make_card(main, "CALIBRATION")
    c_outer.grid(row=0, column=1, sticky="nsew", padx=(S(8), 0))

    cfg_lbl = tk.Label(c_in, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w")
    cfg_lbl.pack(fill="x", pady=(0, S(6)))

    grid = tk.Frame(c_in, bg=C["panel"])
    grid.pack(fill="x")
    grid.columnconfigure(0, weight=1)
    vars_ = dict(mn=tk.StringVar(), st=tk.StringVar(), mx=tk.StringVar(),
                 slew=tk.StringVar(), to=tk.StringVar())
    rows = (("mn", "Off  (0 %)", "\u00b5s"), ("st", "Start  (1 %)", "\u00b5s"),
            ("mx", "Max  (100 %)", "\u00b5s"), ("slew", "Ramp limit", "% / 20 ms"),
            ("to", "Failsafe", "ms  (0 = off)"))
    for i, (key, label, unit) in enumerate(rows):
        tk.Label(grid, text=label, bg=C["panel"], fg=C["fg"], font=FONT, anchor="w"
                 ).grid(row=i, column=0, sticky="w", pady=S(3))
        make_entry(grid, vars_[key]).grid(row=i, column=1, padx=S(8), pady=S(3))
        tk.Label(grid, text=unit, bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w", width=13
                 ).grid(row=i, column=2, sticky="w")

    btn_row = tk.Frame(c_in, bg=C["panel"])
    btn_row.pack(fill="x", pady=(S(10), S(4)))
    make_button(btn_row, "Apply", lambda: apply_cal(), kind="accent", font=FONT_B).pack(side="left")
    make_button(btn_row, "Read", lambda: read_cal()).pack(side="left", padx=S(6))
    make_button(btn_row, "Save to Pico", lambda: save_cal()).pack(side="left")
    make_button(btn_row, "Defaults", lambda: defaults_cal(), kind="ghost").pack(side="right")

    tk.Frame(c_in, bg=C["line"], height=1).pack(fill="x", pady=S(10))
    tk.Label(c_in, text="Raw pulse test", bg=C["panel"], fg=C["dim"], font=FONT_B, anchor="w"
             ).pack(fill="x")
    raw_row = tk.Frame(c_in, bg=C["panel"])
    raw_row.pack(fill="x", pady=(S(4), 0))
    raw_var = tk.StringVar(value="1187")
    make_entry(raw_row, raw_var, width=7).pack(side="left")
    tk.Label(raw_row, text="\u00b5s", bg=C["panel"], fg=C["dim"], font=FONT_S).pack(side="left", padx=S(4))
    make_button(raw_row, "Send", lambda: send_raw(), padx=10).pack(side="left", padx=(S(6), S(4)))
    make_button(raw_row, "Set as 1 % start", lambda: raw_as_start(), padx=10).pack(side="left")

    # ========================================================================= console
    console_wrap = tk.Frame(root, bg=C["bg"])
    console_wrap.pack(fill="both", expand=True, padx=S(20), pady=(S(12), S(16)))
    con_head = tk.Frame(console_wrap, bg=C["bg"])
    con_head.pack(fill="x")
    tk.Label(con_head, text="CONSOLE", bg=C["bg"], fg=C["dim"], font=FONT_B).pack(side="left")
    show_stream = tk.BooleanVar(value=False)
    tk.Checkbutton(con_head, text="show live debug stream", variable=show_stream, bg=C["bg"],
                   fg=C["dim"], selectcolor=C["panel2"], activebackground=C["bg"],
                   activeforeground=C["fg"], font=FONT_S, bd=0, highlightthickness=0
                   ).pack(side="left", padx=S(14))
    make_button(con_head, "Clear", lambda: clear_console(), kind="ghost", padx=8, pady=2
                ).pack(side="right")

    # Entry row is packed to the bottom FIRST so it always keeps its space,
    # even if the window is short - it can never get squeezed to zero height.
    entry_row = tk.Frame(console_wrap, bg=C["console"], highlightthickness=1,
                         highlightbackground=C["line"])
    entry_row.pack(side="bottom", fill="x", pady=(S(6), 0))
    tk.Label(entry_row, text="\u203a", bg=C["console"], fg=C["accent"], font=("Consolas", 12, "bold")
             ).pack(side="left", padx=(S(10), S(4)))
    entry = tk.Entry(entry_row, bg=C["console"], fg=C["fg"], insertbackground=C["fg"], relief="flat",
                     font=MONO, highlightthickness=0, bd=0)
    entry.pack(side="left", fill="x", expand=True, pady=S(7))

    con_body = tk.Frame(console_wrap, bg=C["console"], highlightthickness=1,
                        highlightbackground=C["line"])
    con_body.pack(fill="both", expand=True, pady=(S(6), 0))
    console = tk.Text(con_body, height=8, bg=C["console"], fg=C["fg"], font=MONO, relief="flat",
                      wrap="word", state="disabled", bd=0, padx=S(10), pady=S(8),
                      insertbackground=C["fg"], highlightthickness=0)
    sb = ttk.Scrollbar(con_body, orient="vertical", command=console.yview)
    console.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    console.pack(side="left", fill="both", expand=True)
    console.tag_config("sent", foreground=C["accent"])
    console.tag_config("err", foreground=C["bad"])
    console.tag_config("ok", foreground=C["ok"])
    console.tag_config("dbg", foreground=C["dim"])
    console.tag_config("info", foreground=C["warn"])

    # ========================================================================= logic
    def log(text, tag=None):
        console.config(state="normal")
        console.insert("end", text + "\n", tag)
        n = int(console.index("end-1c").split(".")[0])
        if n > 2000:
            console.delete("1.0", f"{n - 2000}.0")
        console.see("end")
        console.config(state="disabled")

    def clear_console():
        console.config(state="normal")
        console.delete("1.0", "end")
        console.config(state="disabled")

    def send(text, echo=True):
        try:
            link.send(text)
        except Exception as e:
            log(f"[send failed: {e}]", "err")
            return False
        if echo:
            log("> " + text, "sent")
        return True

    def set_banner(text, kind=""):
        colors = {"trip": C["bad"], "info": C["warn"], "": C["warn"]}
        banner.config(text=text, fg=colors.get(kind, C["warn"]))
        state["banner_kind"] = kind if text else ""

    def refresh_ports():
        ports = [d for d, _ in list_ports()]
        port_box["values"] = ports
        if ports and port_var.get() not in ports:
            port_var.set(ports[0])

    def refresh_readouts():
        v = state["throttle"]
        pct_lbl.config(text=str(v))
        tgt = pct_to_us(v, pico["mn"], pico["st"], pico["mx"])
        if link.ser and state["have_status"] and pico["pulse"] is not None:
            detail_lbl.config(text=f"target {tgt} \u00b5s     actual {pico['pulse']} \u00b5s")
            slider.actual = pico["pct"]
        else:
            detail_lbl.config(text=f"target {tgt} \u00b5s     " + ("waiting for Pico\u2026" if link.ser else "not connected"))
            slider.actual = None
        slider.value = v
        slider.draw()

    def set_throttle(v, user=False):
        vf = max(0.0, min(100.0, float(v)))
        state["throttle_f"] = vf
        v = int(round(vf))
        state["throttle"] = v
        if user:
            if not link.ser:
                if not state["hint_shown"]:
                    set_banner("Not connected \u2013 connect to the Pico to send throttle.", "info")
                    state["hint_shown"] = True
            elif v != state["last_sent"]:
                if send(str(v), echo=False):
                    state["last_sent"] = v
                    if state["banner_kind"] in ("trip", "info"):
                        set_banner("")
        refresh_readouts()

    def do_stop():
        stop_ramp()
        if link.ser:
            send("stop")
        state["last_sent"] = 0
        set_banner("")
        set_throttle(0)

    def ramp_rate():
        try:
            v = float(ramp_rate_var.get())
            return v if v > 0 else 20.0
        except ValueError:
            return 20.0

    def ramp_tick():
        step = ramp_rate() * 0.05 * state["ramp_dir"]     # %/s -> % per 50ms tick
        set_throttle(state.get("throttle_f", state["throttle"]) + step, user=True)
        state["ramp_job"] = root.after(50, ramp_tick)

    def start_ramp(direction):
        if not link.ser:
            if not state["hint_shown"]:
                set_banner("Not connected \u2013 connect to the Pico to send throttle.", "info")
                state["hint_shown"] = True
            return
        if state["ramp_job"] is not None:
            return
        state["ramp_dir"] = direction
        (ramp_up_btn if direction > 0 else ramp_dn_btn).config(
            bg=C["accent"] if direction > 0 else C["panel2"])
        ramp_tick()

    def stop_ramp():
        if state["ramp_job"] is not None:
            root.after_cancel(state["ramp_job"])
            state["ramp_job"] = None
        state["ramp_dir"] = 0
        ramp_up_btn.config(bg=C["panel2"])
        ramp_dn_btn.config(bg=C["panel2"])

    def update_cfg_badge():
        cfg = pico.get("cfg")
        text, color = {
            "flash": ("\u25cf  Saved in Pico flash", C["ok"]),
            "defaults": ("\u25cf  Factory defaults (nothing saved)", C["dim"]),
            "modified": ("\u25cf  Unsaved changes on the Pico", C["warn"]),
        }.get(cfg, ("", C["dim"]))
        cfg_lbl.config(text=text, fg=color)

    def fill_entries():
        vars_["mn"].set(str(pico["mn"]))
        vars_["st"].set(str(pico["st"]))
        vars_["mx"].set(str(pico["mx"]))
        vars_["slew"].set(str(pico["slew"]))
        to = pico["timeout"]
        vars_["to"].set("0" if to == "off" else to.replace("ms", ""))

    def request_status(sync=True):
        state["sync"] = sync
        send("status", echo=False)

    def update_conn_ui(detail=None):
        if link.ser:
            waiting = time.time() - state["last_rx"] > 3.0 and state["have_status"]
            if waiting:
                color, text = C["warn"], "No data from Pico"
            else:
                color, text = C["ok"], f"Connected  {port_var.get()}"
        else:
            color, text = (C["bad"], detail) if detail else (C["dim"], "Disconnected")
        dot.itemconfig(dot_id, fill=color)
        conn_lbl.config(text=text, fg=C["fg"] if link.ser else C["dim"])
        conn_btn.config(text="Disconnect" if link.ser else "Connect")
        state["shown_conn"] = (bool(link.ser), text)

    def do_connect(target):
        if not target:
            log("[no serial ports found \u2013 plug in the Pico and press \u21bb]", "err")
            return
        try:
            link.connect(target)
        except Exception as e:
            log(f"[connect failed: {e}]", "err")
            update_conn_ui("Connect failed")
            return
        port_var.set(target)
        state.update(last_sent=None, have_status=False, last_rx=time.time(), hint_shown=False)
        set_banner("")
        log(f"[connected to {target}]", "info")
        send("debug on", echo=False)
        request_status(sync=True)
        update_conn_ui()
        refresh_readouts()

    def do_disconnect(msg="[disconnected]"):
        link.disconnect()
        state.update(have_status=False, last_sent=None)
        pico.update(pulse=None, pct=0, failsafe="ok")
        set_banner("")
        set_throttle(0)
        log(msg, "info")
        update_conn_ui()

    def toggle_connect():
        if link.ser:
            do_disconnect()
        else:
            do_connect(port_var.get())

    def on_trip():
        set_banner("Failsafe tripped \u2013 throttle was cut. Move the slider to resume.", "trip")
        state["last_sent"] = None
        set_throttle(0)

    # ---- calibration actions
    def need_link():
        if not link.ser:
            log("[not connected]", "err")
            return False
        return True

    def read_cal():
        if need_link():
            request_status(sync=True)

    def apply_cal():
        if not need_link():
            return
        try:
            new = dict(mn=int(vars_["mn"].get()), st=int(vars_["st"].get()),
                       mx=int(vars_["mx"].get()), slew=int(vars_["slew"].get()),
                       to=int(vars_["to"].get()))
        except ValueError:
            log("[calibration: enter whole numbers only]", "err")
            return
        if not cal_valid(new["mn"], new["st"], new["mx"]):
            log(f"[calibration: need {HARD_MIN} \u2264 off < start \u2264 max\u221250, max \u2264 {HARD_MAX}]", "err")
            return
        if not 1 <= new["slew"] <= 100:
            log("[calibration: ramp limit must be 1\u2013100]", "err")
            return
        if not (new["to"] == 0 or 200 <= new["to"] <= 60000):
            log("[calibration: failsafe must be 0 or 200\u201360000 ms]", "err")
            return
        cur_to = 0 if pico["timeout"] == "off" else int(pico["timeout"].replace("ms", ""))
        names = dict(mn="min", st="start", mx="max")
        order = safe_order(pico, new)
        if order:
            set_throttle(0)          # firmware cuts throttle whenever min/start/max change
            state["last_sent"] = 0
        for k in order:
            send(f"{names[k]} {new[k]}")
        if new["slew"] != pico["slew"]:
            send(f"slew {new['slew']}")
        if new["to"] != cur_to:
            send(f"timeout {new['to']}")
        request_status(sync=True)

    def save_cal():
        if need_link():
            set_throttle(0)
            state["last_sent"] = 0
            send("save")
            request_status(sync=True)

    def defaults_cal():
        if need_link() and messagebox.askyesno(
                "Factory defaults", "Restore factory values and erase what is saved on the Pico?"):
            set_throttle(0)
            state["last_sent"] = 0
            send("defaults")
            request_status(sync=True)

    def raw_value():
        try:
            v = int(raw_var.get())
        except ValueError:
            log("[raw pulse: enter a whole number]", "err")
            return None
        if not HARD_MIN <= v <= HARD_MAX:
            log(f"[raw pulse must be {HARD_MIN}\u2013{HARD_MAX} \u00b5s]", "err")
            return None
        return v

    def send_raw():
        v = raw_value()
        if v is not None and need_link():
            send(f"us {v}")
            state["last_sent"] = None
            set_banner(f"Raw pulse test: {v} \u00b5s. Move the slider or press STOP to leave it.", "info")

    def raw_as_start():
        v = raw_value()
        if v is not None and need_link():
            set_throttle(0)
            state["last_sent"] = 0
            send(f"start {v}")
            request_status(sync=True)

    # ---- console entry
    def on_enter(_=None):
        text = entry.get().strip()
        entry.delete(0, "end")
        if not text:
            return
        state["history"].append(text)
        state["hidx"] = len(state["history"])
        parts = text.split()
        cmd = parts[0].lower()
        if cmd == "clear":
            clear_console()
            return
        log("> " + text, "sent")
        if cmd in ("quit", "exit"):
            on_close()
        elif cmd == "ports":
            ports = list_ports()
            if not ports:
                log("  (no serial ports found)", "info")
            for dev, desc in ports:
                log(f"  {dev}  {desc}", "info")
        elif cmd == "connect":
            if link.ser:
                log("[already connected \u2013 'disconnect' first]", "err")
            else:
                ports = list_ports()
                target = parts[1] if len(parts) > 1 else (port_var.get() or (ports[0][0] if ports else None))
                do_connect(target)
        elif cmd == "disconnect":
            if link.ser:
                do_disconnect()
            else:
                log("[not connected]", "err")
        elif cmd == "help":
            log(GUI_HELP, "info")
            if link.ser:
                send(text, echo=False)
        else:
            if send(text, echo=False) and cmd in ("min", "start", "max", "slew", "timeout", "save", "defaults", "arm"):
                request_status(sync=True)

    def hist(delta):
        h = state["history"]
        if not h:
            return "break"
        state["hidx"] = max(0, min(len(h), state["hidx"] + delta))
        entry.delete(0, "end")
        if state["hidx"] < len(h):
            entry.insert(0, h[state["hidx"]])
        return "break"

    # ---- keyboard
    def typing_in_field():
        w = root.focus_get()
        return w is not None and w.winfo_class() in ("Entry", "TEntry", "TCombobox", "Text")

    def on_key(e):
        if typing_in_field():
            return
        step = {"Left": -1, "Right": 1, "Down": -5, "Up": 5, "Prior": 10, "Next": -10}.get(e.keysym)
        if step is not None:
            set_throttle(state["throttle"] + step, user=True)
            return "break"
        if e.keysym == "space":
            do_stop()
            return "break"
        if e.char.lower() == "r" and state["ramp_dir"] <= 0:
            start_ramp(1)
            return "break"
        if e.char.lower() == "f" and state["ramp_dir"] >= 0:
            start_ramp(-1)
            return "break"

    def on_key_release(e):
        if typing_in_field():
            return
        if e.char.lower() in ("r", "f"):
            stop_ramp()

    # ---- incoming lines
    def handle_line(l):
        state["last_rx"] = time.time()
        st = parse_status(l)
        if st:
            pico.update(st)
            state["have_status"] = True
            if state["sync"]:
                fill_entries()
                state["sync"] = False
            update_cfg_badge()
            if st["failsafe"] == "TRIPPED":
                if state["banner_kind"] != "trip":
                    on_trip()
            elif state["banner_kind"] == "trip":
                set_banner("")
            refresh_readouts()
        if l.startswith("DBG"):
            if show_stream.get():
                log(l, "dbg")
        elif l.startswith("WARN"):
            log(l, "err")
            if "failsafe" in l.lower() and state["banner_kind"] != "trip":
                on_trip()
        elif l.startswith(("ERR", "[link")):
            log(l, "err")
        elif l.startswith("OK"):
            log(l, "ok")
        else:
            log(l)

    def poll():
        try:
            while True:
                handle_line(q.get_nowait())
        except queue.Empty:
            pass
        if state["shown_conn"] is None or state["shown_conn"][0] != bool(link.ser):
            lost = state["shown_conn"] and state["shown_conn"][0] and not link.ser
            update_conn_ui("Link lost" if lost else None)
            if lost:
                state["have_status"] = False
                set_throttle(0)
        elif link.ser and (time.time() - state["last_rx"] > 3.0) != ("No data" in conn_lbl.cget("text")):
            update_conn_ui()
        root.after(40, poll)

    def on_close():
        link.disconnect()
        root.destroy()

    # ---- wire up
    entry.bind("<Return>", on_enter)
    entry.bind("<Up>", lambda e: hist(-1))
    entry.bind("<Down>", lambda e: hist(+1))
    root.bind("<Key>", on_key)
    root.bind("<KeyRelease>", on_key_release)
    root.bind_all("<Escape>", lambda e: do_stop())
    root.protocol("WM_DELETE_WINDOW", on_close)

    refresh_ports()
    if port:
        port_var.set(port)
    update_cfg_badge()
    update_conn_ui()
    refresh_readouts()
    log("Ready. Console accepts Pico commands plus ports / connect / disconnect / clear.  Type 'help'.", "info")
    poll()
    root.mainloop()


# ----------------------------------------------------------------------------- main

def main():
    args = sys.argv[1:]
    port = None
    if "--port" in args:
        i = args.index("--port")
        port = args[i + 1] if i + 1 < len(args) else None
    run_gui(port)


if __name__ == "__main__":
    main()
