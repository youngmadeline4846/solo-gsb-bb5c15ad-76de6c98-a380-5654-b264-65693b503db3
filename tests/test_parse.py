"""End-to-end tests for the /parse endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

GS = "\x1d"
GTIN_OK = "09501101530003"  # valid Mod-10 check digit


def parse(data):
    return client.post("/parse", json={"data": data})


# ---------------------------------------------------------------- happy path

def test_full_string_all_ais_in_order():
    resp = parse(f"01{GTIN_OK}1725013110ABC123{GS}21SN0001")
    assert resp.status_code == 200
    elements = resp.json()["elements"]
    assert [e["ai"] for e in elements] == ["01", "17", "10", "21"]
    assert [e["value"] for e in elements] == [GTIN_OK, "250131", "ABC123", "SN0001"]
    assert [e["offset"] for e in elements] == [0, 16, 24, 33]
    assert [e["name"] for e in elements] == ["GTIN", "EXPIRY", "BATCH/LOT", "SERIAL"]


def test_fixed_length_fields_need_no_separator():
    resp = parse(f"01{GTIN_OK}17250131")
    assert resp.status_code == 200
    assert [e["ai"] for e in resp.json()["elements"]] == ["01", "17"]


def test_variable_field_at_end_needs_no_separator():
    resp = parse("1725013110ABC123")
    assert resp.status_code == 200
    elements = resp.json()["elements"]
    assert elements[-1]["ai"] == "10"
    assert elements[-1]["value"] == "ABC123"


def test_variable_field_max_length_20():
    resp = parse("10" + "A" * 20)
    assert resp.status_code == 200
    assert resp.json()["elements"][0]["value"] == "A" * 20


def test_serial_full_charset():
    resp = parse("21SN-001/A:OK_+x")
    assert resp.status_code == 200
    assert resp.json()["elements"][0]["value"] == "SN-001/A:OK_+x"


def test_expiry_day_00_means_last_day_of_month():
    assert parse("17250200").status_code == 200


def test_leap_day_valid():
    assert parse("17240229").status_code == 200


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------------------------------------------------------------- errors

def test_empty_input():
    resp = parse("")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "EMPTY_INPUT"
    assert detail["offset"] == 0


def test_unknown_ai():
    resp = parse("99ABC")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "UNSUPPORTED_AI"
    assert detail["offset"] == 0
    assert detail["found"] == "99"
    assert "01" in detail["expected"]


def test_unexpected_separator_at_start():
    resp = parse(f"{GS}01{GTIN_OK}")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "UNEXPECTED_SEPARATOR"
    assert detail["offset"] == 0


def test_truncated_fixed_field():
    resp = parse("0109501101")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "FIELD_TOO_SHORT"
    assert "14" in detail["expected"]


def test_separator_inside_fixed_field():
    resp = parse(f"0109501{GS}17250131")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "FIELD_TOO_SHORT"


def test_gtin_bad_check_digit():
    resp = parse("0109501101530004")  # last digit should be 3
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_CHECK_DIGIT"
    assert detail["offset"] == 15  # byte offset of the check digit
    assert detail["found"] == "4"
    assert "3" in detail["expected"]


def test_gtin_non_digit():
    resp = parse("0109501101530A03")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_CHARACTER"
    assert detail["offset"] == 13
    assert detail["found"] == "A"


def test_invalid_month():
    resp = parse("17251331")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_DATE"
    assert detail["offset"] == 4


def test_invalid_day():
    resp = parse("17250230")  # Feb 30 does not exist
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_DATE"
    assert detail["offset"] == 6


def test_invalid_leap_day():
    assert parse("17250229").status_code == 422  # 2025 is not a leap year


def test_batch_too_long():
    resp = parse("10" + "A" * 21)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "FIELD_TOO_LONG"
    assert detail["offset"] == 22  # offset of the 21st character


def test_batch_empty():
    resp = parse(f"10{GS}21ABC")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "FIELD_EMPTY"


def test_batch_invalid_character():
    resp = parse("10ABC#DEF")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_CHARACTER"
    assert detail["offset"] == 5
    assert detail["found"] == "#"


def test_missing_data_field():
    resp = client.post("/parse", json={})
    assert resp.status_code == 422
