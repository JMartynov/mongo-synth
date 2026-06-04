import pytest
from pydantic import BaseModel, Field
from typing import Optional, List
from mongo_synth.generators.pydantic_generator import PydanticGenerator

# Standard Pydantic model to use in testing
class SimpleModel(BaseModel):
    name: str = Field(description="The user's name")
    age: int = Field(ge=0, le=120)
    tags: Optional[List[str]] = None

class NonPydanticClass:
    pass

def test_pydantic_generator_with_direct_model():
    """Verify initializing PydanticGenerator with a direct BaseModel class."""
    blueprint = {
        "model": SimpleModel,
        "metadata": {
            "expected_document_count": 5
        }
    }
    generator = PydanticGenerator(blueprint, documents_per_collection=5, seed=42)
    
    assert generator.schema is not None
    assert "name" in generator.schema["properties"]
    assert "age" in generator.schema["properties"]
    
    batch = generator.generate_batch()
    assert len(batch) == 5
    for doc in batch:
        assert isinstance(doc["name"], str)
        assert isinstance(doc["age"], int)
        assert doc["age"] >= 0 and doc["age"] <= 120

def test_pydantic_generator_with_string_path():
    """Verify loading Pydantic model dynamically using dot/colon path."""
    blueprint = {
        "model_path": "tests.unit.test_pydantic_generator:SimpleModel",
        "metadata": {
            "expected_document_count": 3
        }
    }
    generator = PydanticGenerator(blueprint, documents_per_collection=3, seed=42)
    
    assert generator.schema is not None
    assert "name" in generator.schema["properties"]
    
    batch = generator.generate_batch()
    assert len(batch) == 3

def test_pydantic_generator_invalid_model_path():
    """Verify that using an invalid module path raises ImportError."""
    blueprint = {
        "model_path": "tests.unit.non_existent_module:SimpleModel"
    }
    with pytest.raises(ImportError):
        PydanticGenerator(blueprint, documents_per_collection=1)

def test_pydantic_generator_invalid_class_name():
    """Verify that using a non-existent class raises AttributeError."""
    blueprint = {
        "model_path": "tests.unit.test_pydantic_generator:NonExistentModel"
    }
    with pytest.raises(AttributeError):
        PydanticGenerator(blueprint, documents_per_collection=1)

def test_pydantic_generator_invalid_path_format():
    """Verify that an invalid path format raises ValueError."""
    blueprint = {
        "model_path": "invalidpath"
    }
    with pytest.raises(ValueError, match="Invalid model path"):
        PydanticGenerator(blueprint, documents_per_collection=1)

def test_pydantic_generator_non_pydantic_class():
    """Verify that passing a non-BaseModel class raises TypeError."""
    blueprint = {
        "model": NonPydanticClass
    }
    with pytest.raises(TypeError, match="does not appear to be a Pydantic model"):
        PydanticGenerator(blueprint, documents_per_collection=1)

def test_pydantic_generator_missing_model_info():
    """Verify that missing model/model_path raises ValueError."""
    blueprint = {}
    with pytest.raises(ValueError, match="requires 'model' or 'model_path'"):
        PydanticGenerator(blueprint, documents_per_collection=1)

class DummyLegacyModel:
    """Simulates a Pydantic v1 model schema definition method."""
    @classmethod
    def schema(cls):
        return {
            "type": "object",
            "properties": {
                "legacy_field": {"type": "string"}
            }
        }

def test_pydantic_generator_legacy_v1_support():
    """Verify that legacy Pydantic v1 schema() method is supported."""
    blueprint = {
        "model": DummyLegacyModel
    }
    generator = PydanticGenerator(blueprint, documents_per_collection=1)
    assert generator.schema == {
        "type": "object",
        "properties": {
            "legacy_field": {"type": "string"}
        }
    }


def test_pydantic_generator_with_existing_schema():
    """Verify that PydanticGenerator accepts pre-serialized schema directly."""
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "direct_field": {"type": "string"}
            },
            "required": ["direct_field"]
        },
        "metadata": {
            "expected_document_count": 2
        }
    }
    generator = PydanticGenerator(blueprint, documents_per_collection=2)
    assert generator.schema == blueprint["schema"]
    batch = generator.generate_batch()
    assert len(batch) == 2
    assert "direct_field" in batch[0]


def test_pydantic_sensitive_type():
    """Verify Pydantic models containing json_schema_extra sensitive annotations generate properly."""
    class SensitiveModel(BaseModel):
        email: str = Field(description="User email", json_schema_extra={"sensitiveType": "email"})
        ssn: str = Field(description="SSN", json_schema_extra={"sensitiveType": "ssn"})
        regular_field: str = Field(description="Normal string")

    blueprint = {
        "model": SensitiveModel,
        "metadata": {
            "expected_document_count": 3
        }
    }
    
    generator = PydanticGenerator(blueprint, documents_per_collection=3, seed=42)
    assert generator.schema is not None
    assert "email" in generator.schema["properties"]
    assert generator.schema["properties"]["email"].get("sensitiveType") == "email"
    assert generator.schema["properties"]["ssn"].get("sensitiveType") == "ssn"

    batch = generator.generate_batch()
    assert len(batch) == 3
    for doc in batch:
        assert isinstance(doc["email"], str)
        assert "@" in doc["email"]
        assert doc["ssn"] is not None
        assert isinstance(doc["ssn"], str)


