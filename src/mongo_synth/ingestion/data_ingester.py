import logging
from typing import Iterable, Dict, Any, Optional, Tuple, List
from pymongo.collection import Collection

logger = logging.getLogger(__name__)

class SecurityError(Exception):
    """Raised when an operation violates security constraints."""
    pass

def parse_mongo_uri_safely(uri: str) -> Tuple[List[Tuple[str, Optional[int]]], Optional[str]]:
    """
    Parses a MongoDB connection URI into a list of node tuples (host, port) and a database name.
    Attempts to use PyMongo's parse_uri first, and falls back to a manual parser on DNS/parsing failures.
    """
    try:
        from pymongo.uri_parser import parse_uri
        parsed = parse_uri(uri)
        return parsed.get("nodelist", []), parsed.get("database")
    except Exception:
        pass

    scheme = "mongodb"
    if uri.startswith("mongodb://"):
        rest = uri[len("mongodb://"):]
    elif uri.startswith("mongodb+srv://"):
        scheme = "mongodb+srv"
        rest = uri[len("mongodb+srv://"):]
    else:
        rest = uri

    if '@' in rest:
        _, rest = rest.rsplit('@', 1)

    db_part = ""
    hosts_part = rest
    if '/' in rest:
        hosts_part, db_part = rest.split('/', 1)
        if '?' in db_part:
            db_part = db_part.split('?', 1)[0]

    database = db_part if db_part else None

    nodelist = []
    for host_str in hosts_part.split(','):
        if not host_str:
            continue
        if host_str.startswith('['):
            end_bracket = host_str.find(']')
            if end_bracket != -1:
                host = host_str[1:end_bracket]
                port_part = host_str[end_bracket+1:]
                if port_part.startswith(':'):
                    port = int(port_part[1:])
                else:
                    port = 27017 if scheme == "mongodb" else None
            else:
                host = host_str
                port = 27017 if scheme == "mongodb" else None
        else:
            if ':' in host_str:
                host, port_str = host_str.split(':', 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 27017 if scheme == "mongodb" else None
            else:
                host = host_str
                port = 27017 if scheme == "mongodb" else None

        nodelist.append((host, port))

    return nodelist, database

def normalize_hosts(nodelist: List[Tuple[str, Optional[int]]]) -> List[Tuple[str, Optional[int]]]:
    """Normalizes hostnames to lowercase and maps localhost to 127.0.0.1, sorting the node list."""
    normalized = []
    for host, port in nodelist:
        h = host.lower()
        if h == "localhost":
            h = "127.0.0.1"
        normalized.append((h, port))
    return sorted(normalized)

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
            return

        try:
            target_nodes, _ = parse_mongo_uri_safely(self.target_uri)
            live_nodes, live_db = parse_mongo_uri_safely(self.live_source_uri)
        except Exception as e:
            logger.warning(f"Failed to parse or normalize URIs, falling back to string match: {e}")
            if self.target_uri == self.live_source_uri:
                raise SecurityError(
                    "Safety Lock Triggered: Attempting to ingest synthetic data into the configured live source URI. "
                    "This operation is strictly prohibited to prevent production corruption."
                )
            return

        # Target database name is best retrieved directly from the collection object to be robust
        target_db = None
        if hasattr(self.target_collection, "database") and self.target_collection.database is not None:
            db_obj = self.target_collection.database
            # If the database name attribute is a standard string, use it
            if hasattr(db_obj, "name") and isinstance(db_obj.name, str):
                target_db = db_obj.name

        # Fall back to target_uri parsed database if collection database name wasn't a valid string
        if not target_db:
            _, parsed_target_db = parse_mongo_uri_safely(self.target_uri)
            target_db = parsed_target_db

        # Compare hosts and database target
        hosts_match = normalize_hosts(target_nodes) == normalize_hosts(live_nodes)
        
        # If live_db is None, it means the whole host is blocked.
        # Otherwise, block if target_db matches live_db.
        db_match = (live_db is None) or (target_db == live_db)

        if hosts_match and db_match:
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
                inserted = self._insert_batch(list(current_batch))
                total_inserted += inserted
                current_batch.clear()

        # Insert any remaining documents
        if current_batch:
            inserted = self._insert_batch(list(current_batch))
            total_inserted += inserted

        return total_inserted

    def _insert_batch(self, batch: list) -> int:
        """
        Helper method to insert a batch of documents into the collection.
        Uses ordered=False to maximize insertion speed and prevent a single error from halting the entire batch.
        """
        from pymongo.errors import BulkWriteError
        try:
            res = self.target_collection.insert_many(batch, ordered=False)
            if hasattr(res, "inserted_ids") and isinstance(res.inserted_ids, list):
                return len(res.inserted_ids)
            return len(batch)
        except BulkWriteError as bwe:
            n_inserted = bwe.details.get("nInserted", 0)
            logger.warning(
                f"Gracefully handled bulk write error during ingestion. "
                f"Successfully inserted {n_inserted} of {len(batch)} documents in this batch. "
                f"Errors: {len(bwe.details.get('writeErrors', []))}"
            )
            return n_inserted
        except Exception as e:
            logger.error(f"Error occurred during batch insertion: {e}")
            raise
