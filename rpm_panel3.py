#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OBD-II real-time dashboard and logger.

- Polls ECU PIDs in fast / medium / slow groups
- Computes MAF-based and trim-corrected fuel flow
- Trapezoidal integration of total fuel used
- Heuristic gear estimation
- Live terminal dashboard (Rich + pyfiglet)
- Watchdog with auto-restart if FAST data stalls
- CSV logging with a stable header
Name 3 becasue i would be have correct version from this what i am using. 
"""

# -----------------------------------------------------------------------------
#Requires: '''
numpy==1.26.4
python-obd==0.7.2
rich==13.7.1
pyfiglet==1.0.2
'''
# -----------------------------------------------------------------------------
import numpy as np
if not hasattr(np, "unicode_"):
    np.unicode_ = np.str_
if not hasattr(np, "cumproduct"):
    np.cumproduct = np.cumprod

# -----------------------------------------------------------------------------
# Standard imports
# -----------------------------------------------------------------------------
import time
import csv
import threading
import os
import sys
import re
from datetime import datetime

# Third-party
import obd
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from pyfiglet import figlet_format

###############################################################################
# 1) OBD-II & logging settings
###############################################################################
PORT = "/dev/ttyUSB0"            # adjust to your interface (e.g., COM3 on Windows)
TIMEOUT = 2                      # OBD timeout (seconds)
CSV_FILENAME = os.environ.get(
    "CSV_FILENAME",
    f"ecu_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
)
# Remember the name across restarts (used by watchdog exec)
os.environ["CSV_FILENAME"] = CSV_FILENAME

###############################################################################
# 2) PIDs split by polling frequency
###############################################################################
commands_fast = [  # ~ every 1 second
    obd.commands.RPM,
    obd.commands.SPEED,
    obd.commands.THROTTLE_POS,
    obd.commands.RELATIVE_THROTTLE_POS,
    obd.commands.MAF,
    obd.commands.ENGINE_LOAD,
    obd.commands.ABSOLUTE_LOAD,
    obd.commands.INTAKE_PRESSURE,
    obd.commands.INTAKE_TEMP,
    obd.commands.ACCELERATOR_POS_D,
    obd.commands.SHORT_FUEL_TRIM_1,  # STFT
    obd.commands.LONG_FUEL_TRIM_1,   # LTFT
]
commands_medium = [  # ~ every 15 seconds
    obd.commands.O2_B1S2,
    obd.commands.O2_B1S1,
]
commands_slow = [  # ~ every 30 seconds
    obd.commands.FUEL_LEVEL,
    obd.commands.ELM_VOLTAGE,
    obd.commands.COOLANT_TEMP,
]
all_commands = commands_fast + commands_medium + commands_slow

###############################################################################
# 3) Globals
###############################################################################
console = Console()
connection = obd.OBD(PORT, timeout=TIMEOUT)

if not connection.is_connected():
    console.print("[bold red]❌ Failed to connect to ECU[/bold red]")
    sys.exit(1)
console.print("[bold green]✅ Connected to ECU[/bold green]")

# Pre-fill with placeholders for all known keys
live_data = {cmd.name: "..." for cmd in all_commands}

# Stable header + our computed fields
live_data.update({
    "FUEL_USAGE_ML_MIN": "-",
    "FUEL_USED_TOTAL_ML": "0.0",          # base (MAF) – trapezoidal integration
    "REAL_FUEL_USAGE_ML_MIN": "-",
    "REAL_FUEL_USED_TOTAL_ML": "0.0",     # trim-corrected – trapezoidal integration
})

csv_header_written = False
csv_field_order = []  # frozen at first write to keep a stable CSV header

# Totals (ml) – trapezoidal integration
fuel_used_total_ml = 0.0           # base (MAF only)
real_fuel_used_total_ml = 0.0      # corrected (MAF * trims)

# Last flow values (ml/min) for the trapezoidal rule
_last_base_ml_min = None
_last_real_ml_min = None

# Loop timing
_last_loop_ts = time.time()

# If the loop sleeps too long (e.g., system sleep), skip that "gap"
MAX_TRAPZ_DT_S = 5.0

# Regex to extract the first float from a string
RX_NUM = re.compile(r"([-+]?\d*\.?\d+)")

###############################################################################
# 4) Helpers
###############################################################################
def parse_first_float(value: str, default: float = 0.0) -> float:
    """Extract the first float from `value` or return `default`."""
    if not value or value == "No data" or value == "...":
        return default
    s = str(value)
    m = RX_NUM.search(s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return default
    return default

def calculate_fuel_usage_maf(maf_g_s: float) -> float:
    """Convert MAF [g/s] to fuel usage [ml/min] assuming stoichiometric AFR."""
    AFR = 14.7           # gasoline stoichiometric air–fuel ratio
    FUEL_DENSITY = 0.745 # g/ml (approx. gasoline density)
    fuel_g_s = maf_g_s / AFR
    fuel_ml_s = fuel_g_s / FUEL_DENSITY
    return fuel_ml_s * 60.0  # ml/min

def calculate_real_fuel_usage(maf_g_s: float, stft_pp: float, ltft_pp: float) -> float:
    """Trim-corrected fuel usage [ml/min]."""
    base_ml_min = calculate_fuel_usage_maf(maf_g_s)
    factor = (1.0 + ltft_pp / 100.0) * (1.0 + stft_pp / 100.0)
    return base_ml_min * factor

def calculate_gear(rpm: int, speed: int) -> str:
    """Heuristic gear estimate based on RPM/Speed ratio."""
    if speed < 2 or rpm < 600:
        return "N"
    ratio = rpm / (speed or 1)
    if 130 >= ratio > 90:
        return "1"
    elif 90 >= ratio > 60:
        return "2"
    elif 60 >= ratio > 45:
        return "3"
    elif 45 >= ratio > 35:
        return "4"
    elif 35 >= ratio > 25:
        return "5"
    else:
        return "?"

###############################################################################
# 5) OBD read loop (trapezoidal integration)
###############################################################################
def read_obd_loop():
    global csv_header_written, csv_field_order
    global fuel_used_total_ml, real_fuel_used_total_ml
    global _last_loop_ts, _last_base_ml_min, _last_real_ml_min

    sec_count = 0

    while True:
        now = time.time()
        dt_s = max(0.0, now - _last_loop_ts)   # delta time [s]
        _last_loop_ts = now

        sec_count += 1
        row = {}

        # ---------- FAST ----------
        for cmd in commands_fast:
            try:
                response = connection.query(cmd)
                value = str(response.value) if not response.is_null() else "No data"
            except Exception as e:
                value = f"error: {e}"
            row[cmd.name] = value

        # ---------- MEDIUM ----------
        if sec_count % 15 == 0:
            for cmd in commands_medium:
                try:
                    response = connection.query(cmd)
                    value = str(response.value) if not response.is_null() else "No data"
                except Exception as e:
                    value = f"error: {e}"
                row[cmd.name] = value

        # ---------- SLOW ----------
        if sec_count % 30 == 0:
            for cmd in commands_slow:
                try:
                    response = connection.query(cmd)
                    value = str(response.value) if not response.is_null() else "No data"
                except Exception as e:
                    value = f"error: {e}"
                row[cmd.name] = value

        # ---------- Update live data ----------
        live_data.update(row)

        # ---------- Fuel calculations + trapezoidal integration ----------
        maf_g_s = parse_first_float(live_data.get("MAF"), default=0.0)
        stft_pp = parse_first_float(live_data.get("SHORT_FUEL_TRIM_1"), default=0.0)
        ltft_pp = parse_first_float(live_data.get("LONG_FUEL_TRIM_1"), default=0.0)

        # current flow rates [ml/min]
        if maf_g_s > 0.0:
            try:
                base_ml_min = calculate_fuel_usage_maf(maf_g_s)
                real_ml_min = calculate_real_fuel_usage(maf_g_s, stft_pp, ltft_pp)

                # update live instantaneous values
                live_data["FUEL_USAGE_ML_MIN"] = f"{base_ml_min:.1f}"
                live_data["REAL_FUEL_USAGE_ML_MIN"] = f"{real_ml_min:.1f}"

                # trapezoidal integration if we have previous points and a sane dt
                if (
                    _last_base_ml_min is not None
                    and _last_real_ml_min is not None
                    and 0.0 < dt_s <= MAX_TRAPZ_DT_S
                ):
                    dt_min = dt_s / 60.0
                    # base
                    fuel_used_total_ml += 0.5 * (_last_base_ml_min + base_ml_min) * dt_min
                    # real (trim-corrected)
                    real_fuel_used_total_ml += 0.5 * (_last_real_ml_min + real_ml_min) * dt_min
                # otherwise: first point or a gap — just set the baselines

                # set previous values for the next trapezoid
                _last_base_ml_min = base_ml_min
                _last_real_ml_min = real_ml_min

                # update total counters
                live_data["FUEL_USED_TOTAL_ML"] = f"{fuel_used_total_ml:.1f}"
                live_data["REAL_FUEL_USED_TOTAL_ML"] = f"{real_fuel_used_total_ml:.1f}"

            except Exception:
                live_data["FUEL_USAGE_ML_MIN"] = "MAF error"
                live_data["REAL_FUEL_USAGE_ML_MIN"] = "calc error"
                # reset to avoid integrating through a bad sample
                _last_base_ml_min = None
                _last_real_ml_min = None
        else:
            # no MAF → do not integrate; reset previous sample
            live_data["FUEL_USAGE_ML_MIN"] = "-"
            live_data["REAL_FUEL_USAGE_ML_MIN"] = "-"
            _last_base_ml_min = None
            _last_real_ml_min = None

        # ---------- CSV write ----------
        try:
            with open(CSV_FILENAME, mode="a", newline="") as file:
                writer = csv.writer(file)
                if not csv_header_written:
                    csv_field_order = list(live_data.keys())
                    header = ["timestamp"] + csv_field_order + ["GEAR"]
                    writer.writerow(header)
                    csv_header_written = True

                rpm = int(parse_first_float(live_data.get("RPM"), default=0.0))
                speed = int(parse_first_float(live_data.get("SPEED"), default=0.0))
                gear = calculate_gear(rpm, speed)

                row_csv = [datetime.now().isoformat()] + [live_data[k] for k in csv_field_order] + [gear]
                writer.writerow(row_csv)
        except Exception as e:
            console.log(f"[red]❌ CSV write error:[/red] {e}")

        time.sleep(1)

###############################################################################
# 6) Watchdog – restart if FAST data stalled
###############################################################################
def watchdog_loop():
    last_fast_seen = time.time()
    while True:
        # monitor FAST group by checking MAF
        maf_val = parse_first_float(live_data.get("MAF"), default=0.0)
        if maf_val > 0.0:
            last_fast_seen = time.time()
        # if no FAST data for >5 s → hard restart the process
        if time.time() - last_fast_seen > 5:
            console.log("[bold red]⚠️ No FAST data — restarting app![/bold red]")
            time.sleep(1)
            os.execvpe(sys.executable, [sys.executable] + sys.argv, os.environ)
        time.sleep(1)

###############################################################################
# 7) Panels rendering
###############################################################################
def render_left_panel():
    try:
        rpm = int(parse_first_float(live_data.get("RPM"), default=0.0))
    except ValueError:
        rpm = 0
    try:
        speed = int(parse_first_float(live_data.get("SPEED"), default=0.0))
    except ValueError:
        speed = 0

    gear = calculate_gear(rpm, speed)

    rpm_text_big = figlet_format(str(rpm), font="digital")
    rpm_text = Text(rpm_text_big, style="bold cyan")

    speed_text_big = figlet_format(str(speed), font="big")
    speed_text = Text(speed_text_big, style="bold green")

    info = Text()
    info.append(f"RPM:   {rpm}\n", style="bold")
    info.append(f"SPEED: {speed} km/h\n", style="bold")
    info.append(f"GEAR:  {gear}\n", style="bold yellow")
    info.append(f"FUEL (base): {live_data.get('FUEL_USAGE_ML_MIN', '-') } ml/min\n", style="bold magenta")
    info.append(f"FUEL (real): {live_data.get('REAL_FUEL_USAGE_ML_MIN', '-') } ml/min\n", style="bold magenta")
    info.append(f"Σ base: {live_data.get('FUEL_USED_TOTAL_ML','0')} ml (trapz)\n", style="bold")
    info.append(f"Σ real: {live_data.get('REAL_FUEL_USED_TOTAL_ML','0')} ml (trapz)\n", style="bold")

    group = Group(Text("SPEED", style="bold underline", justify="center"), speed_text, rpm_text, info)
    return Panel(group, title="Opel Corsa D 1.2", border_style="cyan")

def render_ecu_panel():
    table = Table(title="🧠 ECU Live Data", show_header=True, expand=True)
    table.add_column("Parameter", style="bold cyan")
    table.add_column("Value", style="bold green")
    for key, value in live_data.items():
        table.add_row(key, str(value))
    return Panel(table, border_style="magenta")

###############################################################################
# 8) Main UI loop
###############################################################################
def main():
    threading.Thread(target=read_obd_loop, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()

    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=2),
        Layout(name="bottom", ratio=3)
    )

    with Live(layout, refresh_per_second=2, screen=True):
        while True:
            layout["top"].update(render_left_panel())
            layout["bottom"].update(render_ecu_panel())
            time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]⏹️ Stopped by user[/bold yellow]")
