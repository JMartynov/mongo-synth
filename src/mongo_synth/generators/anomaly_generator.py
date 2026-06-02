import random
from typing import Any, Dict, List
from faker import Faker
from mongo_synth.generators.base import BaseGenerator

class AnomalyGenerator(BaseGenerator):
    def __init__(self, blueprint: Dict[str, Any], documents_per_collection: int, seed: Any = None):
        super().__init__(blueprint, documents_per_collection, seed=seed)
        self.faker = Faker()
        # Keep anomaly_type reference
        self.anomaly_type = self.schema.get("anomaly_type")

    def generate_batch(self) -> List[Dict[str, Any]]:
        # Temporarily remove anomaly_type from schema to avoid any potential strategy issues,
        # then restore it.
        anomaly_type = self.schema.pop("anomaly_type", None)
        try:
            batch = super().generate_batch()
        finally:
            if anomaly_type is not None:
                self.schema["anomaly_type"] = anomaly_type

        mutated_batch = []
        for doc in batch:
            mutated_batch.append(self._inject_anomaly_to_doc(doc, anomaly_type))
        return mutated_batch

    def _inject_anomaly_to_doc(self, doc: Dict[str, Any], anomaly_type: str) -> Dict[str, Any]:
        if not isinstance(doc, dict):
            doc = {}

        if anomaly_type == "whitespace_keys":
            doc["   "] = self.faker.word()
            doc["\t"] = random.random()
            
        elif anomaly_type == "empty_embedded_docs":
            emptied = self._empty_first_embedded_doc(doc)
            if not emptied:
                doc["empty_doc"] = {}
                
        elif anomaly_type == "mixed_type_arrays":
            items = []
            for _ in range(random.randint(2, 5)):
                choice = random.random()
                if choice < 0.3:
                    items.append(self.faker.word())
                elif choice < 0.6:
                    items.append(random.randint(1, 100))
                else:
                    items.append({"key": self.faker.word()})
            
            injected = self._inject_mixed_array(doc, items)
            if not injected:
                doc["mixed_array"] = items
                
        elif anomaly_type == "extreme_nesting":
            if doc:
                key = random.choice(list(doc.keys()))
                val = doc[key]
                for _ in range(10):
                    val = {"nested": val}
                doc[key] = val
            else:
                doc["extreme_nested"] = {"nested": {"nested": {"nested": "value"}}}
                
        elif anomaly_type == "non_standard_chars":
            doc["user!@#$%"] = self.faker.name()
            doc["💩"] = random.choice([True, False])
            
        elif anomaly_type == "bson_type_impersonation":
            doc["fake_object_id"] = "507f1f77bcf86cd799439011"
            doc["fake_date"] = "2026-05-19T06:33:10Z"
            
        elif anomaly_type == "massive_payload":
            large_string = "a" * (1024 * 1024)
            inflated = False
            for k, v in doc.items():
                if isinstance(v, str):
                    doc[k] = large_string
                    inflated = True
                    break
            if not inflated:
                doc["blob"] = large_string
                
        elif anomaly_type == "deep_null_arrays":
            injected = False
            for k, v in doc.items():
                if isinstance(v, list):
                    doc[k] = [None, [None, [None]]]
                    injected = True
                    break
            if not injected:
                doc["null_array"] = [None, [None, [None]]]
                
        elif anomaly_type == "dot_notation_keys":
            doc["user.name"] = self.faker.name()
            doc["address.city"] = self.faker.city()
            
        return doc

    def _empty_first_embedded_doc(self, d: Any) -> bool:
        if isinstance(d, dict):
            for k, v in list(d.items()):
                if isinstance(v, dict):
                    if v:
                        d[k] = {}
                        return True
                    else:
                        if self._empty_first_embedded_doc(v):
                            return True
        return False

    def _inject_mixed_array(self, d: Any, items: List[Any]) -> bool:
        if isinstance(d, dict):
            for k, v in list(d.items()):
                if isinstance(v, list):
                    d[k] = items
                    return True
                elif isinstance(v, dict):
                    if self._inject_mixed_array(v, items):
                        return True
        return False
