#!/bin/bash
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Polls `juju status` until APP_NAME's application status is "active" (or "error", or until TIMEOUT
# seconds elapse), then prints {"status": "<status>"} for the Terraform external data source.

MODEL_UUID=$1
APP_NAME=$2
TIMEOUT=$3

export APP_NAME
LOG="/tmp/wait-for-active.$$.log"

if [ -z "$MODEL_UUID" ] || [ -z "$APP_NAME" ] || [ -z "$TIMEOUT" ]; then
	echo '{"status": "bad_arguments"}'
	exit 0
fi

current_status() {
	juju status "$APP_NAME" --model "$MODEL_UUID" --format=json 2>>"$LOG" |
		jq -r '.applications[env.APP_NAME]["application-status"].current // "unknown"'
}

deadline=$(($(date +%s) + TIMEOUT))
status="unknown"
while [ "$(date +%s)" -lt "$deadline" ]; do
	status=$(current_status)
	echo "[$(date)] $APP_NAME: $status" >>"$LOG"
	case "$status" in
	active | error) break ;;
	esac
	sleep 20
done

echo '{"status": "'"$status"'"}'
