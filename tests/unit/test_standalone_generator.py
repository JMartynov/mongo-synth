import pytest
from unittest.mock import MagicMock, patch, mock_open
import json

from datetime import datetime
from bson.objectid import ObjectId
from mongo_synth.config import GeneratorConfig
from mongo_synth.generators.json_schema_generator import JsonSchemaGenerator
from mongo_synth.generators.anomaly_generator import AnomalyGenerator
from mongo_synth.ingestion.data_ingester import DataIngester, SecurityError

def test_generator_config_defaults():
    config = GeneratorConfig()
    assert config.get("mongodb.uri") == "mongodb://localhost:27017"
    assert config.get("mongodb.db_name") == "generator_db"
    assert config.get("generation.batch_size") == 5000
    assert config.get("nonexistent.key", "fallback") == "fallback"

@patch("os.path.exists", return_value=True)
@patch("builtins.open", new_callable=mock_open, read_data="mongodb:\n  db_name: custom_db\n  live_source_uri: mongodb://prod\ngeneration:\n  batch_size: 100\n")
def test_generator_config_load_yaml(mock_file, mock_exists):
    config = GeneratorConfig()
    config.load_from_yaml("fake_config.yaml")
    assert config.get("mongodb.db_name") == "custom_db"
    assert config.get("mongodb.live_source_uri") == "mongodb://prod"
    assert config.get("generation.batch_size") == 100

def test_standalone_data_ingester_safety_lock():
    mock_collection = MagicMock()
    live_uri = "mongodb://prod-cluster"
    
    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(
            target_collection=mock_collection,
            target_uri=live_uri,
            live_source_uri=live_uri
        )

def test_standalone_data_ingester_ingest():
    mock_collection = MagicMock()
    ingester = DataIngester(
        target_collection=mock_collection,
        target_uri="mongodb://localhost:27017",
        batch_size=5,
        live_source_uri="mongodb://prod"
    )
    
    docs = [{"_id": i} for i in range(12)]
    total = ingester.ingest(docs)
    
    assert total == 12
    assert mock_collection.insert_many.call_count == 3

def test_standalone_cli_parser():
    from mongo_synth.cli import main
    
    mock_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"}
        }
    }
    
    test_args = [
        "cli.py",
        "--schema", "dummy_schema.json",
        "--uri", "mongodb://localhost:27017",
        "--db", "cli_db",
        "--collection", "cli_coll",
        "--count", "10",
        "--batch-size", "5",
        "--seed", "99",
        "--clear"
    ]
    
    with patch("sys.argv", test_args), \
         patch("builtins.open", mock_open(read_data=json.dumps(mock_schema))), \
         patch("mongo_synth.cli.MongoClient") as mock_client, \
         patch("mongo_synth.cli.DataIngester") as mock_ingester:
         
         mock_db = MagicMock()
         mock_coll = MagicMock()
         mock_client.return_value.__getitem__.return_value = mock_db
         mock_db.__getitem__.return_value = mock_coll
         
         mock_ingester_instance = mock_ingester.return_value
         mock_ingester_instance.ingest.return_value = 10
         
         main()
         
         mock_client.assert_called_with("mongodb://localhost:27017", serverSelectionTimeoutMS=5000)
         mock_coll.delete_many.assert_called_once_with({})
         mock_ingester.assert_called_with(
             mock_coll,
             "mongodb://localhost:27017",
             batch_size=5,
             live_source_uri="",
             ordered=False
         )

