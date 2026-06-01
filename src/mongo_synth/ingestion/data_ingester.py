import logging
from typing import Iterable, Dict, Any, Optional
from pymongo.collection import Collection

logger = logging.getLogger(__name__)

class SecurityError(Exception):
    """Raised when an operation violates security constraints."""
    pass

class DataIngester:
    """
    Utility for bulk-inserting synthetic documents into an isolated MongoDB collection.
    Includes safety constraints to prevent operations against live production URIs.
    """

    def __init__(self, target_collection: Collection, target_uri: str, batch_size: Optional[int] = None, live_source_uri: Optional[str] = None):
        """
        Initializes the DataIngester.

        Args:
            target_collection (Collection): The PyMongo Collection object where data will be inserted.
            target_uri (str): The connection URI used to create the target collection, verified against the live source URI.
            batch_size (int, optional): Ingest batch size override.
            live_source_uri (str, optional): Connection URI of the production environment to block writes to.
        """
        self.target_collection = target_collection
        self.target_uri = target_uri

        if batch_size is not None:
            self.batch_size = batch_size
        else:
            try:
                from mongo_synth.config import generator_config
                self.batch_size = generator_config.get("generation.batch_size", 5000)
            except Exception:
                self.batch_size = 5000

        if live_source_uri is not None:
            self.live_source_uri = live_source_uri
        else:
            try:
                from mongo_synth.config import generator_config
                self.live_source_uri = generator_config.get("mongodb.live_source_uri", "")
            except Exception:
                self.live_source_uri = ""

        self._verify_safety_constraints()

    def _verify_safety_constraints(self) -> None:
        """
        Ensures that the target URI is not pointing to the live/production environment.

        Raises:
            SecurityError: If target_uri matches the configured live_source_uri.
        """
        if not self.live_source_uri:
            logger.warning("live_source_uri is not configured. Safety lock might be ineffective.")

        if self.live_source_uri and self.target_uri == self.live_source_uri:
            raise SecurityError(
                "Safety Lock Triggered: Attempting to ingest synthetic data into the configured live source URI. "
                "This operation is strictly prohibited to prevent production corruption."
            )

    def ingest(self, documents: Iterable[Dict[str, Any]]) -> int:
        """
        Iterates over the generated documents and performs bulk inserts using the configured batch size.

        Args:
            documents (Iterable[Dict[str, Any]]): An iterable or generator of documents to insert.

        Returns:
            int: The total number of documents successfully inserted.
        """
        total_inserted = 0
        current_batch = []

        for doc in documents:
            current_batch.append(doc)

            if len(current_batch) >= self.batch_size:
                self._insert_batch(list(current_batch))
                total_inserted += len(current_batch)
                current_batch.clear()

        # Insert any remaining documents
        if current_batch:
            self._insert_batch(list(current_batch))
            total_inserted += len(current_batch)

        return total_inserted

    def _insert_batch(self, batch: list) -> None:
        """
        Helper method to insert a batch of documents into the collection.
        Uses ordered=False to maximize insertion speed and prevent a single error from halting the entire batch.
        """
        try:
            self.target_collection.insert_many(batch, ordered=False)
        except Exception as e:
            logger.error(f"Error occurred during batch insertion: {e}")
            raise
