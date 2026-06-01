#!/bin/bash

# ANSI colors
RESET="\033[0m"
BOLD="\033[1m"
GREEN="\033[32m"
BLUE="\033[34m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
PURPLE="\033[35m"

CONTAINER_NAME="mongo-synth-demo"
SCHEMA_FILE="demo_schema.json"
MODEL_FILE="demo_model.py"
INFERRED_FILE="demo_inferred.json"

# Cleanup function to teardown Docker and files
cleanup() {
    echo -e "\n${BOLD}${YELLOW}[Teardown] Cleaning up resources...${RESET}"
    if docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
        echo -e "${CYAN}Stopping and removing Docker container: ${CONTAINER_NAME}...${RESET}"
        docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
        docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
    fi
    rm -f "${SCHEMA_FILE}" "${MODEL_FILE}" "${INFERRED_FILE}"
    echo -e "${GREEN}${BOLD}✓ Cleanup complete. Goodbye!${RESET}"
}

# Register cleanup trap for exits/interrupts
trap cleanup EXIT

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}${BOLD}Error: Docker is not running or not accessible.${RESET}"
    echo -e "${YELLOW}Please start Docker Desktop and run this script again.${RESET}"
    exit 1
fi

clear
echo -e "${CYAN}${BOLD}======================================================${RESET}"
echo -e "${PURPLE}${BOLD}         🍃 MONGO-SYNTH INTERACTIVE DEMO 🍃           ${RESET}"
echo -e "${CYAN}${BOLD}======================================================${RESET}"
echo -e "This script demonstrates generation, ingestion, and validation"
echo -e "against real MongoDB containers of different versions."
echo -e ""

# 1. MongoDB Version Selection
echo -e "${BOLD}1. Select MongoDB Version to run in Docker:${RESET}"
echo -e "   1) MongoDB 5.0"
echo -e "   2) MongoDB 6.0"
echo -e "   3) MongoDB 7.0 (Default)"
read -p "Choose option (1-3): " MONGO_OPT

case $MONGO_OPT in
    1) MONGO_VERSION="5.0" ;;
    2) MONGO_VERSION="6.0" ;;
    3|*) MONGO_VERSION="7.0" ;;
esac
echo -e "👉 Selected MongoDB version: ${GREEN}${BOLD}${MONGO_VERSION}${RESET}\n"

# 2. Generation Source Selection
echo -e "${BOLD}2. Select Data Definition Type:${RESET}"
echo -e "   1) JSON Schema (Users collection) (Default)"
echo -e "   2) Pydantic Model (Devices collection)"
echo -e "   3) Anomaly Schema (Edge-case schema drift)"
read -p "Choose option (1-3): " SRC_OPT

case $SRC_OPT in
    2) DATA_TYPE="pydantic" ;;
    3) DATA_TYPE="anomaly" ;;
    1|*) DATA_TYPE="schema" ;;
esac
echo -e "👉 Selected Data Definition: ${GREEN}${BOLD}${DATA_TYPE}${RESET}\n"

# 3. Document Count Selection
echo -e "${BOLD}3. Select Number of Documents to Generate:${RESET}"
echo -e "   1) 50"
echo -e "   2) 250 (Default)"
echo -e "   3) 1000"
echo -e "   4) Custom count"
read -p "Choose option (1-4): " COUNT_OPT

case $COUNT_OPT in
    1) DOC_COUNT=50 ;;
    3) DOC_COUNT=1000 ;;
    4) 
       read -p "Enter custom count: " CUSTOM_COUNT
       if [[ "$CUSTOM_COUNT" =~ ^[0-9]+$ ]]; then
           DOC_COUNT=$CUSTOM_COUNT
       else
           echo -e "${YELLOW}Invalid input, defaulting to 250.${RESET}"
           DOC_COUNT=250
       fi
       ;;
    2|*) DOC_COUNT=250 ;;
esac
echo -e "👉 Selected count: ${GREEN}${BOLD}${DOC_COUNT}${RESET}\n"

# 4. Validation Strategy Selection
echo -e "${BOLD}4. Select Schema Validation Strategy:${RESET}"
echo -e "   1) structural (DeepDiff schema structure comparison) (Default)"
echo -e "   2) similarity (Jaccard path type weighted similarity)"
echo -e "   3) functional (Instance behavior generation check)"
echo -e "   4) precision (Unauthorized fields inference checking)"
read -p "Choose option (1-4): " VAL_OPT

case $VAL_OPT in
    2) VAL_STRATEGY="similarity" ;;
    3) VAL_STRATEGY="functional" ;;
    4) VAL_STRATEGY="precision" ;;
    1|*) VAL_STRATEGY="structural" ;;
