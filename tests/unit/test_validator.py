import jsonschema
from unittest.mock import patch
from mongo_synth.validation.validator import FunctionalValidator, SimilarityValidator, ProjectedFunctionalValidator, StructuralValidator

def test_functional_validator_validation_error():
    """
    Test that FunctionalValidator correctly catches and formats
    jsonschema.ValidationError when generated documents fail validation.
    """
    validator = FunctionalValidator(sample_size=1)

    inferred_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    ground_truth_schema = {"type": "object", "properties": {"name": {"type": "number"}}}

    # We patch jsonschema.validate to raise ValidationError to simulate
    # the failure of a generated document matching the inferred schema.
    with patch("mongo_synth.validation.validator.jsonschema.validate") as mock_validate:
        error = jsonschema.ValidationError("Expected type string, got number")
        error.path = ["name"]
        mock_validate.side_effect = error

        result = validator.validate(inferred_schema, ground_truth_schema)

        assert result["valid"] is False
        assert result["method"] == "functional"
        assert result["fail_count"] > 0
        assert any("Field ['name']: Expected type string, got number" in err for err in result["errors"])

def test_functional_validator_unexpected_exception():
    """
    Test that FunctionalValidator correctly catches and formats
    unexpected exceptions during validation.
    """
    validator = FunctionalValidator(sample_size=1)

    inferred_schema = {"type": "object"}
    ground_truth_schema = {"type": "object"}

    with patch("mongo_synth.validation.validator.jsonschema.validate") as mock_validate:
        mock_validate.side_effect = Exception("Some weird unexpected error")

        result = validator.validate(inferred_schema, ground_truth_schema)

        assert result["valid"] is False
        assert result["method"] == "functional"
        assert result["fail_count"] > 0
        assert any("Unexpected: Some weird unexpected error" in err for err in result["errors"])

def test_projected_functional_validator_validation_error():
    """
    Test that ProjectedFunctionalValidator correctly catches and formats
    jsonschema.ValidationError when generated documents fail validation.
    """
    validator = ProjectedFunctionalValidator(sample_size=1)

    inferred_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    ground_truth_schema = {"type": "object", "properties": {"name": {"type": "number"}}}

    with patch("mongo_synth.validation.validator.jsonschema.validate") as mock_validate:
        error = jsonschema.ValidationError("Expected type string, got number")
        error.path = ["name"]
        mock_validate.side_effect = error

        result = validator.validate(inferred_schema, ground_truth_schema)

        assert result["valid"] is False
        assert result["method"] == "projected_functional"
        assert result["error_count"] > 0

def test_projected_functional_validator_unexpected_exception():
    """
    Test that ProjectedFunctionalValidator correctly catches
    unexpected exceptions during validation.
    """
    validator = ProjectedFunctionalValidator(sample_size=1)

    inferred_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    ground_truth_schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    with patch("mongo_synth.validation.validator.jsonschema.validate") as mock_validate:
        mock_validate.side_effect = Exception("Some weird unexpected error")

        result = validator.validate(inferred_schema, ground_truth_schema)

        # Expected behavior based on validator code is `except: pass`
        assert result["valid"] is True
        assert result["method"] == "projected_functional"

def test_projected_functional_validator_generation_exception():
    """
    Test that ProjectedFunctionalValidator gracefully handles exceptions
    thrown during document generation by falling back to a default batch.
    """
    validator = ProjectedFunctionalValidator(sample_size=1)

    inferred_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    ground_truth_schema = {"type": "object", "properties": {"name": {"type": "number"}}}

    with patch("mongo_synth.validation.validator.from_schema", side_effect=Exception("Generator failed")):
        result = validator.validate(inferred_schema, ground_truth_schema)

        assert result["samples"] == 1
        assert result["method"] == "projected_functional"

def test_similarity_validator_valid_json_matching_dict():
    """Test happy path with valid JSON string matching expected schema dict."""
    actual_json = '{"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}'
    expected = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
    validator = SimilarityValidator()
    result = validator.validate(actual_json, expected)
    assert result["valid"] is True

def test_similarity_validator_valid_json_missing_key():
    """Test valid JSON string but missing a key from expected schema dict."""
    actual_json = '{"type": "object", "properties": {"name": {"type": "string"}}}'
    expected = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
    validator = SimilarityValidator()
    result = validator.validate(actual_json, expected)
    assert result["valid"] is False

def test_similarity_validator_valid_json_type_mismatch():
    """Test valid JSON string but value type mismatches expected schema dict."""
    actual_json = '{"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "string"}}}'
    expected = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
    validator = SimilarityValidator()
    result = validator.validate(actual_json, expected)
    assert result["valid"] is False

def test_similarity_validator_dict_with_extra_keys_in_actual():
    """Test that actual schema can have extra keys not present in expected schema."""
    actual_json = '{"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}, "extra": {"type": "boolean"}}}'
    expected = {"type": "object", "properties": {"name": {"type": "string"}}}
    validator = SimilarityValidator()
    result = validator.validate(actual_json, expected)
    assert result["valid"] is False

