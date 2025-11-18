#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OBD-II PID scanner → PDF report.

- Lists all python-OBD commands
- Checks ECU support for each PID
- Queries supported PIDs once
- Generates a PDF report with a table

Requires: python-obd, fpdf2, rich (optional, for nicer logs)
"""

import sys
import os

import obd
from obd import OBDCommand
from rich.console import Console
from fpdf import FPDF

# ---------------------------------------------------------------------
# Connection settings
# ---------------------------------------------------------------------
PORT = "/dev/ttyUSB0"       # change to your interface (e.g. "COM5" on Windows)
TIMEOUT = 2                 # seconds
PDF_FILENAME = os.environ.get("PID_REPORT_PDF", "obd_pid_report.pdf")

console = Console()


def collect_all_commands() -> list[OBDCommand]:
    """
    Collect all OBDCommand objects defined in python-OBD.
    """
    cmds = []
    for name in dir(obd.commands):
        attr = getattr(obd.commands, name)
        if isinstance(attr, OBDCommand):
            cmds.append(attr)
    # Sort by mode and PID for nicer output
    cmds.sort(key=lambda c: (c.mode, c.command))
    return cmds


class PIDReportPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, "OBD-II PID Scan Report", ln=1, align="C")
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, f"Port: {PORT}", ln=1, align="C")
        self.ln(3)

        # Table header
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(230, 230, 230)

        col_widths = [15, 18, 40, 20, 70, 20]  # Mode, PID, Name, Supported, Value, Unit
        headers = ["Mode", "PID", "Name", "Supported", "Value (sample)", "Unit"]

        for w, h in zip(col_widths, headers):
            self.cell(w, 6, h, border=1, align="C", fill=True)
        self.ln()

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def truncate(text: str, max_len: int) -> str:
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def main():
    console.print("[bold]Connecting to OBD-II interface...[/bold]")
    connection = obd.OBD(PORT, timeout=TIMEOUT)

    if not connection.is_connected():
        console.print(f"[bold red]❌ Failed to connect on port {PORT}[/bold red]")
        sys.exit(1)

    console.print(f"[bold green]✅ Connected to ECU on {PORT}[/bold green]\n")

    all_cmds = collect_all_commands()
    console.print(f"[bold]Total python-OBD commands found:[/bold] {len(all_cmds)}")

    rows = []
    supported_count = 0

    for cmd in all_cmds:
        try:
            is_supported = connection.supports(cmd)
        except Exception as e:
            console.log(f"[red]Error checking support for {cmd.name}: {e}[/red]")
            is_supported = False

        value_str = ""
        unit_str = ""

        if is_supported:
            supported_count += 1
            try:
                resp = connection.query(cmd)
                if not resp.is_null():
                    value_str = str(resp.value)
                    try:
                        unit_str = getattr(resp.value, "units", "") or ""
                    except Exception:
                        unit_str = ""
                else:
                    value_str = "No data"
            except Exception as e:
                value_str = f"error: {e}"

        rows.append(
            {
                "mode": cmd.mode,
                "pid": cmd.command,
                "name": cmd.name,
                "supported": "YES" if is_supported else "NO",
                "value": value_str,
                "unit": unit_str,
            }
        )

    console.print(f"\n[bold]Supported PIDs:[/bold] {supported_count}")

    # ---------------- PDF generation ----------------
    console.print(f"[bold]Generating PDF report:[/bold] {PDF_FILENAME}")

    pdf = PIDReportPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 8)

    col_widths = [15, 18, 40, 20, 70, 20]  # must match header

    for r in rows:
        pdf.cell(col_widths[0], 5, str(r["mode"]), border=1)
        pdf.cell(col_widths[1], 5, str(r["pid"]), border=1)
        pdf.cell(col_widths[2], 5, truncate(r["name"], 22), border=1)
        pdf.cell(col_widths[3], 5, r["supported"], border=1, align="C")
        pdf.cell(col_widths[4], 5, truncate(r["value"], 40), border=1)
        pdf.cell(col_widths[5], 5, truncate(r["unit"], 10), border=1)
        pdf.ln()

    try:
        pdf.output(PDF_FILENAME)
        console.print(f"[bold green]💾 PDF report saved to:[/bold green] {PDF_FILENAME}")
    except Exception as e:
        console.print(f"[bold red]❌ Failed to write PDF:[/bold red] {e}")


if __name__ == "__main__":
    main()