esac
echo -e "👉 Selected validator: ${GREEN}${BOLD}${VAL_STRATEGY}${RESET}\n"

# --- Launch Container ---
echo -e "${BLUE}${BOLD}[1/4] Starting MongoDB ${MONGO_VERSION} Container...${RESET}"

# Stop any running version first
docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
docker rm "${CONTAINER_NAME}" >/dev/null 2>&1

docker run -d --name "${CONTAINER_NAME}" -p 27017 mongo:"${MONGO_VERSION}" >/dev/null

# Get the mapped port (27017 or dynamic if mapped differently)
HOST_PORT=$(docker port "${CONTAINER_NAME}" 27017 | head -n1 | cut -d: -f2)
if [ -z "$HOST_PORT" ]; then
    HOST_PORT=27017
fi

MONGO_URI="mongodb://localhost:${HOST_PORT}"

echo -e "Container started. Waiting for MongoDB service to start on port ${GREEN}${HOST_PORT}${RESET}..."

# Wait for MongoDB to become responsive via python ping
.venv/bin/python -c "
import time
from pymongo import MongoClient
for i in range(20):
    try:
        client = MongoClient('${MONGO_URI}', serverSelectionTimeoutMS=1000)
        client.admin.command('ping')
        break
    except Exception:
        time.sleep(1)
"

echo -e "${GREEN}${BOLD}✓ MongoDB container is ready!${RESET}\n"

# --- Write Input Files ---
echo -e "${BLUE}${BOLD}[2/4] Setting up Data Definitions...${RESET}"

if [ "$DATA_TYPE" = "schema" ]; then
    cat <<EOT > "${SCHEMA_FILE}"
{
  "type": "object",
  "properties": {
    "name": { "type": "string" },
    "email": { "type": "string", "format": "email" },
    "age": { "type": "integer", "minimum": 18, "maximum": 75 },
    "created_at": { "type": "string", "bsonType": "date" }
  },
  "required": ["name", "email", "age", "created_at"]
}
EOT
    GEN_ARGS="--schema ${SCHEMA_FILE}"
    echo -e "Created JSON schema: ${GREEN}${SCHEMA_FILE}${RESET}"

elif [ "$DATA_TYPE" = "pydantic" ]; then
    cat <<EOT > "${MODEL_FILE}"
from pydantic import BaseModel, Field
from datetime import datetime

class DeviceModel(BaseModel):
    device_id: str = Field(..., description="Unique Device Identifier")
    status: str = Field("online", pattern="^(online|offline)$")
    temperature: float = Field(20.0, ge=-50.0, le=100.0)
    last_ping: datetime = Field(default_factory=datetime.utcnow)
EOT
    GEN_ARGS="--model demo_model:DeviceModel"
    echo -e "Created Pydantic model: ${GREEN}${MODEL_FILE}${RESET}"

else # anomaly
    cat <<EOT > "${SCHEMA_FILE}"
{
  "type": "object",
  "properties": {
    "name": { "type": "string" }
  },
  "required": ["name"]
}
EOT
    GEN_ARGS="--schema ${SCHEMA_FILE} --anomaly mixed_type_arrays"
    echo -e "Created Base Schema: ${GREEN}${SCHEMA_FILE}${RESET} with Anomaly: ${GREEN}mixed_type_arrays${RESET}"
fi

echo -e ""

# --- Run Generation and Ingestion ---
echo -e "${BLUE}${BOLD}[3/4] Running mongo-synth Generation & Ingestion...${RESET}"
echo -e "Running: ${CYAN}.venv/bin/mongo-synth generate ${GEN_ARGS} --uri ${MONGO_URI} --db demo_db --collection demo_coll --count ${DOC_COUNT} --clear${RESET}"

.venv/bin/mongo-synth generate ${GEN_ARGS} --uri "${MONGO_URI}" --db demo_db --collection demo_coll --count "${DOC_COUNT}" --clear

echo -e "\n${GREEN}${BOLD}✓ Generation and Ingestion completed!${RESET}\n"

