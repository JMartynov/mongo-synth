import pytest
import json
import os
import sys
from unittest.mock import MagicMock, patch
from pymongo.errors import BulkWriteError
from mongo_synth.ingestion.data_ingester import DataIngester
from mongo_synth.cli import run_generation

def test_data_ingester_ordered_insertion_error():
    """Verify that when ordered=True, BulkWriteError is re-raised instead of handled."""
    mock_collection = MagicMock()
    # Mock insert_many to raise BulkWriteError
    bwe = BulkWriteError({"nInserted": 1, "writeErrors": [{"index": 1, "errmsg": "duplicate key", "code": 11000}]})
    mock_collection.insert_many.side_effect = bwe

    # Initialize with ordered=True
    ingester = DataIngester(
        target_collection=mock_collection,
        target_uri="mongodb://localhost:27017",
        ordered=True
    )

    batch = [{"_id": 1}, {"_id": 1}]
    
    # Should raise BulkWriteError directly
    with pytest.raises(BulkWriteError):
        ingester._insert_batch(batch)

def test_data_ingester_dynamic_batch_resizing():
    """Verify that when documents are large, batch size dynamically scales down."""
    mock_collection = MagicMock()
    ingester = DataIngester(
        target_collection=mock_collection,
        target_uri="mongodb://localhost:27017",
        batch_size=1000
    )

    # Standard small documents should not trigger resizing
    small_docs = [{"val": "small"} for _ in range(10)]
    ingester.ingest(small_docs)
    assert ingester.batch_size == 1000

    # Extremely large documents should trigger resizing
    large_docs = [{"val": "x" * 5 * 1024 * 1024} for _ in range(10)] # ~5MB documents
    ingester.ingest(large_docs)
    
    # Target size: 12MB. 5MB documents should adjust batch size to 2 (12 / 5 = 2.4 -> 2)
    assert ingester.batch_size == 2

def test_cli_dry_run_validation():
    """Verify that the dry-run CLI path executes client-side schema validation and exits."""
    # Write a temporary schema file
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_schema:
        json.dump({
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "required": ["name"]
        }, f_schema)
        f_schema.close()

        try:
            parser = MagicMock()
            args = MagicMock()
            args.schema = f_schema.name
            args.model = None
            args.config_file = None
            args.uri = "mongodb://localhost:27017"
            args.db = "test"
            args.collection = "test"
            args.live_uri = None
            args.count = 5
            args.batch_size = None
            args.seed = 42
            args.profile = None
            args.anomaly = None
            args.clear = False
            args.inject_sensitive = False
            args.run_id = None
            args.sensitive_locale = None
            args.verifier_output = None
            args.dry_run = True
            
            def exit_side_effect(code=0):
                raise SystemExit(code)

            with patch("sys.exit", side_effect=exit_side_effect) as mock_exit:
                with pytest.raises(SystemExit) as excinfo:
                    run_generation(args, parser)
                assert excinfo.value.code == 0
        finally:
            os.unlink(f_schema.name)


