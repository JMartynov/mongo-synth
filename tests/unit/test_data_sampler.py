import pytest
from unittest.mock import Mock, MagicMock
from mongo_synth.profiler.data_sampler import DataSampler

def test_data_sampler_initialization():
    mock_collection = Mock()
    sampler = DataSampler(mock_collection)
    assert sampler.collection == mock_collection
    assert isinstance(sampler.sample_size, int)
    assert isinstance(sampler.max_cardinality, int)

def test_profile_fields_empty_list():
    mock_collection = Mock()
    sampler = DataSampler(mock_collection)
    result = sampler.profile_fields([])
    assert result == {}
    mock_collection.aggregate.assert_not_called()

def test_profile_fields_happy_path():
    mock_collection = Mock()
    sampler = DataSampler(mock_collection)

    mock_cursor = [
        {
            "status": [
                {"_id": "active", "count": 90},
                {"_id": "archived", "count": 10}
            ]
        }
    ]
    mock_collection.aggregate.return_value = mock_cursor

    result = sampler.profile_fields(["status"])

    mock_collection.aggregate.assert_called_once()
    pipeline = mock_collection.aggregate.call_args[0][0]

    assert len(pipeline) == 2
    assert "$sample" in pipeline[0]
    assert "$facet" in pipeline[1]
    assert "status" in pipeline[1]["$facet"]

    assert "status" in result
    assert result["status"]["active"] == 0.9
    assert result["status"]["archived"] == 0.1

def test_profile_fields_missing_field():
    mock_collection = Mock()
    sampler = DataSampler(mock_collection)

    mock_cursor = [
        {
            "nonexistent": []
        }
    ]
    mock_collection.aggregate.return_value = mock_cursor

    result = sampler.profile_fields(["nonexistent"])

    assert "nonexistent" in result
    assert result["nonexistent"] == {}

def test_profile_fields_high_cardinality():
    mock_collection = Mock()
    sampler = DataSampler(mock_collection)

    mock_cursor = [
        {
            "uuid": [
                {"_id": f"uuid-{i}", "count": 1} for i in range(sampler.max_cardinality)
            ]
        }
    ]
    mock_collection.aggregate.return_value = mock_cursor

    result = sampler.profile_fields(["uuid"])

    assert "uuid" in result
    assert len(result["uuid"]) == sampler.max_cardinality
    expected_prob = 1.0 / sampler.max_cardinality
    for v in result["uuid"].values():
        assert pytest.approx(v) == expected_prob

def test_profile_fields_complex_type():
    mock_collection = Mock()
    sampler = DataSampler(mock_collection)

    mock_cursor = [
        {
            "address": [
                {"_id": {"city": "NY"}, "count": 1}
            ]
        }
    ]
    mock_collection.aggregate.return_value = mock_cursor

    result = sampler.profile_fields(["address"])

    assert "address" in result
    assert "{'city': 'NY'}" in result["address"]
    assert result["address"]["{'city': 'NY'}"] == 1.0
