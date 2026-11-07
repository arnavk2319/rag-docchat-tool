"""Regenerate tiny binary fixtures: uv run python tests/make_fixtures.py"""

from pathlib import Path

OUT = Path(__file__).parent / "fixtures"


def pdf(path: Path, pages: list[str]) -> None:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, t in enumerate(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
        )
        s = f"BT /F1 12 Tf 20 100 Td ({t}) Tj ET"
        objs.append(f"<< /Length {len(s)} >>\nstream\n{s}\nendstream")
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


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pdf(OUT / "sample.pdf", ["Alpha page one", "Beta page two"])
    (OUT / "sample.txt").write_text("Plain text fixture about llamas.\n" * 3)
    (OUT / "sample.csv").write_text("name,qty\napple,3\npear,5\n")
    import docx
    import openpyxl

    d = docx.Document()
    d.add_paragraph("Docx fixture paragraph about zebras.")
    d.save(OUT / "sample.docx")
    wb = openpyxl.Workbook()
    wb.active.title = "Sales"
    wb.active.append(["item", "amount"])
    wb.active.append(["widget", 7])
    wb.create_sheet("Costs").append(["rent", 100])
    wb.save(OUT / "sample.xlsx")


if __name__ == "__main__":
    main()
