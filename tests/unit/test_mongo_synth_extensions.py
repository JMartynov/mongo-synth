import pytest
import re
from datetime import datetime, timezone
from mongo_synth.generators.json_schema_generator import JsonSchemaGenerator

# ==========================================
# Scenario 1: Piecewise Linear Range Generation
# ==========================================

def test_1_2_skewed_boundary_value_retention():
    """
    Test 1.2: Skewed Boundary Value Retention (lowerPercentile = 0.9)
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "score": {"type": "number"}
            },
            "required": ["score"]
        },
        "percentileStats": {
            "fieldName": "score",
            "boundaryValue": 50.0,
            "lowerPercentile": 0.9
        },
        "metadata": {
            "expected_document_count": 100
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=100, seed=42)
    batch = generator.generate_batch()
    
    less_than_50 = [doc for doc in batch if doc["score"] < 50.0]
    assert len(less_than_50) == 90

def test_1_5_float_integer_coercion():
    """
    Test 1.5: Float/Integer Coercion
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "quantity": {"type": "integer"}
            },
            "required": ["quantity"]
        },
        "percentileStats": {
            "fieldName": "quantity",
            "boundaryValue": 50,
            "lowerPercentile": 0.3
        },
        "metadata": {
            "expected_document_count": 50
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=50, seed=42)
    batch = generator.generate_batch()
    
    for doc in batch:
        assert isinstance(doc["quantity"], int)

def test_1_6_boundary_identity_safety():
    """
    Test 1.6: Boundary Identity Safety (lowerPercentile = 1.0)
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "val": {"type": "number"}
            },
            "required": ["val"]
        },
        "percentileStats": {
            "fieldName": "val",
            "boundaryValue": 200.0,
            "lowerPercentile": 1.0
        },
        "metadata": {
            "expected_document_count": 50
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=50, seed=42)
    batch = generator.generate_batch()
    
    for doc in batch:
        assert doc["val"] <= 200.0

def test_1_7_null_value_preservation():
    """
    Test 1.7: Null Value Preservation
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "val": {
                    "anyOf": [
                        {"type": "number"},
                        {"type": "null"}
                    ]
                }
            },
            "required": ["val"]
        },
        "percentileStats": {
            "fieldName": "val",
            "boundaryValue": 100.0,
            "lowerPercentile": 0.5
        },
        "metadata": {
            "expected_document_count": 200
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=200, seed=42)
    batch = generator.generate_batch()
    
    null_indices = [idx for idx, doc in enumerate(batch) if doc["val"] is None]
    assert len(null_indices) > 0  # Hypothesis should generate some nulls for anyOf with null
    for idx in null_indices:
        assert batch[idx]["val"] is None

def test_1_8_scaling_stream_chunk_consistency():
    """
    Test 1.8: Scaling Stream Chunk Consistency
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "val": {"type": "number"}
            },
            "required": ["val"]
        },
        "percentileStats": {
            "fieldName": "val",
            "boundaryValue": 100.0,
            "lowerPercentile": 0.3
        },
        "metadata": {
            "expected_document_count": 5000
        }
    }
    
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=5000, seed=42)
    for _ in range(3):
        batch = generator.generate_batch()
        less_than_100 = [doc for doc in batch if doc["val"] < 100.0]
        assert len(less_than_100) == 1500

def test_1_9_deterministic_seeding_parity():
    """
    Test 1.9: Deterministic Seeding Parity
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "val": {"type": "number"}
            },
            "required": ["val"]
        },
        "percentileStats": {
            "fieldName": "val",
            "boundaryValue": 100.0,
            "lowerPercentile": 0.3
        },
        "metadata": {
            "expected_document_count": 100
        }
    }
    gen1 = JsonSchemaGenerator(blueprint, documents_per_collection=100, seed=42)
    batch1 = gen1.generate_batch()
    
    gen2 = JsonSchemaGenerator(blueprint, documents_per_collection=100, seed=42)
    batch2 = gen2.generate_batch()
    
    assert [d["val"] for d in batch1] == [d["val"] for d in batch2]

def test_1_10_default_schema_fallback():
    """
    Test 1.10: Default Schema Fallback
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "val": {"type": "number"}
            },
            "required": ["val"]
        },
        "metadata": {
            "expected_document_count": 10
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=10, seed=42)
    batch = generator.generate_batch()
    assert len(batch) == 10
    for doc in batch:
        assert isinstance(doc["val"], float)


# ==========================================
# Scenario 2: Hashed Enum Seeding
# ==========================================

def test_2_1_and_2_7_hex_string_constraint():
    """
    Test 2.1: 16-Character String Constraint
    Test 2.7: Character Set Validation [0-9a-f]
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "token": {
                    "type": "string",
                    "enumValues": ["b8f9a2c3d4e5f678", "e5f678a2b8f9a2c3"]
                }
            },
            "required": ["token"]
        },
        "metadata": {
            "expected_document_count": 10
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=10, seed=42)
    batch = generator.generate_batch()
    
    hex_pattern = re.compile(r"^[0-9a-f]{16}$")
    for doc in batch:
        token = doc["token"]
        assert len(token) == 16
        assert hex_pattern.match(token)

def test_2_6_empty_token_fallback():
    """
    Test 2.6: Empty Token Fallback
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "token": {
                    "type": "string",
                    "enumValues": [],
                    "minLength": 5
                }
            },
            "required": ["token"]
        },
        "metadata": {
            "expected_document_count": 10
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=10, seed=42)
    batch = generator.generate_batch()
    for doc in batch:
        assert isinstance(doc["token"], str)
        assert len(doc["token"]) >= 5

def test_2_8_case_sensitivity_integrity():
    """
    Test 2.8: Case Sensitivity Integrity
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "token": {
                    "type": "string",
                    "enumValues": ["B8F9A2C3D4E5F678"]
                }
            },
            "required": ["token"]
        },
        "metadata": {
            "expected_document_count": 5
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=5, seed=42)
    batch = generator.generate_batch()
    for doc in batch:
        assert doc["token"] == "B8F9A2C3D4E5F678"

def test_2_9_multiple_enum_fields_mapping():
    """
    Test 2.9: Multiple Enum Fields Mapping
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enumValues": ["b8f9a2c3d4e5f678"]
                },
                "role": {
                    "type": "string",
                    "enumValues": ["e5f678a2b8f9a2c3"]
                }
            },
            "required": ["status", "role"]
        },
        "metadata": {
            "expected_document_count": 5
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=5, seed=42)
    batch = generator.generate_batch()
    for doc in batch:
        assert doc["status"] == "b8f9a2c3d4e5f678"
        assert doc["role"] == "e5f678a2b8f9a2c3"

def test_2_10_zero_data_leak_verification():
    """
    Test 2.10: Zero Data Leak Verification
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "token": {
                    "type": "string",
                    "enumValues": ["b8f9a2c3d4e5f678"]
                }
            },
            "required": ["token"]
        },
        "metadata": {
            "expected_document_count": 5
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=5, seed=42)
    batch = generator.generate_batch()
    for doc in batch:
        assert doc["token"] == "b8f9a2c3d4e5f678"
