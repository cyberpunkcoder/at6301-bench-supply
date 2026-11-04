"""AT6301 bench supply controller — live readout, plot, control, CSV logging.

Run:  uv run --with pyserial --with matplotlib python at6301_gui.py
Protocol: SCPI over USB-serial (CH340), 115200 8N1, LF-terminated.
"""
import csv, time, threading, queue, datetime, collections, tkinter as tk
from tkinter import ttk, filedialog, messagebox
import serial, serial.tools.list_ports
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

BAUD = 115200
POLL_S = 0.05            # ~18 Hz is the supply's ceiling
PLOT_WINDOW_S = 60.0
MAX_V, MAX_I, MAX_P = 60.0, 10.0, 300.0


class PSU:
    """Thread-safe SCPI wrapper. One serial write/read at a time."""
    def __init__(self):
        self.ser = None; self.lock = threading.Lock(); self.idn = ""

    def open(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=0.3)
        time.sleep(0.2)
        self.idn = self.query("*IDN?")
        if "AT6301" not in self.idn and "Abestop" not in self.idn:
            raise IOError(f"unexpected device: {self.idn!r}")

    def close(self):
        if self.ser:
            try: self.ser.close()
            except Exception: pass
        self.ser = None

    def query(self, cmd):
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\n").encode())
            return self.ser.read_until(b"\n").decode(errors="ignore").strip()

    def write(self, cmd):
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\n").encode())
            time.sleep(0.05)
            self.ser.read(64)  # swallow any echo

    def measure(self):
        r = self.query("MEAS:ALL?")
        v, i = (float(x) for x in r.split(","))
        return v, i

    def set_volt(self, v): self.write(f"VOLT {v:.3f}")
    def set_curr(self, i): self.write(f"CURR {i:.3f}")
    def output(self, on): self.write("OUTP ON" if on else "OUTP OFF")
    def get_setpoints(self): return float(self.query("VOLT?")), float(self.query("CURR?")), self.query("OUTP?") == "ON"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AT6301 Bench Supply"); self.geometry("1100x720"); self.minsize(900, 600)
        self.psu = PSU(); self.q = queue.Queue(); self.running = False; self.poll_thread = None
        self.t0 = None; self.phase = "idle"
        self.hist = collections.deque(maxlen=int(PLOT_WINDOW_S / POLL_S) + 50)
        self.log_file = None; self.log_writer = None; self.log_n = 0; self.log_acc = 0.0; self.log_pk = 0.0; self.log_pk_i = 0.0
        self._build(); self._refresh_ports(); self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------
    def _build(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("Big.TLabel", font=("Consolas", 34, "bold"))
        s.configure("Unit.TLabel", font=("Segoe UI", 12))
        s.configure("On.TButton", foreground="white", background="#1e8e3e")
        s.configure("Off.TButton", foreground="white", background="#b3261e")
        s.map("On.TButton", background=[("active", "#167a33")]); s.map("Off.TButton", background=[("active", "#8f1e18")])

        top = ttk.Frame(self, padding=8); top.pack(fill="x")
        ttk.Label(top, text="Port").pack(side="left")
        self.port_cb = ttk.Combobox(top, width=28, state="readonly"); self.port_cb.pack(side="left", padx=4)
        ttk.Button(top, text="⟳", width=3, command=self._refresh_ports).pack(side="left")
        self.conn_btn = ttk.Button(top, text="Connect", command=self._toggle_conn); self.conn_btn.pack(side="left", padx=8)
        self.idn_lbl = ttk.Label(top, text="not connected", foreground="#666"); self.idn_lbl.pack(side="left", padx=8)

        body = ttk.Frame(self); body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=8, width=300); left.pack(side="left", fill="y"); left.pack_propagate(False)

        # live readouts
        ro = ttk.LabelFrame(left, text="Live", padding=8); ro.pack(fill="x")
        self.v_lbl = self._readout(ro, "V"); self.i_lbl = self._readout(ro, "A"); self.p_lbl = self._readout(ro, "W")

        # control
        ctl = ttk.LabelFrame(left, text="Control", padding=8); ctl.pack(fill="x", pady=8)
        self.v_set = tk.StringVar(value="12.000"); self.i_set = tk.StringVar(value="1.000")
        for r, (lab, var) in enumerate((("Voltage (V)", self.v_set), ("Current limit (A)", self.i_set))):
            ttk.Label(ctl, text=lab).grid(row=r, column=0, sticky="w", pady=3)
            e = ttk.Entry(ctl, textvariable=var, width=10, font=("Consolas", 12)); e.grid(row=r, column=1, padx=4)
            e.bind("<Return>", lambda _e: self._apply())
        ttk.Button(ctl, text="Apply", command=self._apply).grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)
        self.out_btn = ttk.Button(ctl, text="OUTPUT OFF", style="Off.TButton", command=self._toggle_output)
        self.out_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4, ipady=6)
        self.out_on = False
        ttk.Label(ctl, text="OVP/OCP: set on front panel", foreground="#666", font=("Segoe UI", 8)).grid(row=4, column=0, columnspan=2, sticky="w")

        # logging
        lg = ttk.LabelFrame(left, text="Logging", padding=8); lg.pack(fill="x")
        ttk.Label(lg, text="Phase label").grid(row=0, column=0, sticky="w")
        self.phase_var = tk.StringVar(value="idle")
        pe = ttk.Entry(lg, textvariable=self.phase_var, width=14); pe.grid(row=0, column=1, padx=4)
        pe.bind("<Return>", lambda _e: self._mark())
        ttk.Button(lg, text="Mark", command=self._mark).grid(row=0, column=2)
        self.log_btn = ttk.Button(lg, text="Start log…", command=self._toggle_log); self.log_btn.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        self.log_stat = ttk.Label(lg, text="", foreground="#666", font=("Consolas", 9), justify="left"); self.log_stat.grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Button(lg, text="Reset plot/stats", command=self._reset).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        # plot
        fig = Figure(figsize=(7, 5), dpi=100, tight_layout=True)
        self.ax_v = fig.add_subplot(311); self.ax_i = fig.add_subplot(312, sharex=self.ax_v); self.ax_p = fig.add_subplot(313, sharex=self.ax_v)
        for ax, lab, col in ((self.ax_v, "V", "#1f77b4"), (self.ax_i, "A", "#d62728"), (self.ax_p, "W", "#2ca02c")):
            ax.set_ylabel(lab); ax.grid(True, alpha=0.3)
        self.ln_v, = self.ax_v.plot([], [], color="#1f77b4", lw=1.2)
        self.ln_i, = self.ax_i.plot([], [], color="#d62728", lw=1.2)
        self.ln_p, = self.ax_p.plot([], [], color="#2ca02c", lw=1.2)
        self.ax_p.set_xlabel("t (s)")
        self.canvas = FigureCanvasTkAgg(fig, master=body); self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True)
        self.after(200, self._redraw)

    def _readout(self, parent, unit):
        f = ttk.Frame(parent); f.pack(fill="x")
        l = ttk.Label(f, text="—", style="Big.TLabel", width=8, anchor="e"); l.pack(side="left")
        ttk.Label(f, text=unit, style="Unit.TLabel").pack(side="left", padx=(6, 0), anchor="s", pady=(0, 10))
        return l

    # ---------- connection ----------
    def _refresh_ports(self):
        ports = list(serial.tools.list_ports.comports())
        names = [f"{p.device}  {p.description}" for p in ports]
        self.port_cb["values"] = names
        pick = next((n for n in names if "CH340" in n), names[0] if names else "")
        self.port_cb.set(pick)

    def _toggle_conn(self):
        if self.running: self._disconnect(); return
        sel = self.port_cb.get().split()[0] if self.port_cb.get() else ""
        if not sel: messagebox.showerror("Port", "No serial port selected"); return
        try:
            self.psu.open(sel)
            v, i, on = self.psu.get_setpoints()
        except Exception as e:
            self.psu.close(); messagebox.showerror("Connect", str(e)); return
        self.v_set.set(f"{v:.3f}"); self.i_set.set(f"{i:.3f}"); self._set_out_btn(on)
        self.idn_lbl.config(text=self.psu.idn, foreground="#1e8e3e"); self.conn_btn.config(text="Disconnect")
        self.t0 = time.time(); self.running = True
        self.poll_thread = threading.Thread(target=self._poll, daemon=True); self.poll_thread.start()

    def _disconnect(self):
        self.running = False
        if self.poll_thread: self.poll_thread.join(timeout=1)
        if self.log_file: self._toggle_log()
        self.psu.close(); self.idn_lbl.config(text="not connected", foreground="#666"); self.conn_btn.config(text="Connect")

    def _poll(self):
        while self.running:
            try:
                v, i = self.psu.measure()
                self.q.put((time.time(), v, i))
            except Exception as e:
                self.q.put(("err", str(e)))
            time.sleep(POLL_S)

    # ---------- control ----------
    def _apply(self):
        if not self.running: return
        try:
            v = float(self.v_set.get()); i = float(self.i_set.get())
            if not (0 <= v <= MAX_V and 0 <= i <= MAX_I): raise ValueError("out of range")
            self.psu.set_volt(v); self.psu.set_curr(i)
            rv, ri, _ = self.psu.get_setpoints()
            self.v_set.set(f"{rv:.3f}"); self.i_set.set(f"{ri:.3f}")
        except Exception as e:
            messagebox.showerror("Apply", str(e))

    def _toggle_output(self):
        if not self.running: return
        self.psu.output(not self.out_on)
        _, _, on = self.psu.get_setpoints(); self._set_out_btn(on)
        self._mark(f"output {'on' if on else 'off'}", keep=True)

    def _set_out_btn(self, on):
        self.out_on = on
        self.out_btn.config(text="OUTPUT ON" if on else "OUTPUT OFF", style="On.TButton" if on else "Off.TButton")

    # ---------- logging ----------
    def _mark(self, label=None, keep=False):
        lab = label or self.phase_var.get().strip() or "unlabeled"
        if not keep: self.phase = lab
        if self.log_writer:
            self.log_writer.writerow([datetime.datetime.now().isoformat(timespec="milliseconds"), f"{time.time()-self.t0:.3f}", "MARK", "", "", "", lab])

    def _toggle_log(self):
        if self.log_file:
            self.log_file.close(); self.log_file = None; self.log_writer = None
            self.log_btn.config(text="Start log…"); return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"psu_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv")
        if not path: return
        self.log_file = open(path, "w", newline="", encoding="utf-8")
        self.log_writer = csv.writer(self.log_file)
        self.log_writer.writerow(["# device", self.psu.idn]); self.log_writer.writerow(["# started", datetime.datetime.now().isoformat()])
        self.log_writer.writerow(["timestamp_iso", "t_s", "phase", "voltage_V", "current_A", "power_W", "note"])
        self.log_n = 0; self.log_acc = 0.0; self.log_pk = 0.0; self.log_pk_i = 0.0
        self.log_btn.config(text=f"Stop log ({path.split('/')[-1]})")

    def _reset(self):
        self.hist.clear(); self.t0 = time.time(); self.log_n = 0; self.log_acc = 0.0; self.log_pk = 0.0; self.log_pk_i = 0.0

    # ---------- main loop ----------
    def _drain(self):
        last = None
        while not self.q.empty():
            item = self.q.get_nowait()
            if item[0] == "err": self.idn_lbl.config(text=f"error: {item[1][:40]}", foreground="#b3261e"); continue
            ts, v, i = item; p = v * i; t = ts - self.t0
            self.hist.append((t, v, i, p)); last = (v, i, p)
            if self.log_writer:
                self.log_writer.writerow([datetime.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds"), f"{t:.3f}", self.phase, f"{v:.3f}", f"{i:.3f}", f"{p:.3f}", ""])
                self.log_n += 1; self.log_acc += p; self.log_pk = max(self.log_pk, p); self.log_pk_i = max(self.log_pk_i, i)
        if last:
            v, i, p = last
            self.v_lbl.config(text=f"{v:6.2f}"); self.i_lbl.config(text=f"{i:6.3f}"); self.p_lbl.config(text=f"{p:6.2f}")
        if self.log_writer:
            self.log_stat.config(text=f"{self.log_n} samples\navg {self.log_acc/max(self.log_n,1):.2f} W\npeak {self.log_pk:.2f} W / {self.log_pk_i:.3f} A")
            if self.log_n % 20 == 0: self.log_file.flush()
        self.after(100, self._drain)

    def _redraw(self):
        if self.hist:
            tnow = self.hist[-1][0]
            pts = [h for h in self.hist if h[0] >= tnow - PLOT_WINDOW_S]
            ts = [h[0] for h in pts]
            self.ln_v.set_data(ts, [h[1] for h in pts]); self.ln_i.set_data(ts, [h[2] for h in pts]); self.ln_p.set_data(ts, [h[3] for h in pts])
            self.ax_v.set_xlim(max(0, tnow - PLOT_WINDOW_S), max(tnow, PLOT_WINDOW_S * 0.1))
            for ax, idx, cap in ((self.ax_v, 1, MAX_V), (self.ax_i, 2, MAX_I), (self.ax_p, 3, MAX_P)):
                m = max((h[idx] for h in pts), default=1.0)
                ax.set_ylim(0, min(cap, max(m * 1.15, 0.1)))
            self.canvas.draw_idle()
        self.after(250, self._redraw)

    def _on_close(self):
        if self.running: self._disconnect()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
