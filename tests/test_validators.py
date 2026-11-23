from app import validators as v


def test_gstin_known_good():
    # Widely published sample GSTIN with a correct check digit.
    assert v.gstin_checksum_ok("27AAPFU0939F1ZV")


def test_gstin_bad_check_digit():
    assert v.gstin_format_ok("27AAPFU0939F1ZA")
    assert not v.gstin_checksum_ok("27AAPFU0939F1ZA")


def test_make_gstin_roundtrip():
    g = v.make_gstin("29", "AAKCS4821M")
    assert v.gstin_checksum_ok(g)
    assert v.gstin_state(g) == "Karnataka"
    assert v.gstin_pan(g) == "AAKCS4821M"


def test_pan():
    assert v.pan_valid("AAKCS4821M")
    assert not v.pan_valid("AAKCS4821")
    assert v.pan_holder_type("AAKCS4821M") == "company"
    assert v.pan_holder_type("ABCPK1234L") == "individual"


def test_ifsc():
    assert v.ifsc_valid("HDFC0001234")
    assert not v.ifsc_valid("HDFC1001234")  # 5th character must be zero
    assert v.ifsc_bank("HDFC0001234") == "HDFC Bank"


def test_pincode_state():
    assert v.pincode_state("560001") == "Karnataka"
    assert v.pincode_state("400001") == "Maharashtra"
    assert v.pincode_state("12") is None


def test_us_ids():
    assert v.ein_valid("47-2918365")
    assert not v.ein_valid("472918365")
    assert not v.ein_valid("00-1234567")
    assert v.aba_routing_valid("021000021")
    assert not v.aba_routing_valid("021000022")


def test_tax_id_country():
    assert v.looks_like_tax_id("27AAPFU0939F1ZV") == "IN"
    assert v.looks_like_tax_id("47-2918365") == "US"


def test_names_formatting_is_a_match():
    r = v.compare_names("Malabar Spice Exports Private Limited", "MALABAR SPICE EXPORTS PVT. LTD.")
    assert r["verdict"] == "formatting"


def test_names_different():
    assert v.compare_names("Orbit Facility Services Private Limited", "Ravi Kumar")["verdict"] == "different"


def test_names_close_is_not_a_match():
    r = v.compare_names("Sharma Logistics Private Limited", "Sharma Logistic Private Limited")
    assert r["verdict"] == "close"


def test_names_legal_form_differs():
    r = v.compare_names("Deccan Print Works LLP", "Deccan Print Works Private Limited")
    assert r["verdict"] != "formatting"


def test_individual_detection():
    assert v.looks_like_individual("Ravi Kumar")
    assert not v.looks_like_individual("Kumar Traders")
    assert not v.looks_like_individual("Orbit Facility Services Pvt Ltd")


def test_email():
    assert v.email_valid("accounts@sharmalogistics.in")
    assert not v.email_valid("accounts@sharma")
    assert v.email_is_free("someone@gmail.com")
