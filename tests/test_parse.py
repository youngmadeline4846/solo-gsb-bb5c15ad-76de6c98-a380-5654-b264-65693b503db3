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


# ------------------------------------------------------- GS1 Digital Link URIs

DL = "https://id.example.com"  # 22 chars; path starts at offset 22


def test_digital_link_path_and_query_elements_in_order():
    resp = parse(f"{DL}/01/{GTIN_OK}/10/ABC123?17=250131&21=SN0001")
    assert resp.status_code == 200
    elements = resp.json()["elements"]
    assert [e["ai"] for e in elements] == ["01", "10", "17", "21"]
    assert [e["value"] for e in elements] == [GTIN_OK, "ABC123", "250131", "SN0001"]
    assert [e["offset"] for e in elements] == [23, 41, 51, 61]
    assert [e["name"] for e in elements] == ["GTIN", "BATCH/LOT", "EXPIRY", "SERIAL"]


def test_digital_link_path_elements_come_before_query_regardless_of_ai():
    resp = parse(f"{DL}/01/{GTIN_OK}/21/SN0001?10=ABC123&17=250131")
    assert resp.status_code == 200
    assert [e["ai"] for e in resp.json()["elements"]] == ["01", "21", "10", "17"]


def test_digital_link_http_scheme():
    resp = parse(f"http://example.com/01/{GTIN_OK}")
    assert resp.status_code == 200
    element = resp.json()["elements"][0]
    assert element["ai"] == "01"
    assert element["offset"] == 19  # after "http://example.com/"


def test_digital_link_query_only():
    resp = parse(f"{DL}?01={GTIN_OK}&17=250131")
    assert resp.status_code == 200
    elements = resp.json()["elements"]
    assert [e["ai"] for e in elements] == ["01", "17"]
    assert [e["offset"] for e in elements] == [23, 41]


def test_digital_link_non_ai_query_params_ignored():
    resp = parse(f"{DL}/01/{GTIN_OK}?linkType=gs1:default&17=250131")
    assert resp.status_code == 200
    elements = resp.json()["elements"]
    assert [e["ai"] for e in elements] == ["01", "17"]
    assert [e["offset"] for e in elements] == [23, 62]


def test_digital_link_percent_decoding():
    resp = parse(f"{DL}/10/ABC%44%45%46/21/SN%2D001")
    assert resp.status_code == 200
    assert [e["value"] for e in resp.json()["elements"]] == ["ABCDEF", "SN-001"]


def test_digital_link_plus_sign_kept_literal():
    resp = parse(f"{DL}/21/SN+001")
    assert resp.status_code == 200
    assert resp.json()["elements"][0]["value"] == "SN+001"


def test_digital_link_fragment_ignored():
    resp = parse(f"{DL}/01/{GTIN_OK}#section")
    assert resp.status_code == 200
    assert [e["ai"] for e in resp.json()["elements"]] == ["01"]


def test_element_string_containing_url_like_value_still_parses():
    resp = parse("10http://example.com")
    assert resp.status_code == 200
    assert resp.json()["elements"][0]["value"] == "http://example.com"


# -------------------------------------------------- digital link URI errors

def test_digital_link_unpaired_path_segment():
    resp = parse(f"{DL}/01/{GTIN_OK}/21")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_URI"
    assert detail["offset"] == 41  # the unpaired '21' segment
    assert detail["found"] == "21"


def test_digital_link_unknown_ai_in_path():
    resp = parse(f"{DL}/99/ABC")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "UNSUPPORTED_AI"
    assert detail["offset"] == 23
    assert detail["found"] == "99"


def test_digital_link_unknown_ai_in_query():
    resp = parse(f"{DL}/01/{GTIN_OK}?99=ABC")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "UNSUPPORTED_AI"
    assert detail["offset"] == 41
    assert detail["found"] == "99"


def test_digital_link_non_numeric_ai_in_path():
    resp = parse(f"{DL}/AB/CD")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_AI"
    assert detail["offset"] == 23
    assert detail["found"] == "AB"


def test_digital_link_duplicate_ai_in_path():
    resp = parse(f"{DL}/01/{GTIN_OK}/01/{GTIN_OK}")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "DUPLICATE_AI"
    assert detail["offset"] == 41  # the second '01'
    assert detail["found"] == "01"


def test_digital_link_duplicate_ai_in_query():
    resp = parse(f"{DL}?10=A&10=B")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "DUPLICATE_AI"
    assert detail["offset"] == 28  # the second '10'
    assert detail["found"] == "10"


def test_digital_link_ai_in_both_path_and_query():
    resp = parse(f"{DL}/01/{GTIN_OK}?01={GTIN_OK}")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "DUPLICATE_AI"
    assert detail["offset"] == 41  # the query '01'
    assert "both" in detail["message"]


def test_digital_link_check_digit_validation_reused():
    resp = parse(f"{DL}/01/09501101530004")  # last digit should be 3
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_CHECK_DIGIT"
    assert detail["offset"] == 39  # byte offset of the check digit in the URI
    assert detail["found"] == "4"


def test_digital_link_date_validation_reused():
    resp = parse(f"{DL}/17/251331")  # month 13
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_DATE"
    assert detail["offset"] == 28  # byte offset of the month in the URI


def test_digital_link_fixed_field_too_short():
    resp = parse(f"{DL}/01/0950")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "FIELD_TOO_SHORT"
    assert detail["offset"] == 30  # end of the value in the URI


def test_digital_link_variable_field_too_long():
    resp = parse(f"{DL}/10/" + "A" * 21)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "FIELD_TOO_LONG"
    assert detail["offset"] == 46  # the 21st character


def test_digital_link_empty_query_value():
    resp = parse(f"{DL}/01/{GTIN_OK}?10=")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "FIELD_EMPTY"
    assert detail["offset"] == 44


def test_digital_link_invalid_character_offset_maps_to_original_uri():
    resp = parse(f"{DL}/10/AB%23CD")  # %23 decodes to '#'
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_CHARACTER"
    assert detail["found"] == "#"
    assert detail["offset"] == 28  # byte offset of '%' in the original URI


def test_digital_link_utf8_percent_decoding_before_charset_check():
    resp = parse(f"{DL}/21/SN%C3%A9")  # %C3%A9 decodes to 'é'
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_CHARACTER"
    assert detail["found"] == "é"
    assert detail["offset"] == 28  # byte offset of the '%C3' sequence


def test_digital_link_invalid_percent_encoding():
    resp = parse(f"{DL}/10/AB%2")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_PERCENT_ENCODING"
    assert detail["offset"] == 28
    assert detail["found"] == "%2"


def test_digital_link_invalid_percent_encoding_hex():
    resp = parse(f"{DL}/10/AB%ZZ")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_PERCENT_ENCODING"
    assert detail["offset"] == 28
    assert detail["found"] == "%ZZ"


def test_digital_link_invalid_utf8_percent_encoding():
    resp = parse(f"{DL}/10/%FF")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "INVALID_PERCENT_ENCODING"
    assert detail["offset"] == 26


def test_digital_link_without_elements():
    resp = parse(DL)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "EMPTY_INPUT"
    assert detail["offset"] == 0


def test_digital_link_bare_path_without_elements():
    resp = parse(f"{DL}/")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "EMPTY_INPUT"
