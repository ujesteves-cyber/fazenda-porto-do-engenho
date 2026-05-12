import pytest
from pathlib import Path
from embriao_pdf import normalizar_tipo, parse_fivet_pdf


def test_normalizar_tipo_sex_f_canonical():
    assert normalizar_tipo("Sex F") == "Sex F"


def test_normalizar_tipo_sex_uppercase():
    assert normalizar_tipo("SEX F") == "Sex F"


def test_normalizar_tipo_sexada():
    assert normalizar_tipo("Sexada") == "Sex F"


def test_normalizar_tipo_conv_with_period():
    assert normalizar_tipo("Conv.") == "Conv."


def test_normalizar_tipo_conv_without_period():
    assert normalizar_tipo("Conv") == "Conv."


def test_normalizar_tipo_convencional():
    assert normalizar_tipo("Convencional") == "Conv."


def test_normalizar_tipo_empty():
    assert normalizar_tipo("") == "Conv."


def test_normalizar_tipo_none():
    assert normalizar_tipo(None) == "Conv."


FIXTURE = Path(__file__).parent / "fixtures" / "fivet_exemplo.pdf"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_extracts_header_date():
    result = parse_fivet_pdf(str(FIXTURE))
    assert result["data_planilha"] == "30/04/2026"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_extracts_total():
    result = parse_fivet_pdf(str(FIXTURE))
    assert result["total_declarado"] == 136


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_extracts_all_rows():
    result = parse_fivet_pdf(str(FIXTURE))
    # Sum of Quant.VT column should equal declared total
    soma = sum(r["qtd"] for r in result["linhas"])
    assert soma == result["total_declarado"]


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_first_row_shape():
    result = parse_fivet_pdf(str(FIXTURE))
    first = result["linhas"][0]
    assert set(first.keys()) >= {"dt_opu", "dt_vitrificacao", "doadora",
                                  "touro", "tipo_semen", "qtd", "obs"}
    assert first["dt_opu"] == "04/09/25"
    assert first["doadora"] == "R3529"
    assert first["touro"] == "CIA ROBUSTO JATA"
    assert first["tipo_semen"] == "Sex F"
    assert first["qtd"] == 6


def test_parse_fivet_pdf_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        parse_fivet_pdf("/nonexistent/path.pdf")
