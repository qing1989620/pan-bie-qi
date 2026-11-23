import base64
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from skill_discriminator.api import create_app
from skill_discriminator.pdf_table import safe_csv_cell

EXAMPLES = Path(__file__).resolve().parents[1] / "docs"
REFERENCE = (EXAMPLES / "examples/paper-table-6.csv").read_text(encoding="utf-8-sig")
ENDPOINT = "/api/discriminator/task/pdf-table"


def request_body(pdf: bytes, page: int = 1, **fields) -> dict:
    return {"pdf_base64": base64.b64encode(pdf).decode(), "page": page, **fields}


@pytest.mark.parametrize(("reference", "expected_state", "expected_cells"), [
    (REFERENCE, "verified_match", 16),
    (REFERENCE.replace(",319,", ",320,"), "disagreement", 15),
    ("", "no_reference", None),
])
def test_pdf_endpoint_extracts_a_real_table_and_compares_the_reference(reference, expected_state, expected_cells):
    paper = (EXAMPLES / "skill-discriminator-paper.pdf").read_bytes()
    with TestClient(create_app()) as client:
        response = client.post(ENDPOINT, json=request_body(
            paper, 4, reference_csv=reference, expected_rows=4,
            headers="方法|前排改善|前排回退|复查回退|增益 MAE",
        ))
    assert response.status_code == 200
    result = response.json()
    assert result["state"] == "extracted" and result["method"] == "layout"
    assert result["row_count"] == 4 and result["column_count"] == 5
    assert result["verification"]["state"] == expected_state
    assert result["verification"].get("matched_cells") == expected_cells
    assert not result["skill_effect_measured"] and not result["skill_code_executed"]
    observed_rows = list(csv.reader(io.StringIO(result["csv"])))
    expected_rows = list(csv.reader(io.StringIO(REFERENCE)))
    assert observed_rows[1:] == expected_rows[1:]
    if expected_state == "disagreement":
        assert result["verification"]["differences"] == [
            {"row": "SkillAudit 发布增益", "column": "前排改善", "expected": "320", "actual": "319"},
        ]


def test_pdf_endpoint_requires_ocr_for_an_image_page():
    file = io.BytesIO()
    with Image.new("RGB", (48, 48), "white") as image:
        image.save(file, format="PDF")
    with TestClient(create_app()) as client:
        response = client.post(ENDPOINT, json=request_body(file.getvalue(), reference_csv=REFERENCE))
    assert response.status_code == 200
    result = response.json()
    assert result["state"] == "needs_ocr"
    assert "OCR" in result["reason"] and "csv" not in result


@pytest.mark.parametrize("invalid", ["not valid base64", base64.b64encode(b"%PDF-not-valid").decode()])
def test_pdf_endpoint_returns_an_input_error_for_unreadable_files(invalid):
    with TestClient(create_app()) as client:
        assert client.post(ENDPOINT, json={"pdf_base64": invalid, "page": 1}).status_code == 422


def test_pdf_download_protects_spreadsheet_cells():
    assert safe_csv_cell("+1") == "+1"
    assert safe_csv_cell("-23.5") == "-23.5"
    assert safe_csv_cell("  =1+1") == "'  =1+1"
