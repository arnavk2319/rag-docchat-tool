"""Regenerate the eval fixtures (synthetic travel documents plus docs/spec.md; run once, then commit).

    uv run python -m evals.make_fixtures

WARNING: golden.jsonl pins facts to the committed snapshots. Do not regenerate casually.
The travel documents are invented (fictional hotel, address, dates, checklist). Reads only docs/spec.md.
"""

from __future__ import annotations

import re
import textwrap
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "fixtures"

# Fully synthetic fixtures (fictional traveller, hotel, address and dates). No real data.
HOTEL_P1 = """Example Grand Hotel
100 Example Street Exampleville, Exampleland 00000
Mon, Mar 3, 2031 - Tue, Mar 4, 2031
Check-In: Monday, March 3, 2031 02:00 PM
Check-Out: Tuesday, March 4, 2031 11:00 AM
Number of rooms 1 Room
Guests per room 2 Adults
Guarantee Method Credit Card Guarantee, Mastercard"""
HOTEL_P2 = """Total for Stay (all rooms)
Room 1
Room Type
1 Bedroom Suite, Bedroom 1: 1 King, Sofa bed"""

CHECKLIST = """Things for travel
* Mobile Internet while travelling 
* Phone data plan restrictions 
* Ability to use cards
* Travel adapter
* Examplo shampoo, razor, after shave
* Power bank
* Light noise cancelling headphones
* Type c to aux
* Passport renewal check, travel insurance, currency exchange, return ticket
Before travel
* Document binder
Things to do before travel
* Charge laptop, power bank, earphones, phone
* Pack shirts, shorts, jeans, pajamas, socks, 1 hoodie
* Pack shampoo, conditioner, shaving kit
* Pack 2 pair shoes
* Pack 2 phone chargers, laptop charger, power bank, earphones, headphones
* Pack a notebook and pen
* Pack a rain jacket and umbrella
* Keep a copy of passport, travel insurance and return ticket
* Organize document binder
* Pack blue bag with tablet, document binder
* Buy travel pillow, skin repair cream
* Buy bag locks
* Buy headphones
* Remove old receipts from wallet
* Complete 2 Jira tickets and deploy
"""


def _ascii(s: str) -> str:
    s = s.replace("–", "-").replace("—", "-").replace("≤", "<=").replace("≥", ">=")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, pages: list[str]) -> None:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>"]
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(len(pages)))}] "
                f"/Count {len(pages)} >>")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        lines: list[str] = []
        for para in _ascii(text).split("\n"):
            lines += textwrap.wrap(para, 95) or [""]
        body = "BT /F1 8 Tf 9 TL 30 760 Td " + " T* ".join(f"({_esc(ln)}) Tj" for ln in lines) + " ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>")
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream")
    out = b"%PDF-1.4\n"
    offs = []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    path.write_bytes(out)


def spec_pages(md: str) -> list[str]:
    md = "\n".join(ln for ln in md.splitlines() if "Personal documents" not in ln)  # not for fixtures
    parts = re.split(r"(?m)^(?=## )", md)
    pages = [parts[0] + parts[1]] + parts[2:]
    return [p.strip() for p in pages if p.strip()]


def tasks_csv(checklist: str) -> str:
    """One row per item under 'Things to do before travel' (action = first word of the line)."""
    rows = ["task_no,action,item"]
    n = 0
    on = False
    for ln in checklist.splitlines():
        if ln.startswith("Things to do before travel"):
            on = True
            continue
        if on and ln.startswith("* "):
            n += 1
            action, _, item = ln[2:].strip().partition(" ")
            rows.append(f'{n},{action},"{item.replace(chr(34), "")}"')
    return "\n".join(rows) + "\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "travel_checklist.txt").write_text(CHECKLIST)
    (OUT / "travel_tasks.csv").write_text(tasks_csv(CHECKLIST))
    write_pdf(OUT / "hotel_reservation.pdf", [HOTEL_P1, HOTEL_P2])
    write_pdf(OUT / "docchat_spec.pdf", spec_pages((ROOT / "docs/spec.md").read_text()))


if __name__ == "__main__":
    main()
