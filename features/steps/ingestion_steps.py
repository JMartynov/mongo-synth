import os
import json
import jsonschema
from behave import given, when, then
from pymongo import MongoClient
from bson.objectid import ObjectId

from mongo_synth.generators.json_schema_generator import JsonSchemaGenerator
from mongo_synth.ingestion.data_ingester import DataIngester, SecurityError

# Helper to start containers dynamically
def start_mongo_container(context, version, auth=False):
    client = context.client
    container_name = f"mongo-synth-test-{version.replace('.', '_')}"
    if auth:
        container_name += "-auth"
        
    # Clean up existing container if any
    try:
        old = client.containers.get(container_name)
        old.stop(timeout=2)
        old.remove()
    except Exception:
        pass

    environment = {}
    if auth:
        environment = {
            "MONGO_INITDB_ROOT_USERNAME": "admin",
            "MONGO_INITDB_ROOT_PASSWORD": "password"
        }

    # Dynamic port binding to avoid collision
    container = client.containers.run(
        f"mongo:{version}",
        name=container_name,
        ports={'27017/tcp': None},
        environment=environment,
        detach=True
    )
    
    # Wait for the container to be ready and get port
    host_port = None
    retries = 20
    while retries > 0:
        container.reload()
        if container.status == "running":
            ports = container.attrs['NetworkSettings']['Ports']
            if '27017/tcp' in ports and ports['27017/tcp']:
                host_port = ports['27017/tcp'][0]['HostPort']
                break
        time.sleep(1)
        retries -= 1

    if not host_port:
        container.stop()
        container.remove()
        raise RuntimeError("Failed to map port for MongoDB container")

    uri = f"mongodb://localhost:{host_port}"
    if auth:
        uri = f"mongodb://admin:password@localhost:{host_port}"

    # Wait for MongoDB service to accept connections
    mongo_client = None
    connected = False
    retries = 30
    import time
    while retries > 0:
        try:
            mongo_client = MongoClient(uri, serverSelectionTimeoutMS=2000)
            mongo_client.admin.command("ping")
            connected = True
            break
        except Exception:
            time.sleep(1)
            retries -= 1

    if not connected:
        container.stop()
        container.remove()
        raise RuntimeError("MongoDB service failed to start within timeout")

    context.mongo_container = container
    context.mongo_uri = uri
    context.mongo_client = mongo_client

@given('a clean MongoDB container of version "{mongo_version}" is running')
def step_impl_clean_mongo(context, mongo_version):
    start_mongo_container(context, mongo_version, auth=False)

@given('a MongoDB container of version "{mongo_version}" with root authentication is running')
def step_impl_auth_mongo(context, mongo_version):
    start_mongo_container(context, mongo_version, auth=True)

@given('a schema file "{schema_path}" defining:')
def step_impl_schema_file(context, schema_path):
    # Ensure directory exists
    dir_name = os.path.dirname(schema_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
        
    schema_content = json.loads(context.text)
    with open(schema_path, "w") as f:
        json.dump(schema_content, f, indent=2)
        
    context.schema_path = schema_path
    context.schema_content = schema_content

@when('I run the mongo-synth tool to generate and ingest {count:d} documents')
def step_impl_run_ingest(context, count):
    # Setup Blueprint
    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": count
        }
    }
    
    # Generate batch documents using JsonSchemaGenerator
    generator = JsonSchemaGenerator(blueprint, count, seed=42)
    documents = generator.generate_batch()
    
    # Target collection
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    
    # Ingest data
    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)
    context.inserted_count = ingester.ingest(documents)

@when('I run the mongo-synth tool to generate and ingest {count:d} documents with credentials')
def step_impl_run_ingest_auth(context, count):
    # Setup Blueprint
    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": count
        }
    }
    
    generator = JsonSchemaGenerator(blueprint, count, seed=42)
    documents = generator.generate_batch()
    
    # Target collection in authenticated instance
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    
    ingester = DataIngester(collection, context.mongo_uri, batch_size=1000)
    context.inserted_count = ingester.ingest(documents)

@when('I attempt to run the mongo-synth tool with live URI set to the target container URI')
def step_impl_attempt_live_ingest(context):
    blueprint = {
        "schema": context.schema_content,
        "metadata": {
            "expected_document_count": 10
        }
    }
    
    generator = JsonSchemaGenerator(blueprint, 10, seed=42)
    documents = generator.generate_batch()
    
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    
    # Set target_uri and live_source_uri to the same connection string to trigger the lock
    context.security_error_raised = False
    try:
        ingester = DataIngester(
            target_collection=collection,
            target_uri=context.mongo_uri,
            live_source_uri=context.mongo_uri
        )
        ingester.ingest(documents)
    except SecurityError as e:
        context.security_error_raised = True
        context.security_error_message = str(e)

@then('the target collection should contain exactly {count:d} documents')
def step_impl_check_count(context, count):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    actual_count = collection.count_documents({})
    assert actual_count == count, f"Expected {count} documents, got {actual_count}"

@then('every document in the target collection must conform to the user schema')
def step_impl_verify_conformance(context):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    
    # Read the generated documents
    documents = list(collection.find({}))
    assert len(documents) > 0, "No documents found to validate"
    
    for doc in documents:
        # Remove MongoDB native metadata _id before JSON schema validation if not in schema properties
        doc_to_validate = dict(doc)
        if "_id" in doc_to_validate and "_id" not in context.schema_content.get("properties", {}):
            del doc_to_validate["_id"]
            
        # jsonschema doesn't know about ObjectId, ISODate, Decimal128, etc.
        # But this schema has normal fields (name, age), so we can check those.
        jsonschema.validate(instance=doc_to_validate, schema=context.schema_content)

@then('the documents in the target collection must have valid BSON ObjectIds')
def step_impl_verify_objectids(context):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    
    documents = list(collection.find({}))
    assert len(documents) > 0
    
    for doc in documents:
        # Check that "_id" is an instance of BSON ObjectId
        assert isinstance(doc["_id"], ObjectId), f"Expected ObjectId for _id, got {type(doc['_id'])}"

@then('the operation must fail with a Security Error')
def step_impl_verify_security_error(context):
    assert getattr(context, "security_error_raised", False), "Expected SecurityError, but none was raised"
    assert "Safety Lock Triggered" in context.security_error_message

@then('no documents should be inserted into the collection')
def step_impl_verify_empty(context):
    db = context.mongo_client["test_db"]
    collection = db["test_collection"]
    actual_count = collection.count_documents({})
    assert actual_count == 0, f"Expected 0 documents in target collection, got {actual_count}"
