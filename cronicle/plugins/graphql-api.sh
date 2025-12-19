#!/bin/bash
# Cronicle Plugin: GraphQL API
# Generic plugin for making GraphQL mutations/queries
#
# Cronicle Job Parameters (JSON):
#   api_url       - GraphQL endpoint URL
#   api_key       - API key for authentication
#   graphql_query - The GraphQL query/mutation to execute
#   description   - Optional description for logging

set -euo pipefail

# Read JSON input from Cronicle
JSON_INPUT=$(cat)

# Parse JSON string value using awk (handles escaped quotes properly)
get_param() {
    local key="$1"
    local default="${2:-}"
    local value

    value=$(echo "$JSON_INPUT" | awk -v key="$key" '
    {
        # Find "key": " pattern
        pattern = "\"" key "\"[[:space:]]*:[[:space:]]*\""
        if (match($0, pattern)) {
            start = RSTART + RLENGTH
            rest = substr($0, start)

            # Parse JSON string, handling escape sequences
            result = ""
            i = 1
            while (i <= length(rest)) {
                c = substr(rest, i, 1)
                if (c == "\\") {
                    i++
                    nc = substr(rest, i, 1)
                    if (nc == "n") result = result "\n"
                    else if (nc == "r") result = result "\r"
                    else if (nc == "t") result = result "\t"
                    else result = result nc
                } else if (c == "\"") {
                    break
                } else {
                    result = result c
                }
                i++
            }
            print result
            exit
        }
    }')

    if [[ -n "$value" ]]; then
        echo "$value"
    else
        echo "$default"
    fi
}

API_URL=$(get_param "api_url" "")
API_KEY=$(get_param "api_key" "")
GRAPHQL_QUERY=$(get_param "graphql_query" "")
DESCRIPTION=$(get_param "description" "GraphQL API call")

# Allow env var overrides for sensitive values
API_KEY="${GRAPHQL_API_KEY:-$API_KEY}"

# Validate required parameters
if [[ -z "$API_URL" ]]; then
    echo "ERROR: api_url is required"
    echo '{"complete":1,"code":1,"description":"api_url is required"}'
    exit 1
fi

if [[ -z "$API_KEY" ]]; then
    echo "ERROR: API key is required (set api_key param or GRAPHQL_API_KEY env var)"
    echo '{"complete":1,"code":1,"description":"API key is required"}'
    exit 1
fi

if [[ -z "$GRAPHQL_QUERY" ]]; then
    echo "ERROR: graphql_query is required"
    echo '{"complete":1,"code":1,"description":"graphql_query is required"}'
    exit 1
fi

echo "Starting GraphQL API call..."
echo "Description: $DESCRIPTION"
echo "API URL: $API_URL"
echo ""

echo '{"progress":0.3,"description":"Sending GraphQL request..."}'

# Create JSON payload
TEMP_FILE=$(mktemp)
trap 'rm -f "$TEMP_FILE"' EXIT

# Escape special characters for JSON and build payload
escape_json() {
    local str="$1"
    str="${str//\\/\\\\}"      # Escape backslashes first
    str="${str//\"/\\\"}"      # Escape double quotes
    str="${str//$'\n'/\\n}"    # Escape newlines
    str="${str//$'\r'/\\r}"    # Escape carriage returns
    str="${str//$'\t'/\\t}"    # Escape tabs
    echo "$str"
}

ESCAPED_QUERY=$(escape_json "$GRAPHQL_QUERY")
echo "{\"query\": \"$ESCAPED_QUERY\"}" > "$TEMP_FILE"

echo "Payload:"
cat "$TEMP_FILE"
echo ""

# Make the API call
RESPONSE=$(curl -s -w "\n%{http_code}" \
    -H "Content-Type: application/json" \
    -H "ApiKey: $API_KEY" \
    -d @"$TEMP_FILE" \
    "$API_URL")

# Extract HTTP status code (last line) and body
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$RESPONSE" | sed '$d')

echo "HTTP Status: $HTTP_CODE"
echo "Response: $RESPONSE_BODY"
echo ""

if [[ "$HTTP_CODE" -ge 200 ]] && [[ "$HTTP_CODE" -lt 300 ]]; then
    # Check for GraphQL errors in response (simple pattern match, no jq)
    if echo "$RESPONSE_BODY" | grep -q '"errors"[[:space:]]*:'; then
        # Try to extract the first error message
        ERRORS=$(echo "$RESPONSE_BODY" | grep -o '"message"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"\([^"]*\)".*/\1/')
        if [[ -z "$ERRORS" ]]; then
            ERRORS="Unknown GraphQL error"
        fi
        echo "GraphQL Error: $ERRORS"
        echo '{"complete":1,"code":1,"description":"GraphQL error: '"$ERRORS"'"}'
        exit 1
    fi

    echo "API call completed successfully!"
    echo '{"complete":1,"code":0,"description":"'"$DESCRIPTION"' completed (HTTP '"$HTTP_CODE"')"}'
else
    echo "ERROR: API call failed with HTTP status $HTTP_CODE"
    echo '{"complete":1,"code":1,"description":"'"$DESCRIPTION"' failed (HTTP '"$HTTP_CODE"')"}'
    exit 1
fi
