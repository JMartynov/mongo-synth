import os
import json
from behave import given, when, then
from pymongo import MongoClient
from mongo_synth.generators.json_schema_generator import JsonSchemaGenerator
from mongo_synth.ingestion.data_ingester import DataIngester

@when('I run the mongo-synth tool to generate and ingest {count:d} sensitive documents with verifier output "{verifier_path}" and run-id "{run_id}"')
def step_impl_run_sensitive_ingest(context, count, verifier_path, run_id):
    # Ensure cleanup of any previous verifier file
    if os.path.exists(verifier_path):
        os.unlink(verifier_path)

    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": count,
            "run_id": run_id
        }
    }
    generator = JsonSchemaGenerator(blueprint, count, seed=42)
    documents = generator.generate_batch()

    # Save verifiers
    with open(verifier_path, "w") as f:
        json.dump(generator.sensitive_tracker.verifiers, f, indent=2)

    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    collection.delete_many({})

    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)
    context.inserted_count = ingester.ingest(documents)
    context.verifier_path = verifier_path

@then('the generated email and api_key values must contain "{run_id}" prefix')
def step_impl_verify_prefixes(context, run_id):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    documents = list(collection.find({}))
    assert len(documents) > 0
    for doc in documents:
        assert run_id in doc["user_email"]
        assert run_id in doc["api_token"]

@then('the verifier file "{verifier_path}" must contain exactly {expected_count:d} verifier entries')
def step_impl_verify_file_entries(context, verifier_path, expected_count):
    assert os.path.exists(verifier_path)
    with open(verifier_path, "r") as f:
        data = json.load(f)
    assert len(data) == expected_count, f"Expected {expected_count} entries, got {len(data)}"

@when('I run the mongo-synth tool with auto-inject, run-id "{run_id}", and verifier output "{verifier_path}" for {count:d} documents')
def step_impl_run_auto_inject(context, run_id, verifier_path, count):
    if os.path.exists(verifier_path):
        os.unlink(verifier_path)

    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": count,
            "inject_sensitive": True,
            "run_id": run_id
        }
    }
    generator = JsonSchemaGenerator(blueprint, count, seed=42)
    documents = generator.generate_batch()

    with open(verifier_path, "w") as f:
        json.dump(generator.sensitive_tracker.verifiers, f, indent=2)

    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    collection.delete_many({})

    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)
    context.inserted_count = ingester.ingest(documents)

@then('every document in the target collection must contain auto-injected PII structures')
def step_impl_verify_auto_injected_pii(context):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    documents = list(collection.find({}))
    assert len(documents) > 0
    for doc in documents:
        assert "personal_info" in doc
        assert "billing" in doc
        assert "credentials" in doc
        assert "email" in doc["personal_info"]
        assert "password" in doc["credentials"]

@given('the target collection has a unique index on "{field_name}"')
def step_impl_unique_index(context, field_name):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    collection.delete_many({})
    collection.create_index(field_name, unique=True)

@when('I attempt to bulk insert a list of documents with duplicate values:')
def step_impl_bulk_insert_duplicates(context):
    documents = json.loads(context.text)
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    
    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)
    # This should succeed and not raise BulkWriteError
    context.inserted_count = ingester.ingest(documents)

@then('the ingestion should succeed without raising an error')
def step_impl_ingest_succeeds(context):
    pass

@given('a verifier list file "{file_path}" containing:')
def step_impl_create_verifier_file(context, file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as f:
        f.write(context.text)
    context.verifier_path = file_path

@given('a log file "{file_path}" with content:')
def step_impl_create_log_file(context, file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as f:
        f.write(context.text)
    context.log_path = file_path

@when('I run the mongo-synth verify-leak tool with verifier file "{verifier_file}" and target "{target_file}"')
def step_impl_run_verify_leak_tool(context, verifier_file, target_file):
    import subprocess
    cmd = [
        ".venv/bin/python", "-m", "mongo_synth.cli", "verify-leak",
        "--verifier-file", verifier_file,
        "--target", target_file
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    context.cli_returncode = res.returncode
    context.cli_stdout = res.stdout
    context.cli_stderr = res.stderr

@then('the verification operation should fail detecting a leak')
def step_impl_check_verification_fail(context):
    assert context.cli_returncode != 0, f"Expected non-zero returncode but got {context.cli_returncode}. Stdout: {context.cli_stdout}, Stderr: {context.cli_stderr}"
    assert "LEAK DETECTED" in context.cli_stdout, f"Expected LEAK DETECTED but got: {context.cli_stdout}"

@then('the verification operation should succeed detecting no leaks')
def step_impl_check_verification_success(context):
    assert context.cli_returncode == 0, f"Expected zero returncode but got {context.cli_returncode}. Stdout: {context.cli_stdout}, Stderr: {context.cli_stderr}"
    assert "SECURE" in context.cli_stdout, f"Expected SECURE but got: {context.cli_stdout}"

