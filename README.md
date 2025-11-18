# obd2-live-panel

**OBD-II real‑time dashboard and logger for Python.**  
Reads ECU PIDs in fast/medium/slow groups, computes MAF‑based and trim‑corrected fuel usage, integrates totals with the trapezoidal rule, and estimates gear from the RPM/SPEED ratio. Includes a Rich/pyfiglet terminal UI, watchdog auto‑restart, and CSV logging with a stable header.

> Dataset collected with this tool: **OBD2_panel_opel_2012** (Opel Corsa 1.2 A12XER, 2012).  
> Logger code here is the same family used to produce that dataset.

---

## ✨ Features

- **PID polling tiers**
  - **Fast (~1s):** `RPM`, `SPEED`, `THROTTLE_POS`, `RELATIVE_THROTTLE_POS`, `MAF`, `ENGINE_LOAD`, `ABSOLUTE_LOAD`, `INTAKE_PRESSURE`, `INTAKE_TEMP`, `ACCELERATOR_POS_D`, `SHORT_FUEL_TRIM_1`, `LONG_FUEL_TRIM_1`
  - **Medium (~15s):** `O2_B1S1`, `O2_B1S2`
  - **Slow (~30s):** `FUEL_LEVEL`, `ELM_VOLTAGE`, `COOLANT_TEMP`
- **Fuel model**
  - MAF → fuel [ml/min], trim‑corrected instantaneous usage
  - Trapezoidal integration of total fuel used (base & corrected)
- **Heuristic gear estimation** from RPM/SPEED
- **Live terminal dashboard** (Rich + pyfiglet)
- **Watchdog**: restarts the app if FAST data stalls
- **CSV output**: timestamp + all raw and derived fields with a stable header

---

## 📦 Requirements

Pinned to the versions used during development:

numpy==1.26.4
python-obd==0.7.2
rich==13.7.1
pyfiglet==1.0.2


Recommended Python: **3.12.2**

> Standard library modules (`csv`, `time`, `threading`, `os`, `re`, `datetime`) are built‑ins and are **not** listed in `requirements.txt`.

---

## ⚙️ Installation

```bash
# 1) (optional) create & activate a virtual environment
python -m venv .venv && source .venv/bin/activate  # on Windows: .venv\Scripts\activate

# 2) install dependencies
pip install -r requirements.txt

# 3) or normal copy/paste and run the script manually
```

---

## My recommendation

Try scanning all available PIDs first to see what your ECU supports.  
I’m using the OBDLink EX Multiprotocol OBD-II Scan Tool (~80 EUR). After that, you can modify your panel based on the supported data.

Be careful after any ECU update, service, or app change — some PIDs may behave differently or become hidden. Always re-scan to detect potential issues.