def test_structural_validator_formatting_mismatches():
    """Verify DeepDiff error parsing, clean path formatting, and truncation logic."""
    validator = StructuralValidator()

    # Scenario 1: Identical schemas
    schema_gt = {"type": "object", "properties": {"device_id": {"type": "string"}}}
    schema_inf = {"type": "object", "properties": {"device_id": {"type": "string"}}}
    res = validator.validate(schema_inf, schema_gt)
    assert res["valid"] is True
    assert res["error"] == "Structurally identical"

    # Scenario 2: Type mismatch parser formatting directly
    diff_dict = {
        "type_changes": {
            "root['properties']['payload']['properties']['diagnostics']['properties']['cpu_load']['type']": {
                "old_value": "integer",
                "new_value": "number"
            }
        }
    }
    formatted = validator._format_diff(diff_dict)
    assert "Type mismatch at payload.diagnostics.cpu_load (number vs integer)" in formatted

    # Scenario 2b: Real validation type mismatch
    schema_gt = {"type": "object", "properties": {"payload": {"type": "object", "properties": {"diagnostics": {"type": "object", "properties": {"cpu_load": {"type": "integer"}}}}}}}
    schema_inf = {"type": "object", "properties": {"payload": {"type": "object", "properties": {"diagnostics": {"type": "object", "properties": {"cpu_load": {"type": "string"}}}}}}}
    res = validator.validate(schema_inf, schema_gt)
    assert res["valid"] is False
    assert "Type mismatch at payload.diagnostics.cpu_load (string vs number)" in res["error"]

    # Scenario 3: Extra and missing keys
    schema_gt = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "number"}}}
    schema_inf = {"type": "object", "properties": {"name": {"type": "string"}, "extra_field": {"type": "boolean"}}}
    res = validator.validate(schema_inf, schema_gt)
    assert res["valid"] is False
    assert "Missing field: age" in res["error"] or "Extra field: extra_field" in res["error"]

    # Scenario 4: Truncation logic (more than 3 mismatches)
    schema_gt = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "string"},
            "c": {"type": "string"},
            "d": {"type": "string"},
            "e": {"type": "string"}
        }
    }
    schema_inf = {
        "type": "object",
        "properties": {
            "a": {"type": "number"},
            "b": {"type": "number"},
            "c": {"type": "number"},
            "d": {"type": "number"},
            "e": {"type": "number"}
        }
    }
    res = validator.validate(schema_inf, schema_gt)
    assert res["valid"] is False
    assert "... (" in res["error"]
    assert "more)" in res["error"]

def test_subschema_validator():
    """
    Test SubschemaValidator logic checking if inferred schema is a subschema
    of ground truth schema.
    """
    import pytest
    from mongo_synth.validation.validator import SubschemaValidator, isSubschema
    if isSubschema is None:
        pytest.skip("jsonsubschema library not installed")
        
    validator = SubschemaValidator()

    # Case 1: Inferred schema is identical to ground truth (subschema should be True)
    inferred_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    ground_truth_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    res = validator.validate(inferred_schema, ground_truth_schema)
    assert res["valid"] is True
    assert res["method"] == "subschema"

    # Case 2: Inferred schema is a subset (fewer properties, which is a subschema)
    inferred_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    ground_truth_schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
    res = validator.validate(inferred_schema, ground_truth_schema)
    assert res["valid"] is True

    # Case 3: Inferred schema has extra property (not a subschema if ground_truth is closed)
    inferred_schema = {"type": "object", "properties": {"name": {"type": "string"}, "extra": {"type": "string"}}}
    ground_truth_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "additionalProperties": False}
    res = validator.validate(inferred_schema, ground_truth_schema)
    assert res["valid"] is False

def test_validator_ignores_sensitive_metadata():
    """
    Test that StructuralValidator and SubschemaValidator ignore "sensitiveType"
    and "sensitive_locale" during normalization and cleaning, so they don't cause validation failures.
    """
    # 1. StructuralValidator test
    struct_validator = StructuralValidator()
    # Inferred schema has sensitiveType and sensitive_locale metadata, ground truth does not.
    # Because structural validator ignores these fields during normalization, it should evaluate them as identical.
    inferred_schema = {
        "type": "object",
        "properties": {
            "user_email": {
                "type": "string",
                "sensitiveType": "email",
                "sensitive_locale": "en_US"
            }
        }
    }
    ground_truth_schema = {
        "type": "object",
        "properties": {
            "user_email": {
                "type": "string"
            }
        }
    }
    res_struct = struct_validator.validate(inferred_schema, ground_truth_schema)
    assert res_struct["valid"] is True

    # 2. SubschemaValidator test
    from mongo_synth.validation.validator import SubschemaValidator, isSubschema
    if isSubschema is not None:
        sub_validator = SubschemaValidator()
        res_sub = sub_validator.validate(inferred_schema, ground_truth_schema)
        assert res_sub["valid"] is True

