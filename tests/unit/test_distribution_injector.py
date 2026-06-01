import pytest
from mongo_synth.generators.distribution_injector import DistributionInjector

def test_distribution_injector_happy_path():
    schema = {
        "properties": {
            "status": {"type": "string"},
            "age": {"type": "integer"}
        }
    }
    profile = {
        "status": {"A": 0.8, "B": 0.2}
    }
    injector = DistributionInjector(schema, profile)

    # Generate 1000 uniform documents
    docs = [{"status": "unknown", "age": 25} for _ in range(1000)]

    # Inject distribution
    injected_docs = injector.inject_batch(docs)

    # Verify counts
    count_A = sum(1 for doc in injected_docs if doc["status"] == "A")
    count_B = sum(1 for doc in injected_docs if doc["status"] == "B")

    # Verify total
    assert count_A + count_B == 1000
    # Allow some tolerance for randomness
    assert 750 <= count_A <= 850
    assert 150 <= count_B <= 250

def test_distribution_injector_missing_profile():
    schema = {"properties": {"status": {"type": "string"}}}
    injector = DistributionInjector(schema, {})

    doc = {"status": "default"}
    injected_doc = injector.inject_batch([doc])[0]

    assert injected_doc["status"] == "default"

def test_distribution_injector_unique_constraint_override():
    schema = {
        "properties": {
            "user_id": {
                "type": "string",
                "format": "uuid"
            }
        }
    }
    profile = {
        "user_id": {"uuid-1": 0.5, "uuid-2": 0.5}
    }
    injector = DistributionInjector(schema, profile)

    doc = {"user_id": "generated-unique-uuid-1234"}
    injected_doc = injector.inject_batch([doc])[0]

    # Since it's marked as UUID, it should NOT override with the profile
    assert injected_doc["user_id"] == "generated-unique-uuid-1234"

def test_distribution_injector_missing_field_in_doc():
    schema = {"properties": {"status": {"type": "string"}}}
    profile = {"status": {"A": 1.0}}
    injector = DistributionInjector(schema, profile)

    doc = {"other_field": "test"}
    injected_doc = injector.inject_batch([doc])[0]

    # Should not add the field if it wasn't generated
    assert "status" not in injected_doc
