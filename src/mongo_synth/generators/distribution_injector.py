import random
from typing import Any, Dict, List

class DistributionInjector:
    """
    Injects statically profiled values into generated datasets according to a configured probability distribution.
    """

    def __init__(self, schema: Dict[str, Any], profile: Dict[str, Dict[str, float]], seed: Any = None):
        """
        Initializes the injector.
        :param schema: The JSON schema of the documents.
        :param profile: A dictionary mapping field names to a dictionary of values and their probabilities.
                        e.g., {"status": {"active": 0.8, "inactive": 0.2}}
        :param seed: A master seed for random generator determinism.
        """
        self.schema = schema
        self.profile = profile
        
        if seed is not None:
            self._seed = seed
        else:
            try:
                from mongo_synth.config import generator_config
                self._seed = generator_config.get("generation.master_seed", 42)
            except Exception:
                self._seed = 42
                
        # Initialize an isolated random instance for deterministic behavior
        self._rng = random.Random(self._seed)

    def is_field_unique(self, field_schema: Dict[str, Any]) -> bool:
        """
        Determines if a field should be unique and thus excluded from distribution sampling.
        """
        if not field_schema:
            return False

        # In JSON Schema, uuid format or uniqueItems might imply uniqueness
        if field_schema.get("format") in ["uuid", "email"]:
            return True

        return False

    def inject_distribution(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """
        Modifies a document in-place to inject values according to the profile.
        """
        if not self.profile:
            return document

        properties = self.schema.get("properties", {})

        for field, dist in self.profile.items():
            if field in document:
                field_schema = properties.get(field, {})

                # Skip injection if the field requires uniqueness
                if self.is_field_unique(field_schema):
                    continue

                values = list(dist.keys())
                weights = list(dist.values())

                if values and weights:
                    chosen_value = self._rng.choices(values, weights=weights, k=1)[0]
                    document[field] = chosen_value

        return document

    def inject_batch(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Applies the distribution injection to a batch of documents.
        """
        for doc in batch:
            self.inject_distribution(doc)
        return batch
