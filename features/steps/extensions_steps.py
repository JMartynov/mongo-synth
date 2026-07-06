import json
from datetime import datetime, timezone
from behave import given, when, then
from unittest.mock import patch
import re
from mongo_synth.generators.json_schema_generator import JsonSchemaGenerator
from mongo_synth.ingestion.data_ingester import DataIngester

@given('the schema has a percentileStats block:')
def step_impl_percentile_stats_block(context):
    context.percentile_stats = json.loads(context.text)

@given('the schema has multiple percentileStats blocks:')
def step_impl_multiple_percentile_stats_blocks(context):
    context.percentile_stats = json.loads(context.text)

@given('the blueprint has a distribution configuration:')
def step_impl_distribution_config(context):
    context.distribution_config = json.loads(context.text)

@when('I run the replicated mongo-synth tool to generate and ingest {count:d} documents')
def step_impl_replicated_ingest(context, count):
    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": count
        }
    }
    if hasattr(context, "percentile_stats"):
        blueprint["percentileStats"] = context.percentile_stats
    if hasattr(context, "distribution_config"):
        blueprint["metadata"]["distribution"] = context.distribution_config

    # Replicate pool at size 2
    with patch("mongo_synth.generators.base.min", return_value=2):
        generator = JsonSchemaGenerator(blueprint, count, seed=42)
        documents = generator.generate_batch()

    db = context.mongo_client["test_db"]
    collection = db["test_collection"]

    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)
    context.inserted_count = ingester.ingest(documents)

@when('I run the mongo-synth extensions tool to generate and ingest {count:d} documents')
def step_impl_run_ingest_extensions(context, count):
    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": count
        }
    }
    if hasattr(context, "percentile_stats"):
        blueprint["percentileStats"] = context.percentile_stats
    if hasattr(context, "distribution_config"):
        blueprint["metadata"]["distribution"] = context.distribution_config

    generator = JsonSchemaGenerator(blueprint, count, seed=42)
    documents = generator.generate_batch()

    db = context.mongo_client["test_db"]
    collection = db["test_collection"]

    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)
    context.inserted_count = ingester.ingest(documents)

@when('I ingest {count:d} documents in chunks of {chunk_size:d}')
def step_impl_ingest_in_chunks(context, count, chunk_size):
    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": chunk_size
        }
    }
    if hasattr(context, "percentile_stats"):
        blueprint["percentileStats"] = context.percentile_stats

    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)

    total_ingested = 0
    # Ingest in distinct chunk batches to test stream consistency
    for _ in range(count // chunk_size):
        generator = JsonSchemaGenerator(blueprint, chunk_size)
        documents = generator.generate_batch()
        total_ingested += ingester.ingest(documents)
    context.inserted_count = total_ingested

def parse_bdd_query(query_str):
    query = json.loads(query_str)
    for key, val in list(query.items()):
        if isinstance(val, dict):
            for op, op_val in list(val.items()):
                if isinstance(op_val, dict) and "$date" in op_val:
                    date_str = op_val["$date"]
                    if date_str.endswith("Z"):
                        date_str = date_str[:-1] + "+00:00"
                    val[op] = datetime.fromisoformat(date_str).replace(tzinfo=None)
    return query

@then('the target collection should contain exactly {count:d} documents matching {query_json}')
@then('the target collection should contain exactly {count:d} document matching {query_json}')
def step_impl_check_query_count(context, count, query_json):
    query = parse_bdd_query(query_json)
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    actual_count = collection.count_documents(query)
    assert actual_count == count, f"Expected {count} documents matching {query}, got {actual_count}"

@then('the target collection should contain between {low:d} and {high:d} documents matching {query_json}')
def step_impl_check_query_range(context, low, high, query_json):
    query = parse_bdd_query(query_json)
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    actual_count = collection.count_documents(query)
    assert low <= actual_count <= high, f"Expected between {low} and {high} documents matching {query}, got {actual_count}"

@then('all documents in the target collection should match {query_json}')
def step_impl_all_match_query(context, query_json):
    query = parse_bdd_query(query_json)
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    actual_count = collection.count_documents(query)
    total_count = collection.count_documents({})
    assert actual_count == total_count, f"Expected all {total_count} documents to match {query}, but only {actual_count} matched"

@then('every document in the target collection must have an integer "{field}" value')
def step_impl_check_integer_type(context, field):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({}))
    for doc in docs:
        val = doc.get(field)
        assert isinstance(val, int) and not isinstance(val, bool), f"Field {field} is not an integer: {val} (type {type(val)})"

@then('the target collection should contain some null "{field}" values')
def step_impl_check_some_nulls(context, field):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    null_count = collection.count_documents({field: None})
    assert null_count > 0, f"Expected some null values for {field}, got none"

@then('all null "{field}" values should remain null after scaling')
def step_impl_all_nulls_remain_null(context, field):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({field: None}))
    for doc in docs:
        assert doc[field] is None

@then('all documents in the target collection should have unique "{field}" values ending with "{suffix}"')
def step_impl_check_unique_values(context, field, suffix):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({}))
    values = [doc[field] for doc in docs]
    assert len(set(values)) == len(docs), f"Values for field {field} are not unique: {values}"
    for val in values:
        assert val.endswith(suffix), f"Value {val} does not end with {suffix}"

@then('every document in the target collection must have nested status "{status}" and array items matching allowed tokens')
def step_impl_check_nested_and_arrays(context, status):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({}))
    allowed = {"b8f9a2c3d4e5f678", "e5f678a2b8f9a2c3"}
    for doc in docs:
        assert doc["nested"]["status"] == status, f"Nested status was {doc['nested']['status']}, expected {status}"
        for token in doc["tokens"]:
            assert token in allowed, f"Token {token} not in allowed set {allowed}"

@then('all documents in the target collection should have a "{field}" field of length at least {length:d}')
def step_impl_check_min_length(context, field, length):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({}))
    for doc in docs:
        val = doc.get(field)
        assert isinstance(val, str) and len(val) >= length, f"Field {field} value {val} does not have length >= {length}"

@then('all documents in the target collection should have "{field}" matching exactly "{value}"')
def step_impl_check_exact_value(context, field, value):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({}))
    for doc in docs:
        assert doc.get(field) == value, f"Field {field} value was {doc.get(field)}, expected {value}"

@then('all documents in the target collection must have status "{status}" and role "{role}"')
def step_impl_check_status_and_role(context, status, role):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({}))
    for doc in docs:
        assert doc.get("status") == status, f"status was {doc.get('status')}, expected {status}"
        assert doc.get("role") == role, f"role was {doc.get('role')}, expected {role}"

@then('the database should contain only hashed enum values and no plaintext data leak')
def step_impl_check_no_data_leak(context):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    docs = list(collection.find({}))
    # Assert that all token fields are exactly the HMAC hashes, matching the allowed hex pattern
    hex_pattern = re.compile(r"^[0-9a-fA-F]{16}$")
    for doc in docs:
        token = doc.get("token")
        assert hex_pattern.match(token), f"Potential data leak or invalid token: {token}"
