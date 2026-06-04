import pytest
from mongo_synth.generators.sensitive import SensitiveDataTracker

def test_tracker_init():
    tracker = SensitiveDataTracker()
    assert tracker.run_id is None
    assert len(tracker.verifiers) == 0

    tracker_with_run_id = SensitiveDataTracker(run_id="test_run")
    assert tracker_with_run_id.run_id == "test_run"

def test_tracker_clear():
    tracker = SensitiveDataTracker()
    tracker.track("email", "test@example.com")
    assert len(tracker.verifiers) == 1
    tracker.clear()
    assert len(tracker.verifiers) == 0

def test_tracker_generate_value_no_run_id():
    tracker = SensitiveDataTracker()
    
    # Test 'name'
    name = tracker.generate_value("name")
    assert isinstance(name, str)
    assert len(name) > 0
    
    # Test 'email'
    email = tracker.generate_value("email")
    assert "@" in email
    
    # Test 'phone'
    phone = tracker.generate_value("phone")
    assert isinstance(phone, str)
    assert len(phone) > 0
    
    # Test 'ssn'
    ssn = tracker.generate_value("ssn")
    assert isinstance(ssn, str)
    assert len(ssn) > 0
    
    # Test 'credit_card'
    cc = tracker.generate_value("credit_card")
    assert isinstance(cc, str)
    assert len(cc) > 0
    
    # Test 'address'
    address = tracker.generate_value("address")
    assert isinstance(address, str)
    assert "\n" not in address
    
    # Test 'password'
    password = tracker.generate_value("password")
    assert isinstance(password, str)
    assert len(password) == 16
    
    # Test 'api_key'
    api_key = tracker.generate_value("api_key")
    assert api_key.startswith("key_live_")
    
    # Test fallback
    fallback = tracker.generate_value("unknown_type")
    assert isinstance(fallback, str)
    assert len(fallback) > 0

    # Assert that all 9 generated values are tracked
    assert len(tracker.verifiers) == 9
    for entry in tracker.verifiers:
        assert "type" in entry
        assert "value" in entry

def test_tracker_generate_value_with_run_id():
    tracker = SensitiveDataTracker(run_id="run42")
    
    # Test 'name' has prefix
    name = tracker.generate_value("name")
    assert name.startswith("run42_")
    
    # Test 'email' has local-part prefix
    email = tracker.generate_value("email")
    assert email.startswith("run42_")
    
    # Test 'password' has prefix
    password = tracker.generate_value("password")
    assert password.startswith("run42_")
    # Prefix "run42_" is 6 chars, password itself is 16 chars = 22 chars
    assert len(password) == 22
    
    # Test 'api_key' has prefix in correct place
    api_key = tracker.generate_value("api_key")
    assert api_key.startswith("key_live_run42_")
    
    # Test fallback has prefix
    fallback = tracker.generate_value("something")
    assert fallback.startswith("run42_")

def test_tracker_auto_inject():
    tracker = SensitiveDataTracker()
    
    # Test non-dict is ignored
    assert tracker.auto_inject(None) is None
    assert tracker.auto_inject([]) == []
    
    # Test auto inject adds standard structure
    doc = {"existing": "value"}
    result = tracker.auto_inject(doc)
    assert result is doc
    assert "existing" in doc
    assert "personal_info" in doc
    assert "billing" in doc
    assert "credentials" in doc
    
    # Inspect injected fields
    pi = doc["personal_info"]
    assert "full_name" in pi
    assert "email" in pi
    assert "phone" in pi
    assert "ssn" in pi
    assert "address" in pi
    
    assert "credit_card" in doc["billing"]
    
    cred = doc["credentials"]
    assert "password" in cred
    assert "api_key" in cred

    # Check verifiers have been updated for all 8 injected fields
    assert len(tracker.verifiers) == 8


def test_tracker_determinism():
    tracker1 = SensitiveDataTracker(seed=12345)
    tracker2 = SensitiveDataTracker(seed=12345)

    name1 = tracker1.generate_value("name")
    name2 = tracker2.generate_value("name")
    assert name1 == name2

    email1 = tracker1.generate_value("email")
    email2 = tracker2.generate_value("email")
    assert email1 == email2


def test_tracker_locale():
    # Verify that a localized tracker is instantiated and operates
    tracker_de = SensitiveDataTracker(locale="de_DE", seed=99)
    tracker_en = SensitiveDataTracker(locale="en_US", seed=99)

    # Generated PII fields (like addresses or names) should be different for different locales
    addr_de = tracker_de.generate_value("address")
    addr_en = tracker_en.generate_value("address")

    # They should not be identical due to locale-specific providers
    assert addr_de != addr_en

