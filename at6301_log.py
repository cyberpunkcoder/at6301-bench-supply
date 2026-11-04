"""AT6301 power logger. Logs V, I, P at max rate to CSV and prints live stats.
Usage: uv run --with pyserial python at6301_log.py COM4 out.csv [--set 48 --limit 6]
Press Ctrl+C to stop. Type a label + Enter at any time to mark a phase (e.g. 'idle', 'load', 'peak').
"""
import sys, time, csv, threading, serial, argparse

ap = argparse.ArgumentParser()
ap.add_argument("port"); ap.add_argument("out")
ap.add_argument("--set", type=float, help="set output voltage before logging")
ap.add_argument("--limit", type=float, help="set current limit before logging")
ap.add_argument("--on", action="store_true", help="turn output on before logging")
a = ap.parse_args()

label = ["start"]
def reader():
    for line in sys.stdin:
        label[0] = line.strip() or label[0]
        print(f"--- phase: {label[0]}")
threading.Thread(target=reader, daemon=True).start()

with serial.Serial(a.port, 115200, timeout=0.3) as s, open(a.out, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["t_s", "phase", "V", "I", "P"])
    def q(c):
        s.reset_input_buffer(); s.write((c + "\n").encode()); return s.read_until(b"\n").decode(errors="ignore").strip()
    print("IDN:", q("*IDN?"))
    if a.set is not None: print("set V:", q(f"VOLT {a.set:.3f}") or "ok", "->", q("VOLT?"))
    if a.limit is not None: print("set I:", q(f"CURR {a.limit:.3f}") or "ok", "->", q("CURR?"))
    if a.on: q("OUTP ON"); print("output:", q("OUTP?"))
    t0 = time.time(); n = 0; pk = 0.0; acc = 0.0; last = t0
    try:
        while True:
            r = q("MEAS:ALL?")
            try: V, I = (float(x) for x in r.split(","))
            except ValueError: continue
            t = time.time() - t0; P = V * I; n += 1; acc += P; pk = max(pk, P)
            w.writerow([f"{t:.3f}", label[0], f"{V:.3f}", f"{I:.3f}", f"{P:.2f}"])
            if time.time() - last > 1.0:
                f.flush(); last = time.time()
                print(f"\r{t:7.1f}s  {V:6.2f} V  {I:6.3f} A  {P:7.2f} W   avg {acc/n:6.1f} W  peak {pk:6.1f} W  [{label[0]}]   ", end="")
    except KeyboardInterrupt:
        print(f"\nstopped. {n} samples, avg {acc/max(n,1):.1f} W, peak {pk:.1f} W -> {a.out}")