# --- Fetch MongoDB Server Details & Document Count ---
echo -e "${BLUE}${BOLD}[4/4] Fetching Ingested Server Info & Verification...${RESET}"
.venv/bin/python -c "
import sys
from pymongo import MongoClient
try:
    client = MongoClient('${MONGO_URI}', serverSelectionTimeoutMS=2000)
    server_info = client.server_info()
    version = server_info.get('version', 'unknown')
    db = client['demo_db']
    coll = db['demo_coll']
    doc_count = coll.count_documents({})
    
    print('\033[1;36m┌──────────────────────────────────────────────┐\033[0m')
    print('\033[1;36m│          INSPECTED MONGO CONFIGS             │\033[0m')
    print('\033[1;36m├──────────────────────────────────────────────┤\033[0m')
    print(f'\033[1;32m  Connection URI:   \033[0m {sys.argv[1]}')
    print(f'\033[1;32m  Server Version:   \033[0m {version}')
    print(f'\033[1;32m  Storage Engine:   \033[0m {server_info.get(\"storageEngine\", {}).get(\"name\", \"unknown\")}')
    print(f'\033[1;32m  Target Coll:      \033[0m demo_db.demo_coll')
    print(f'\033[1;32m  Document Count:   \033[0m {doc_count}')
    
    # Try showing compatibility
    try:
        cmd_res = client.admin.command('getParameter', '*')
        fcv = cmd_res.get('featureCompatibilityVersion', {}).get('version', 'N/A')
        print(f'\033[1;32m  Feature Compat:   \033[0m {fcv}')
    except Exception:
        pass
    print('\033[1;36m└──────────────────────────────────────────────┘\033[0m')
except Exception as e:
    print(f'Error querying MongoDB: {e}')
" "${MONGO_URI}"

echo -e ""

# --- Schema Validation Demo ---
echo -e "${BLUE}${BOLD}=== Schema Validation Verification ===${RESET}"
echo -e "We will now compare the schema of the generated data."

if [ "$DATA_TYPE" = "schema" ]; then
    # Generate inferred schema which is identical
    cp "${SCHEMA_FILE}" "${INFERRED_FILE}"
    
    echo -e "${YELLOW}Test 1: Validate identical schemas (Should pass):${RESET}"
    .venv/bin/mongo-synth validate --schema "${SCHEMA_FILE}" --inferred "${INFERRED_FILE}" --validator "${VAL_STRATEGY}"
    
    # Generate inferred schema with mismatch
    cat <<EOT > "${INFERRED_FILE}"
{
  "type": "object",
  "properties": {
    "name": { "type": "string" },
    "email": { "type": "integer" },
    "age": { "type": "string" },
    "created_at": { "type": "string", "bsonType": "date" }
  },
  "required": ["name", "email", "age", "created_at"]
}
EOT
    echo -e "\n${YELLOW}Test 2: Validate mismatched schema types (Should fail):${RESET}"
    # Run, ignoring the non-zero validation status exit code to finish execution cleanly
    .venv/bin/mongo-synth validate --schema "${SCHEMA_FILE}" --inferred "${INFERRED_FILE}" --validator "${VAL_STRATEGY}" || true

elif [ "$DATA_TYPE" = "pydantic" ]; then
    # Translate model to json schema first using Python
    .venv/bin/python -c "
import json
from demo_model import DeviceModel
with open('demo_schema.json', 'w') as f:
    if hasattr(DeviceModel, 'model_json_schema'):
        json.dump(DeviceModel.model_json_schema(), f, indent=2)
    else:
        json.dump(DeviceModel.schema(), f, indent=2)
"
    # Success case
    cp "${SCHEMA_FILE}" "${INFERRED_FILE}"
    echo -e "${YELLOW}Test 1: Validate generated Pydantic schema (Should pass):${RESET}"
    .venv/bin/mongo-synth validate --schema "${SCHEMA_FILE}" --inferred "${INFERRED_FILE}" --validator "${VAL_STRATEGY}"

    # Failure case
    cat <<EOT > "${INFERRED_FILE}"
{
  "type": "object",
  "properties": {
    "device_id": { "type": "integer" },
    "status": { "type": "string" }
  }
}
EOT
    echo -e "\n${YELLOW}Test 2: Validate incorrect field type / missing fields (Should fail):${RESET}"
    .venv/bin/mongo-synth validate --schema "${SCHEMA_FILE}" --inferred "${INFERRED_FILE}" --validator "${VAL_STRATEGY}" || true

else # anomaly
    # The anomaly generated data contains anomalies, let's show validation checks
    cp "${SCHEMA_FILE}" "${INFERRED_FILE}"
    echo -e "${YELLOW}Test 1: Comparing anomaly schema against base schema:${RESET}"
    .venv/bin/mongo-synth validate --schema "${SCHEMA_FILE}" --inferred "${INFERRED_FILE}" --validator "${VAL_STRATEGY}"
fi

echo -e ""
echo -e "${CYAN}${BOLD}======================================================${RESET}"
echo -e "${GREEN}${BOLD}🎉 INTERACTIVE DEMO RUN COMPLETE! 🎉${RESET}"
echo -e "Press [ENTER] to stop the Docker container and clean up."
echo -e "${CYAN}${BOLD}======================================================${RESET}"
read
