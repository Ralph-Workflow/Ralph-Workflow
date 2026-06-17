#!/bin/sh
echo "MOCK_AGY_ARTIFACT_DIR=$MOCK_AGY_ARTIFACT_DIR"
exec tests/_support/mock_agy.sh "$@"
