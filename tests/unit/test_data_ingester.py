import pytest
from unittest.mock import MagicMock
from mongo_synth.ingestion.data_ingester import DataIngester, SecurityError

def test_data_ingester_safety_lock():
    """
    Tests that the DataIngester raises a SecurityError when the target URI
    matches the configured live source URI.
    """
    live_uri = "mongodb+srv://production-cluster"
    mock_collection = MagicMock()

    with pytest.raises(SecurityError, match="Safety Lock Triggered"):
        DataIngester(target_collection=mock_collection, target_uri=live_uri, live_source_uri=live_uri)

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
