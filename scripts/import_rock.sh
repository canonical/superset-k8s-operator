#!/bin/bash
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

# ==============================================================================
# Script: import_rock
# Description: Imports a .rock (OCI archive) into Docker and pushes to MicroK8s.
# Dependencies: skopeo, docker
# Usage: ./import_rock <path_to_rock> <image_name> <version> [--latest]
# ==============================================================================

# --- Configuration ---
# The address of your local MicroK8s registry.
# Standard MicroK8s registry runs on localhost:32000.
MICROK8S_REGISTRY="localhost:32000"

# --- Input Parsing ---
ROCK_FILEPATH="$1"
IMAGE_NAME="$2"
VERSION="$3"
OPTION="$4"

# Function to display usage
usage() {
    echo "Usage: import_rock <path_to_rock> <image_name> <version> [--latest]"
    echo "Example: import_rock ./ubuntu_22.04_amd64.rock my-ubuntu-image v1.0 --latest"
    exit 1
}

# --- Validation ---

# Check if arguments are provided
if [[ -z "$ROCK_FILEPATH" || -z "$IMAGE_NAME" || -z "$VERSION" ]]; then
    echo "Error: Missing arguments."
    usage
fi

# Check if file exists
if [[ ! -f "$ROCK_FILEPATH" ]]; then
    echo "Error: File not found at $ROCK_FILEPATH"
    exit 1
fi

# Convert relative path to absolute path for robustness and better logging
ROCK_FILEPATH=$(realpath "$ROCK_FILEPATH")

# Check for dependencies
if ! command -v skopeo &> /dev/null; then
    echo "Error: 'skopeo' is not installed. Please install it first."
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo "Error: 'docker' is not installed or not in PATH."
    exit 1
fi

# --- Execution ---

echo "--------------------------------------------------"
echo "Processing Rock: $ROCK_FILEPATH"
echo "Target Image:    $IMAGE_NAME:$VERSION"
echo "Target Registry: $MICROK8S_REGISTRY"
echo "--------------------------------------------------"

# 1. Copy from Rock (OCI Archive) to Docker Daemon
# We use 'docker-daemon:' transport so the image becomes available to the 'docker' CLI immediately.
echo -e "\n[1/3] Importing Rock to local Docker daemon with Skopeo..."
if sudo skopeo copy --insecure-policy "oci-archive:$ROCK_FILEPATH" "docker-daemon:$IMAGE_NAME:$VERSION"; then
    echo "Successfully imported into Docker."
else
    echo "Failed to import Rock file. Check if the rock format is valid."
    exit 1
fi

# 2. Tag the image for the MicroK8s registry
TARGET_TAG="$MICROK8S_REGISTRY/$IMAGE_NAME:$VERSION"
echo -e "\n[2/3] Tagging image as $TARGET_TAG..."
if docker tag "$IMAGE_NAME:$VERSION" "$TARGET_TAG"; then
    echo "Image tagged."
else
    echo "Failed to tag image."
    exit 1
fi

if [[ "$OPTION" == "--latest" ]]; then
    LATEST_TAG="$MICROK8S_REGISTRY/$IMAGE_NAME:latest"
    if docker tag "$IMAGE_NAME:$VERSION" "$LATEST_TAG"; then
        echo "Image tagged as latest."
    else
        echo "Failed to tag image as latest."
        exit 1
    fi
fi

# 3. Push the image to the MicroK8s registry
echo -e "\n[3/3] Pushing image to MicroK8s registry..."

PUSHED_TAGS=()

# Helper function for failure
fail_push() {
    echo "Failed to push image."
    echo "Tip: Ensure MicroK8s registry is enabled ('microk8s enable registry') and accessible."
    echo "Tip: You might need to configure Docker to accept insecure registries for localhost:32000."
    exit 1
}

# Push the specific version tag
if docker push "$TARGET_TAG"; then
    echo "Successfully pushed to $TARGET_TAG"
    PUSHED_TAGS+=("$TARGET_TAG")
else
    fail_push
fi

# Push the 'latest' tag if requested
if [[ "$OPTION" == "--latest" ]]; then
    LATEST_TAG="$MICROK8S_REGISTRY/$IMAGE_NAME:latest"
    if docker push "$LATEST_TAG"; then
        echo "Successfully pushed to $LATEST_TAG"
        PUSHED_TAGS+=("$LATEST_TAG")
    else
        fail_push
    fi
fi

echo ""
echo "Deployment complete. You can now use the image in MicroK8s:"
for tag in "${PUSHED_TAGS[@]}"; do
    echo "  image: $tag"
done
