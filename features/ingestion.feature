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