def test_schema_aware_replication_mutations():
    """
    Tests that replicating documents in BaseGenerator correctly and dynamically
    mutates unique/high-cardinality fields based on their schema definition.
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "_id": {"type": "string", "bsonType": "objectId"},
                "session_uuid": {"type": "string", "format": "uuid"},
                "dec_val": {"type": "number", "bsonType": "decimal", "unique": True},
                "bin_val": {"type": "string", "bsonType": "binData", "unique": True},
                "double_val": {"type": "number", "bsonType": "double"},
                "long_val": {"type": "integer", "bsonType": "long"},
                "ts_val": {"type": "integer", "bsonType": "timestamp", "unique": True},
                "regex_val": {"type": "string", "bsonType": "regex", "unique": True},
                "nested": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "unique": True},
                        "timestamp": {"type": "string", "bsonType": "date"},
                        "static_val": {"type": "string"}
                    },
                    "required": ["device_id", "timestamp", "static_val"]
                }
            },
            "required": ["_id", "session_uuid", "dec_val", "bin_val", "double_val", "long_val", "ts_val", "regex_val", "nested"]
        },
        "metadata": {
            "expected_document_count": 5
        }
    }
    
    # Instantiate JsonSchemaGenerator with a count of 5
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=5)
    
    # We patch pool_size calculation in the base generator namespace to trigger replication at count 2
    with patch("mongo_synth.generators.base.min", return_value=2):
        batch = generator.generate_batch()
        
    assert len(batch) == 5
    
    # Verify BSON types are correctly translated
    from bson.decimal128 import Decimal128
    from bson.binary import Binary
    from bson.timestamp import Timestamp
    from bson.regex import Regex
    
    for doc in batch:
        assert isinstance(doc["_id"], ObjectId)
        assert isinstance(doc["dec_val"], Decimal128)
        assert isinstance(doc["bin_val"], Binary)
        assert isinstance(doc["double_val"], float)
        assert isinstance(doc["long_val"], int)
        assert isinstance(doc["ts_val"], Timestamp)
        assert isinstance(doc["regex_val"], Regex)
        assert isinstance(doc["nested"]["timestamp"], datetime)
    
    # Verify that:
    # 1. _id is mutated and unique across all 5 generated documents.
    ids = [doc["_id"] for doc in batch]
    assert len(set(ids)) == 5
    
    # 2. session_uuid is mutated and unique.
    uuids = [doc["session_uuid"] for doc in batch]
    assert len(set(uuids)) == 5
    for u in uuids:
        # A mutated session_uuid must be a valid UUID hex string of length 36
        assert len(u) == 36
        
    # 3. nested.device_id is mutated and unique.
    device_ids = [doc["nested"]["device_id"] for doc in batch]
    assert len(set(device_ids)) == 5
    
    # 4. nested.timestamp is mutated and unique.
    timestamps = [doc["nested"]["timestamp"] for doc in batch]
    assert len(set(timestamps)) == 5
    
    # 5. dec_val is mutated and unique.
    dec_vals = [str(doc["dec_val"]) for doc in batch]
    assert len(set(dec_vals)) == 5
    
    # 6. bin_val is mutated and unique.
    bin_vals = [bytes(doc["bin_val"]) for doc in batch]
    assert len(set(bin_vals)) == 5
    
    # 7. ts_val is mutated and unique.
    ts_vals = [str(doc["ts_val"]) for doc in batch]
    assert len(set(ts_vals)) == 5
    
    # 8. regex_val is mutated and unique.
    regex_vals = [str(doc["regex_val"]) for doc in batch]
    assert len(set(regex_vals)) == 5
    
    # 9. nested.static_val (not marked unique) should NOT be unique across all 5 docs.
    static_vals = [doc["nested"]["static_val"] for doc in batch]
    assert len(set(static_vals)) <= 2

def test_anomaly_generator_mutations():
    """
    Tests that AnomalyGenerator generates schema-conforming documents
    and correctly injects various anomalies into them.
    """
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "nested_doc": {
                    "type": "object",
                    "properties": {
                        "val": {"type": "string"}
                    },
                    "required": ["val"]
                },
                "array_field": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["name", "nested_doc", "array_field"]
        }
    }

    # Test whitespace_keys
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "whitespace_keys"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert "name" in doc
    assert "nested_doc" in doc
    assert "   " in doc and "\t" in doc

    # Test empty_embedded_docs
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "empty_embedded_docs"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert "name" in doc
    assert doc["nested_doc"] == {}

    # Test mixed_type_arrays
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "mixed_type_arrays"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert "name" in doc
    assert isinstance(doc["array_field"], list)
    element_types = {type(x) for x in doc["array_field"]}
    assert len(element_types) > 0

    # Test extreme_nesting
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "extreme_nesting"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert any(isinstance(v, dict) and "nested" in v for v in doc.values())

    # Test non_standard_chars
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "non_standard_chars"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert "user!@#$%" in doc
    assert "💩" in doc

    # Test bson_type_impersonation
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "bson_type_impersonation"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert "fake_object_id" in doc
    assert "fake_date" in doc

    # Test massive_payload
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "massive_payload"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    lengths = [len(v) for v in doc.values() if isinstance(v, str)]
    assert any(l >= 1024 * 1024 for l in lengths)

    # Test deep_null_arrays
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "deep_null_arrays"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert doc["array_field"] == [None, [None, [None]]]

    # Test dot_notation_keys
    bp = json.loads(json.dumps(blueprint))
    bp["schema"]["anomaly_type"] = "dot_notation_keys"
    gen = AnomalyGenerator(bp, documents_per_collection=1)
    doc = gen.generate_batch()[0]
    assert "user.name" in doc
    assert "address.city" in doc

def test_cli_generate_pydantic():
    from mongo_synth.cli import main
    test_args = [
        "cli.py",
        "generate",
        "--model", "tests.unit.test_pydantic_generator:SimpleModel",
        "--uri", "mongodb://localhost:27017",
        "--count", "3",
        "--seed", "42"
    ]
    with patch("sys.argv", test_args), \
         patch("mongo_synth.cli.MongoClient") as mock_client, \
         patch("mongo_synth.cli.DataIngester") as mock_ingester:
         
         mock_db = MagicMock()
         mock_coll = MagicMock()
         mock_client.return_value.__getitem__.return_value = mock_db
         mock_db.__getitem__.return_value = mock_coll
         
         mock_ingester_instance = mock_ingester.return_value
         mock_ingester_instance.ingest.return_value = 3
         
         main()
         
         mock_client.assert_called_once()
         mock_ingester.assert_called_once()

def test_cli_validate_success():
    from mongo_synth.cli import main
    import tempfile
    import os
    
    schema_data = {"type": "object", "properties": {"name": {"type": "string"}}}
    
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_gt, \
         tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_inf:
        json.dump(schema_data, f_gt)
        json.dump(schema_data, f_inf)
        f_gt.close()
        f_inf.close()
        
        try:
            test_args = [
                "cli.py",
                "validate",
                "--schema", f_gt.name,
                "--inferred", f_inf.name,
                "--validator", "structural"
            ]
            with patch("sys.argv", test_args):
                with pytest.raises(SystemExit) as excinfo:
                    main()
                assert excinfo.value.code == 0
        finally:
            os.unlink(f_gt.name)
            os.unlink(f_inf.name)

def test_cli_validate_failure():
    from mongo_synth.cli import main
    import tempfile
    import os
    
    gt_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    inf_schema = {"type": "object", "properties": {"name": {"type": "number"}}}
    
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_gt, \
         tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_inf:
        json.dump(gt_schema, f_gt)
        json.dump(inf_schema, f_inf)
        f_gt.close()
        f_inf.close()
        
        try:
            test_args = [
                "cli.py",
                "validate",
                "--schema", f_gt.name,
                "--inferred", f_inf.name,
                "--validator", "structural"
            ]
            with patch("sys.argv", test_args):
                with pytest.raises(SystemExit) as excinfo:
                    main()
                assert excinfo.value.code == 1
        finally:
            os.unlink(f_gt.name)
            os.unlink(f_inf.name)


def test_anomaly_generator_non_dict_fallback():
    """
    Tests that AnomalyGenerator correctly falls back to an empty dictionary
    and injects anomalies when the input document generated is not a dictionary (e.g. primitive/None).
    """
    blueprint = {
        "schema": {
            "type": "string",  # Primitive type schema
            "anomaly_type": "whitespace_keys"
        },
        "metadata": {
            "expected_document_count": 1
        }
    }
    generator = AnomalyGenerator(blueprint, documents_per_collection=1)
    
    batch = generator.generate_batch()
    assert len(batch) == 1
    doc = batch[0]
    assert isinstance(doc, dict)
    assert "   " in doc and "\t" in doc


def test_schema_sensitive_type():
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "user_email": {"type": "string", "sensitiveType": "email"},
                "user_pass": {"type": "string", "sensitiveType": "password"}
            },
            "required": ["user_email", "user_pass"]
        },
        "metadata": {
            "run_id": "cli_canary"
        }
    }
    generator = JsonSchemaGenerator(blueprint, documents_per_collection=3)
    
    # Force replication to ensure unique mutation logic runs
    with patch("mongo_synth.generators.base.min", return_value=1):
        batch = generator.generate_batch()

    assert len(batch) == 3
    emails = [d["user_email"] for d in batch]
    passwords = [d["user_pass"] for d in batch]
    
    # Check that they are generated, unique, and contain the prefix
    assert len(set(emails)) == 3
    assert len(set(passwords)) == 3
    for email in emails:
        assert email.startswith("cli_canary_")
    for password in passwords:
        assert password.startswith("cli_canary_")

    # Check verifiers are tracked
    assert len(generator.sensitive_tracker.verifiers) == 6


def test_cli_sensitive_data_options():
    from mongo_synth.cli import main
    import tempfile
    import os

    mock_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"}
        }
    }

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f_schema, \
         tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f_verifier:
        
        f_schema.write(json.dumps(mock_schema).encode('utf-8'))
        f_schema.close()
        f_verifier.close()

        try:
            test_args = [
                "cli.py",
                "generate",
                "--schema", f_schema.name,
                "--uri", "mongodb://localhost:27017",
                "--db", "cli_test_db",
                "--collection", "cli_test_coll",
                "--count", "2",
                "--inject-sensitive",
                "--run-id", "test_canary",
                "--verifier-output", f_verifier.name
            ]

            with patch("sys.argv", test_args), \
                 patch("mongo_synth.cli.MongoClient") as mock_client, \
                 patch("mongo_synth.cli.DataIngester") as mock_ingester:

                mock_db = MagicMock()
                mock_coll = MagicMock()
                mock_client.return_value.__getitem__.return_value = mock_db
                mock_db.__getitem__.return_value = mock_coll

                mock_ingester_instance = mock_ingester.return_value
                mock_ingester_instance.ingest.return_value = 2

                main()

                # Read output verifiers file
                with open(f_verifier.name, "r") as f:
                    verifiers = json.load(f)

                # Auto inject adds 8 sensitive values per document. 2 docs * 8 = 16 values.
                assert len(verifiers) == 16
                for v in verifiers:
                    assert "type" in v
                    assert "value" in v
                    # Check prefixing where applicable
                    if v["type"] in ["email", "name", "password"]:
                        assert "test_canary" in v["value"]

        finally:
            os.unlink(f_schema.name)
            os.unlink(f_verifier.name)


def test_data_ingester_bulk_write_error_handling():
    from pymongo.errors import BulkWriteError
    
    mock_collection = MagicMock()
    # Mock insert_many to raise BulkWriteError
    bwe = BulkWriteError({"nInserted": 4, "writeErrors": [{"index": 4, "errmsg": "duplicate key", "code": 11000}]})
    mock_collection.insert_many.side_effect = bwe

    ingester = DataIngester(
        target_collection=mock_collection,
        target_uri="mongodb://localhost:27017"
    )

    batch = [{"_id": i} for i in range(5)]
    inserted = ingester._insert_batch(batch)

    # Returns nInserted from bwe.details instead of raising error
    assert inserted == 4

    # Other exceptions should still be raised
    mock_collection.insert_many.side_effect = ValueError("Catastrophic error")
    with pytest.raises(ValueError, match="Catastrophic error"):
        ingester._insert_batch(batch)


def test_data_ingester_bulk_write_error_re_raises_on_validation():
    from pymongo.errors import BulkWriteError
    
    mock_collection = MagicMock()
    # Mock insert_many to raise BulkWriteError with a validation error code 121
    bwe = BulkWriteError({"nInserted": 1, "writeErrors": [{"index": 1, "errmsg": "Document failed validation", "code": 121}]})
    mock_collection.insert_many.side_effect = bwe

    ingester = DataIngester(
        target_collection=mock_collection,
        target_uri="mongodb://localhost:27017"
    )

    batch = [{"_id": i} for i in range(2)]
    # Should not swallow validation errors, must re-raise the exception
    with pytest.raises(BulkWriteError):
        ingester._insert_batch(batch)


def test_cli_yaml_config_fallback():
    from mongo_synth.cli import run_generation
    from mongo_synth.config import generator_config
    import yaml
    import tempfile
    import os
    
    config_data = {
        "generation": {
            "inject_sensitive": True,
            "run_id": "yaml_run",
            "sensitive_locale": "fr_FR",
            "verifier_output": "yaml_verifiers.json"
        }
    }
    
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f_yaml, \
         tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_schema:
         
        yaml.dump(config_data, f_yaml)
        json.dump({"type": "object", "properties": {"name": {"type": "string"}}}, f_schema)
        f_yaml.close()
        f_schema.close()
        
        try:
            parser = MagicMock()
            args = MagicMock()
            args.schema = f_schema.name
            args.model = None
            args.config_file = f_yaml.name
            args.uri = None
            args.db = None
            args.collection = None
            args.live_uri = None
            args.count = 2
            args.batch_size = None
            args.seed = None
            args.profile = None
            args.anomaly = None
            args.clear = False
            # CLI args are unset so they fall back to YAML
            args.inject_sensitive = False
            args.run_id = None
            args.sensitive_locale = None
            args.verifier_output = None
            
            with patch("mongo_synth.cli.MongoClient") as mock_client, \
                 patch("mongo_synth.cli.DataIngester") as mock_ingester, \
                 patch("builtins.open", side_effect=open):
                 
                mock_db = MagicMock()
                mock_coll = MagicMock()
                mock_client.return_value.__getitem__.return_value = mock_db
                mock_db.__getitem__.return_value = mock_coll
                mock_ingester_instance = mock_ingester.return_value
                mock_ingester_instance.ingest.return_value = 2
                
                run_generation(args, parser)
                
                # Check that config values were loaded correctly into generator_config
                assert generator_config.get("generation.inject_sensitive") is True
                assert generator_config.get("generation.run_id") == "yaml_run"
                assert generator_config.get("generation.sensitive_locale") == "fr_FR"
                assert generator_config.get("generation.verifier_output") == "yaml_verifiers.json"
        finally:
            os.unlink(f_yaml.name)
            os.unlink(f_schema.name)




