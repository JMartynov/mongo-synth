from typing import Dict, List, Any, Optional
from pymongo.collection import Collection

class DataSampler:
    """
    A statistical profiler utility that analyzes a live MongoDB collection's
    data distribution and cardinality.
    """

    def __init__(self, collection: Collection, sample_size: Optional[int] = None, max_cardinality: Optional[int] = None):
        """
        Initializes the DataSampler with a PyMongo collection.

        Args:
            collection (Collection): The PyMongo collection to profile.
            sample_size (int, optional): Sample size overrides.
            max_cardinality (int, optional): Maximum cardinality overrides.
        """
        self.collection = collection
        
        if sample_size is not None:
            self.sample_size = sample_size
        else:
            try:
                from mongo_synth.config import generator_config
                self.sample_size = generator_config.get("mongodb.sample_size", 100000)
            except Exception:
                self.sample_size = 100000
                
        if max_cardinality is not None:
            self.max_cardinality = max_cardinality
        else:
            try:
                from mongo_synth.config import generator_config
                self.max_cardinality = generator_config.get("mongodb.max_cardinality", 50)
            except Exception:
                self.max_cardinality = 50

    def profile_fields(self, fields: List[str]) -> Dict[str, Dict[str, float]]:
        """
        Calculates the data distribution for specified fields using an aggregation pipeline.

        Args:
            fields (List[str]): List of field names to profile.

        Returns:
            Dict[str, Dict[str, float]]: A dictionary mapping each field to its distribution,
                                         where distribution is a dictionary of value -> probability.
        """
        if not fields:
            return {}

        pipeline: List[Dict[str, Any]] = [
            {"$sample": {"size": self.sample_size}}
        ]

        facet_stage = {}
        for field in fields:
            facet_stage[field] = [
                {"$match": {field: {"$exists": True, "$ne": None}}},
                {"$sortByCount": f"${field}"},
                {"$limit": self.max_cardinality}
            ]

        pipeline.append({"$facet": facet_stage})

        cursor = self.collection.aggregate(pipeline)
        result = list(cursor)

        if not result:
            return {field: {} for field in fields}

        facet_result = result[0]
        distributions: Dict[str, Dict[str, float]] = {}

        for field in fields:
            field_data = facet_result.get(field, [])
            if not field_data:
                distributions[field] = {}
                continue

            total_count = 0
            distribution = {}
            for item in field_data:
                val = item["_id"]
                count = item["count"]
                total_count += count
                if isinstance(val, (dict, list)):
                    val = str(val)
                distribution[val] = count

            if total_count == 0:
                distributions[field] = {}
                continue

            inv_total = 1.0 / total_count
            for k in distribution:
                distribution[k] *= inv_total

            distributions[field] = distribution

        return distributions
