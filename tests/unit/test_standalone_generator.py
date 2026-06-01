import pytest
from unittest.mock import MagicMock, patch, mock_open
import json

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
             live_source_uri=""
         )
