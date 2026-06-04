import secrets
import string
from typing import Any, Dict, List, Optional
from faker import Faker

class SensitiveDataTracker:
    def __init__(self, run_id: Optional[str] = None, seed: Optional[Any] = None, locale: Optional[str] = None):
        import logging
        try:
            self.faker = Faker(locale) if locale else Faker()
        except Exception:
            logging.getLogger(__name__).warning(f"Invalid sensitive locale '{locale}' specified. Falling back to default locale.")
            self.faker = Faker()
        if seed is not None:
            self.faker.seed_instance(seed)
        self.verifiers: List[Dict[str, str]] = []
        self.run_id = run_id

    def clear(self):
        self.verifiers.clear()

    def track(self, data_type: str, value: str):
        self.verifiers.append({
            "type": data_type,
            "value": value
        })

    def generate_value(self, sensitive_type: str) -> str:
        """Generates a value for a given sensitive type using libraries, not hardcoding."""
        prefix = f"{self.run_id}_" if self.run_id else ""
        val = ""
        if sensitive_type == "name":
            val = prefix + self.faker.name()
        elif sensitive_type == "email":
            local_part, domain = self.faker.email().split("@", 1)
            val = f"{prefix}{local_part}@{domain}"
        elif sensitive_type == "phone":
            val = self.faker.phone_number()
        elif sensitive_type == "ssn":
            val = self.faker.ssn()
        elif sensitive_type == "credit_card":
            val = self.faker.credit_card_number()
        elif sensitive_type == "address":
            val = self.faker.address().replace("\n", ", ")
        elif sensitive_type == "password":
            # Generate a cryptographically secure password
            chars = string.ascii_letters + string.digits + "!@#$%^&*"
            val = prefix + "".join(secrets.choice(chars) for _ in range(16))
        elif sensitive_type == "api_key":
            # Generate a realistic AWS-style or generic API key
            val = f"key_live_{prefix}{secrets.token_hex(20)}"
        else:
            val = prefix + self.faker.word()

        self.track(sensitive_type, val)
        return val

    def auto_inject(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Automatically appends sensitive fields to a document."""
        if not isinstance(doc, dict):
            return doc
        
        doc["personal_info"] = {
            "full_name": self.generate_value("name"),
            "email": self.generate_value("email"),
            "phone": self.generate_value("phone"),
            "ssn": self.generate_value("ssn"),
            "address": self.generate_value("address")
        }
        doc["billing"] = {
            "credit_card": self.generate_value("credit_card")
        }
        doc["credentials"] = {
            "password": self.generate_value("password"),
            "api_key": self.generate_value("api_key")
        }
        return doc
