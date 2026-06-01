import random
from typing import Any, Dict, List
from faker import Faker
from mongo_synth.generators.base import BaseGenerator

class AnomalyGenerator(BaseGenerator):
    def __init__(self, blueprint: Dict[str, Any], documents_per_collection: int, seed: Any = None):
        super().__init__(blueprint, documents_per_collection, seed=seed)
        self.faker = Faker()
        self.anomaly_type = self.schema.get("anomaly_type")

    def generate_batch(self) -> List[Dict[str, Any]]:
        batch = []
        count = self.metadata.get("expected_document_count", self.documents_per_collection)
        for _ in range(count):
            doc = self._generate_single_doc()
            batch.append(doc)
        return batch

    def _generate_single_doc(self) -> Dict[str, Any]:
        if self.anomaly_type == "whitespace_keys":
            return {
                "   ": self.faker.word(),
                "\t": random.random()
            }
        elif self.anomaly_type == "empty_embedded_docs":
            return {
                "empty_doc": {}
            }
        elif self.anomaly_type == "mixed_type_arrays":
            items = []
            for _ in range(random.randint(2, 5)):
                choice = random.random()
                if choice < 0.3:
                    items.append(self.faker.word())
                elif choice < 0.6:
                    items.append(random.randint(1, 100))
                else:
                    items.append({"key": self.faker.word()})
            return {"mixed_array": items}
        elif self.anomaly_type == "extreme_nesting":
            doc = self.faker.word()
            for _ in range(10): 
                doc = {"nested": doc}
            return doc
        elif self.anomaly_type == "non_standard_chars":
            return {
                "user!@#$%": self.faker.name(),
                "💩": random.choice([True, False])
            }
        elif self.anomaly_type == "bson_type_impersonation":
            return {
                "fake_object_id": "507f1f77bcf86cd799439011",
                "fake_date": "2026-05-19T06:33:10Z"
            }
        elif self.anomaly_type == "massive_payload":
            large_string = "a" * (1024 * 1024) 
            return {
                "blob": large_string
            }
        elif self.anomaly_type == "deep_null_arrays":
            return {
                "null_array": [None, [None, [None]]]
            }
        elif self.anomaly_type == "dot_notation_keys":
            return {
                "user.name": self.faker.name(),
                "address.city": self.faker.city()
            }
        else:
            return {"generic": "data"}
