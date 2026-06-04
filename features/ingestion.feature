Feature: MongoDB Synthetic Data Ingestion
  As a database engineer
  I want to generate and ingest synthetic datasets into real MongoDB collections
  So that I can populate test databases deterministically across different MongoDB versions and configurations

  Scenario Outline: Ingesting data into standalone Mongo instances of different versions
    Given a clean MongoDB container of version "<mongo_version>" is running
    And a schema file "features/schemas/user_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "name": { "type": "string" },
          "age": { "type": "integer", "minimum": 18, "maximum": 80 }
        },
        "required": ["name", "age"]
      }
      """
    When I run the mongo-synth tool to generate and ingest 200 documents
    Then the target collection should contain exactly 200 documents
    And every document in the target collection must conform to the user schema

    Examples:
      | mongo_version |
      | 5.0           |
      | 6.0           |
      | 7.0           |

  Scenario: Ingesting into MongoDB with Authentication Enabled
    Given a MongoDB container of version "7.0" with root authentication is running
    And a schema file "features/schemas/device_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "_id": { "type": "string", "bsonType": "objectId" },
          "status": { "type": "string", "enum": ["online", "offline"] }
        },
        "required": ["_id", "status"]
      }
      """
    When I run the mongo-synth tool to generate and ingest 50 documents with credentials
    Then the target collection should contain exactly 50 documents
    And the documents in the target collection must have valid BSON ObjectIds

  Scenario: Safety Lock Blocks Ingestion on Live Database
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/user_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "name": { "type": "string" }
        },
        "required": ["name"]
      }
      """
    When I attempt to run the mongo-synth tool with live URI set to the target container URI
    Then the operation must fail with a Security Error
    And no documents should be inserted into the collection

  Scenario: Verify dry-run mode runs client-side schema validation without writing to MongoDB
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/user_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "name": { "type": "string" }
        },
        "required": ["name"]
      }
      """
    When I run the mongo-synth tool with dry-run mode for 10 documents
    Then the operation must succeed executing only client-side validation
    And no documents should be inserted into the collection

  Scenario: Verify ordered insertion halts and raises error on duplicate keys
    Given a clean MongoDB container of version "7.0" is running
    And the target collection has a unique index on "email"
    And a schema file "features/schemas/collision_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "email": { "type": "string" }
        },
        "required": ["email"]
      }
      """
    When I attempt to bulk insert with ordered set to true a list of duplicate documents:
      """
      [
        {"email": "colliding@example.com"},
        {"email": "colliding@example.com"}
      ]
      """
    Then the ingestion operation must fail with a bulk write error
    And the target collection should contain exactly 1 document

  Scenario: Verify parallel ingestion inserts documents correctly into MongoDB collection using multiple workers
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/user_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "name": { "type": "string" }
        },
        "required": ["name"]
      }
      """
    When I run the mongo-synth tool to generate and ingest 300 documents with 3 workers
    Then the target collection should contain exactly 300 documents
    And every document in the target collection must conform to the user schema

  Scenario: Verify parallel dry-run validation with multiple workers succeeds without writing to MongoDB
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/user_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "name": { "type": "string" }
        },
        "required": ["name"]
      }
      """
    When I run the mongo-synth tool with dry-run mode and 2 workers for 20 documents
    Then the operation must succeed executing only client-side validation
    And no documents should be inserted into the collection

