Feature: Deep Verification of Multi-Mode Support Extensions
  As a database engineer
  I want to deeply verify all invariants of percentile-aware range generation and hashed enum seeding
  So that I can ensure the synthetic data matches all requirements and constraints on a live MongoDB database

  Scenario: Verify piecewise linear range generation exact selectivity and query parity
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/price_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "price": { "type": "number" }
        },
        "required": ["price"]
      }
      """
    And the schema has a percentileStats block:
      """
      {
        "fieldName": "price",
        "boundaryValue": 100.0,
        "lowerPercentile": 0.3
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 100 documents
    Then the target collection should contain exactly 30 documents matching {"price": {"$lt": 100.0}}
    And the target collection should contain exactly 70 documents matching {"price": {"$gte": 100.0}}

  Scenario: Verify skewed boundary value retention
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/score_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "score": { "type": "number" }
        },
        "required": ["score"]
      }
      """
    And the schema has a percentileStats block:
      """
      {
        "fieldName": "score",
        "boundaryValue": 50.0,
        "lowerPercentile": 0.9
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 100 documents
    Then the target collection should contain exactly 90 documents matching {"score": {"$lt": 50.0}}

  Scenario: Verify date range interpolation selectivity
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/date_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "created_at": { "type": "string", "bsonType": "date" }
        },
        "required": ["created_at"]
      }
      """
    And the schema has a percentileStats block:
      """
      {
        "fieldName": "created_at",
        "boundaryValue": "2026-01-01T00:00:00Z",
        "lowerPercentile": 0.5
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 100 documents
    Then the target collection should contain exactly 50 documents matching {"created_at": {"$lt": {"$date": "2026-01-01T00:00:00Z"}}}

  Scenario: Verify float/integer coercion
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/integer_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "quantity": { "type": "integer" }
        },
        "required": ["quantity"]
      }
      """
    And the schema has a percentileStats block:
      """
      {
        "fieldName": "quantity",
        "boundaryValue": 50,
        "lowerPercentile": 0.3
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 50 documents
    Then every document in the target collection must have an integer "quantity" value

  Scenario: Verify extreme boundary percentiles
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/extreme_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "val1": { "type": "number" },
          "val2": { "type": "number" }
        },
        "required": ["val1", "val2"]
      }
      """
    And the schema has multiple percentileStats blocks:
      """
      [
        {
          "fieldName": "val1",
          "boundaryValue": 200.0,
          "lowerPercentile": 1.0
        },
        {
          "fieldName": "val2",
          "boundaryValue": 5.0,
          "lowerPercentile": 0.0
        }
      ]
      """
    When I run the mongo-synth extensions tool to generate and ingest 50 documents
    Then all documents in the target collection should match {"val1": {"$lte": 200.0}}
    And all documents in the target collection should match {"val2": {"$gte": 5.0}}

  Scenario: Verify null value preservation during interpolation
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/nulls_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "val": {
            "anyOf": [
              { "type": "number" },
              { "type": "null" }
            ]
          }
        },
        "required": ["val"]
      }
      """
    And the schema has a percentileStats block:
      """
      {
        "fieldName": "val",
        "boundaryValue": 100.0,
        "lowerPercentile": 0.5
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 200 documents
    Then the target collection should contain some null "val" values
    And all null "val" values should remain null after scaling

  Scenario: Verify streaming chunk consistency
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/chunk_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "val": { "type": "number" }
        },
        "required": ["val"]
      }
      """
    And the schema has a percentileStats block:
      """
      {
        "fieldName": "val",
        "boundaryValue": 100.0,
        "lowerPercentile": 0.3
      }
      """
    When I ingest 15000 documents in chunks of 5000
    Then the target collection should contain exactly 4500 documents matching {"val": {"$lt": 100.0}}

  Scenario: Verify point query selectivity parity for hashed enums
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/enum_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "status": {
            "type": "string",
            "enumValues": ["b8f9a2c3d4e5f678", "e5f678a2b8f9a2c3"]
          }
        },
        "required": ["status"]
      }
      """
    And the blueprint has a distribution configuration:
      """
      {
        "status": {
          "b8f9a2c3d4e5f678": 0.9,
          "e5f678a2b8f9a2c3": 0.1
        }
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 100 documents
    Then the target collection should contain between 80 and 98 documents matching {"status": "b8f9a2c3d4e5f678"}

  Scenario: Verify unique index collision prevention for hashed enums
    Given a clean MongoDB container of version "7.0" is running
    And the target collection has a unique index on "token"
    And a schema file "features/schemas/unique_enum_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "token": {
            "type": "string",
            "enumValues": ["b8f9a2c3d4e5f678"],
            "unique": true
          }
        },
        "required": ["token"]
      }
      """
    When I run the replicated mongo-synth tool to generate and ingest 5 documents
    Then the target collection should contain exactly 5 documents
    And all documents in the target collection should have unique "token" values ending with "b8f9a2c3d4e5f678"

  Scenario: Verify nested object and array enum seeding
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/nested_enum_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "nested": {
            "type": "object",
            "properties": {
              "status": {
                "type": "string",
                "enumValues": ["b8f9a2c3d4e5f678"]
              }
            },
            "required": ["status"]
          },
          "tokens": {
            "type": "array",
            "items": {
              "type": "string",
              "enumValues": ["b8f9a2c3d4e5f678", "e5f678a2b8f9a2c3"]
            }
          }
        },
        "required": ["nested", "tokens"]
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 10 documents
    Then every document in the target collection must have nested status "b8f9a2c3d4e5f678" and array items matching allowed tokens

  Scenario: Verify empty token fallback to random strings
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/fallback_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "token": {
            "type": "string",
            "enumValues": [],
            "minLength": 5
          }
        },
        "required": ["token"]
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 10 documents
    Then all documents in the target collection should have a "token" field of length at least 5

  Scenario: Verify case sensitivity integrity and character set constraints
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/case_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "status": {
            "type": "string",
            "enumValues": ["B8F9A2C3D4E5F678"]
          }
        },
        "required": ["status"]
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 5 documents
    Then all documents in the target collection should have "status" matching exactly "B8F9A2C3D4E5F678"

  Scenario: Verify multiple enum fields mapping independently
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/multi_enum_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "status": {
            "type": "string",
            "enumValues": ["b8f9a2c3d4e5f678"]
          },
          "role": {
            "type": "string",
            "enumValues": ["e5f678a2b8f9a2c3"]
          }
        },
        "required": ["status", "role"]
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 5 documents
    Then all documents in the target collection must have status "b8f9a2c3d4e5f678" and role "e5f678a2b8f9a2c3"

  Scenario: Verify zero data leak of plaintext production strings
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/no_leak_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "token": {
            "type": "string",
            "enumValues": ["b8f9a2c3d4e5f678"]
          }
        },
        "required": ["token"]
      }
      """
    When I run the mongo-synth extensions tool to generate and ingest 5 documents
    Then the database should contain only hashed enum values and no plaintext data leak
