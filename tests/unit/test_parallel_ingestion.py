import pytest
import json
import tempfile
import os
from unittest.mock import MagicMock, patch
from mongo_synth.cli import _run_generation_worker, run_generation

def test_run_generation_worker_dry_run():
    """Verify that _run_generation_worker performs dry-run validation successfully."""
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "required": ["name"]
        }
    }
    args_dict = {"dry_run": True}
    
    result = _run_generation_worker(
        worker_idx=0,
        task_count=3,
        seed=42,
        args_dict=args_dict,
        blueprint=blueprint
    )
    
    assert result["success"] is True
    assert result["inserted_count"] == 0
    assert result["error_count"] == 0
    assert isinstance(result["verifiers"], list)

def test_run_generation_worker_ingestion_success():
    """Verify that _run_generation_worker connects to MongoDB and inserts documents in ingestion mode."""
    blueprint = {
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            }
        }
    }
    args_dict = {
        "dry_run": False,
        "uri": "mongodb://localhost:27017",
        "db": "test_db",
        "collection": "test_coll",
        "batch_size": 100,
        "live_uri": "",
        "ordered": False
    }

    with patch("pymongo.MongoClient") as mock_client, \
         patch("mongo_synth.ingestion.data_ingester.DataIngester") as mock_ingester:
        
        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_client.return_value.__getitem__.return_value = mock_db
        mock_db.__getitem__.return_value = mock_coll
        
        mock_ingester_instance = mock_ingester.return_value
        mock_ingester_instance.ingest.return_value = 5

        result = _run_generation_worker(
            worker_idx=1,
            task_count=5,
            seed=100,
            args_dict=args_dict,
            blueprint=blueprint
        )

        assert result["success"] is True
        assert result["inserted_count"] == 5
        assert result["error_count"] == 0
        mock_client.assert_called_with("mongodb://localhost:27017", serverSelectionTimeoutMS=5000)
        mock_ingester.assert_called_once()

def test_parallel_pool_execution_distribution():
    """Verify that run_generation splits the count and offsets the seed correctly for the multiprocessing Pool."""
    # Write a temporary schema file so sys.open isn't mocked
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_schema:
        json.dump({
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            }
        }, f_schema)
        f_schema.close()

    try:
        parser = MagicMock()
        args = MagicMock()
        args.schema = f_schema.name
        args.model = None
        args.config_file = None
        args.uri = "mongodb://localhost:27017"
        args.db = "db"
        args.collection = "coll"
        args.count = 10
        args.batch_size = 5
        args.seed = 42
        args.profile = None
        args.anomaly = None
        args.clear = False
        args.inject_sensitive = False
        args.run_id = None
        args.sensitive_locale = None
        args.verifier_output = None
        args.dry_run = True
        args.workers = 3
        args.ordered = False

        with patch("multiprocessing.get_context") as mock_ctx, \
             patch("sys.exit") as mock_exit:
            
            mock_pool = MagicMock()
            mock_ctx.return_value.Pool.return_value = mock_pool
            
            # Mock workers returning success
            mock_pool.starmap.return_value = [
                {"success": True, "inserted_count": 0, "error_count": 0, "verifiers": [{"type": "email", "value": "a@example.com"}]},
                {"success": True, "inserted_count": 0, "error_count": 0, "verifiers": []},
                {"success": True, "inserted_count": 0, "error_count": 0, "verifiers": []}
            ]

            run_generation(args, parser)

            # Verify pool was created and starmap called
            mock_ctx.return_value.Pool.assert_called_once_with(processes=3)
            starmap_args = mock_pool.starmap.call_args[0][1]
            
            # 3 workers: count=10 -> base_count=3, remainder=1 -> counts: 3, 3, 4
            assert len(starmap_args) == 3
            # Worker 0
            assert starmap_args[0][0] == 0 # worker_idx
            assert starmap_args[0][1] == 3 # task_count
            assert starmap_args[0][2] == 42 # seed
            # Worker 1
            assert starmap_args[1][0] == 1
            assert starmap_args[1][1] == 3
            assert starmap_args[1][2] == 43 # seed offset
            # Worker 2
            assert starmap_args[2][0] == 2
            assert starmap_args[2][1] == 4
            assert starmap_args[2][2] == 44 # seed offset
            
            # Verify exit with 0 (since error_count was 0)
            mock_exit.assert_called_with(0)
    finally:
        os.unlink(f_schema.name)

def test_parallel_execution_failure_propagation():
    """Verify that run_generation exits with an error code if any parallel worker fails."""
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_schema:
        json.dump({
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            }
        }, f_schema)
        f_schema.close()

    try:
        parser = MagicMock()
        args = MagicMock()
        args.schema = f_schema.name
        args.model = None
        args.config_file = None
        args.uri = "mongodb://localhost:27017"
        args.db = "db"
        args.collection = "coll"
        args.count = 10
        args.batch_size = 5
        args.seed = 42
        args.profile = None
        args.anomaly = None
        args.clear = False
        args.inject_sensitive = False
        args.run_id = None
        args.sensitive_locale = None
        args.verifier_output = None
        args.dry_run = True
        args.workers = 2
        args.ordered = False

        with patch("multiprocessing.get_context") as mock_ctx, \
             patch("sys.exit") as mock_exit:
            
            mock_pool = MagicMock()
            mock_ctx.return_value.Pool.return_value = mock_pool
            
            # One worker fails
            mock_pool.starmap.return_value = [
                {"success": True, "inserted_count": 0, "error_count": 0, "verifiers": []},
                {"success": False, "error": "Database disconnected"}
            ]

            run_generation(args, parser)
                
            mock_exit.assert_called_with(1)
    finally:
        os.unlink(f_schema.name)
