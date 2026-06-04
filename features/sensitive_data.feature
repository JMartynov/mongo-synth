Feature: MongoDB Synthetic Sensitive Data Generation & Robust Ingestion
  As a security and database engineer
  I want to generate synthetic sensitive data, salt them with run IDs, and collect leak verifiers
  So that I can test database leak scenarios, compliance detection, and handle unique constraint collisions gracefully

  Scenario: Generate sensitive data using schema annotations and output verifier list
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/sensitive_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "user_email": { "type": "string", "sensitiveType": "email" },
          "api_token": { "type": "string", "sensitiveType": "api_key" }
        },
        "required": ["user_email", "api_token"]
      }
      """
    When I run the mongo-synth tool to generate and ingest 5 sensitive documents with verifier output "features/schemas/verifiers.json" and run-id "dev_run"
    Then the target collection should contain exactly 5 documents
    And the generated email and api_key values must contain "dev_run" prefix
    And the verifier file "features/schemas/verifiers.json" must contain exactly 10 verifier entries

  Scenario: Auto-inject PII fields using CLI flag
    Given a clean MongoDB container of version "7.0" is running
    And a schema file "features/schemas/basic_schema.json" defining:
      """
      {
        "type": "object",
        "properties": {
          "item": { "type": "string" }
        },
        "required": ["item"]
      }
      """
    When I run the mongo-synth tool with auto-inject, run-id "auto_canary", and verifier output "features/schemas/verifiers_auto.json" for 3 documents
    Then the target collection should contain exactly 3 documents
    And every document in the target collection must contain auto-injected PII structures
    And the verifier file "features/schemas/verifiers_auto.json" must contain exactly 24 verifier entries

  Scenario: Bulk write resilience with duplicate key violations
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
    When I attempt to bulk insert a list of documents with duplicate values:
      """
      [
        {"email": "colliding@example.com"},
        {"email": "colliding@example.com"},
        {"email": "unique@example.com"}
      ]
      """
    Then the ingestion should succeed without raising an error
    And the target collection should contain exactly 2 documents


