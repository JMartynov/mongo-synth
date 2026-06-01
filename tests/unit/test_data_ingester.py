import pytest
from unittest.mock import MagicMock
from mongo_synth.ingestion.data_ingester import DataIngester, SecurityError

def test_data_ingester_safety_lock():
    """
    Tests that the DataIngester raises a SecurityError when the target URI
    matches the configured live source URI under various normalization situations.
    """
    mock_collection = MagicMock()

    # Identical URIs
    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(target_collection=mock_collection, target_uri="mongodb://localhost:27017/test", live_source_uri="mongodb://localhost:27017/test")

    # Localhost vs 127.0.0.1
    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(target_collection=mock_collection, target_uri="mongodb://localhost:27017/test", live_source_uri="mongodb://127.0.0.1:27017/test")

    # Casing of hostnames
    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(target_collection=mock_collection, target_uri="mongodb://PROD-HOST:27017/test", live_source_uri="mongodb://prod-host:27017/test")

    # User credentials and query params in target_uri
    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(target_collection=mock_collection, target_uri="mongodb://user:pass@localhost:27017/test?authSource=admin", live_source_uri="mongodb://localhost:27017/test")

    # Multiple hosts in different order
    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(target_collection=mock_collection, target_uri="mongodb://host1:27017,host2:27018/test", live_source_uri="mongodb://host2:27018,host1:27017/test")

    # live_source_uri without database (blocks all databases on that host)
    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(target_collection=mock_collection, target_uri="mongodb://localhost:27017/my_test_db", live_source_uri="mongodb://localhost:27017/")

    # live_source_uri with database should NOT block a different database
    mock_collection_other_db = MagicMock()
    mock_collection_other_db.database.name = "safe_db"
    
    # This should succeed without raising SecurityError
    DataIngester(target_collection=mock_collection_other_db, target_uri="mongodb://localhost:27017/safe_db", live_source_uri="mongodb://localhost:27017/prod_db")

def test_data_ingester_batching():
    """
    Tests that the DataIngester correctly chunks a generator of 12,000 documents
    into batches based on the configured batch_size (5000) and calls insert_many exactly 3 times.
    """
    mock_collection = MagicMock()
    target_uri = "mongodb://localhost:27017" # Safe URI

    # Instantiate ingester
    ingester = DataIngester(target_collection=mock_collection, target_uri=target_uri, batch_size=5000)

    # Verify our configured test batch size is what we expect for the test
    assert ingester.batch_size == 5000

    # Create a generator for 12,000 documents
    def doc_generator():
        for i in range(12000):
            yield {"_id": i, "name": f"synthetic_doc_{i}"}

    total_inserted = ingester.ingest(doc_generator())

    # Verify exactly 12000 documents were counted as inserted
    assert total_inserted == 12000

    # Verify insert_many was called exactly 3 times (5000, 5000, 2000)
    assert mock_collection.insert_many.call_count == 3

    # Validate the sizes of the batches passed to insert_many
    call_args_list = mock_collection.insert_many.call_args_list
    assert len(call_args_list[0][0][0]) == 5000
    assert len(call_args_list[1][0][0]) == 5000
    assert len(call_args_list[2][0][0]) == 2000

def test_data_ingester_empty_generator():
    """
    Tests that the DataIngester gracefully handles an empty generator without calling insert_many.
    """
    mock_collection = MagicMock()
    target_uri = "mongodb://localhost:27017"

    ingester = DataIngester(target_collection=mock_collection, target_uri=target_uri)

    def empty_generator():
        yield from []

    total_inserted = ingester.ingest(empty_generator())

    assert total_inserted == 0
    assert mock_collection.insert_many.call_count == 0
