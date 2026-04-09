#!/usr/bin/env bash
# Build the MCP image and push to ECR (same Dockerfile as local Docker / CDK asset).
# Prerequisites: aws CLI, docker, ECR repository created (e.g. by CDK or console).
#
# Usage:
#   export AWS_REGION=us-east-1
#   export AWS_ACCOUNT_ID=123456789012
#   export ECR_REPO=easydeploy-ai-mcp
#   ./scripts/push_mcp_image_ecr.sh [optional-git-sha-tag]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
ECR_REPO="${ECR_REPO:-easydeploy-ai-mcp}"
EXTRA_TAG="${1:-}"

REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${ECR_REPO}"

echo "==> Logging in to ECR"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> docker build"
docker build -t "${ECR_REPO}:local" .

docker tag "${ECR_REPO}:local" "${IMAGE_URI}:latest"
if [[ -n "$EXTRA_TAG" ]]; then
  docker tag "${ECR_REPO}:local" "${IMAGE_URI}:${EXTRA_TAG}"
fi

echo "==> docker push"
docker push "${IMAGE_URI}:latest"
if [[ -n "$EXTRA_TAG" ]]; then
  docker push "${IMAGE_URI}:${EXTRA_TAG}"
fi

echo "Pushed ${IMAGE_URI}:latest${EXTRA_TAG:+ and ${IMAGE_URI}:${EXTRA_TAG}}"
