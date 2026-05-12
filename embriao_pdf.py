"""PDF parser for FIVET embryo stock spreadsheets.

Pure-function module isolated from Flask for easy unit testing.
"""
import re
from typing import Optional

import pdfplumber


def normalizar_tipo(raw: Optional[str]) -> str:
    """Canonicalize tipo_semen to 'Sex F' or 'Conv.'.

    Anything containing 'sex' (case-insensitive) -> 'Sex F'.
    Everything else (including empty/None) -> 'Conv.'.
    """
    s = (raw or "").strip().lower()
    if "sex" in s:
        return "Sex F"
    return "Conv."


def parse_fivet_pdf(file_path: str) -> dict:
    """Parse a FIVET stock spreadsheet PDF.

    Returns:
        {
            'data_planilha': str | None,    # e.g. "30/04/2026"
            'total_declarado': int | None,  # e.g. 136
            'linhas': list[dict],           # each row of the main table
        }
    Each `linha` has keys: dt_opu, dt_vitrificacao, doadora, touro,
    tipo_semen, qtd, obs.

    Raises:
        FileNotFoundError: if `file_path` does not exist.
        ValueError: if the main table cannot be located in the PDF.
    """
    import os
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF não encontrado: {file_path}")

    with pdfplumber.open(file_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""

        m = re.search(r"Atualizado em (\d{2}/\d{2}/\d{4})", text)
        data_planilha = m.group(1) if m else None

        m = re.search(r"ESTOQUE TOTAL.*?(\d+)", text, re.DOTALL)
        total_declarado = int(m.group(1)) if m else None

        tables = page.extract_tables()
        principal = None
        for t in tables:
            if t and len(t[0]) == 7:
                principal = t
                break
        if not principal:
            raise ValueError(
                "Tabela principal de 7 colunas não foi encontrada no PDF. "
                "Verifique se o layout do laboratório mudou."
            )

        linhas = []
        for row in principal[1:]:
            if not row[0] or "ESTOQUE TOTAL" in (row[0] or ""):
                continue
            try:
                linhas.append({
                    "dt_opu": (row[0] or "").strip(),
                    "dt_vitrificacao": (row[1] or "").strip(),
                    "doadora": (row[2] or "").strip(),
                    "touro": (row[3] or "").strip().upper(),
                    "tipo_semen": normalizar_tipo(row[4]),
                    "qtd": int(row[5]),
                    "obs": (row[6] or "").strip(),
                })
            except (ValueError, TypeError):
                # Malformed line — skip silently; user catches in preview
                continue

        return {
            "data_planilha": data_planilha,
            "total_declarado": total_declarado,
            "linhas": linhas,
        }
