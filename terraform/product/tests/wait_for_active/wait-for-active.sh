#!/bin/bash
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Polls `juju status` until APP_NAME's application status is "active" (or "error", or until TIMEOUT
# seconds elapse), then prints {"status": "<status>", "message": "<message>"} for the Terraform
# external data source. The message is what a failed assertion reports.

MODEL_UUID=$1
APP_NAME=$2
TIMEOUT=$3

export APP_NAME
LOG="/tmp/wait-for-active.$$.log"

if [ -z "$MODEL_UUID" ] || [ -z "$APP_NAME" ] || [ -z "$TIMEOUT" ]; then
	echo '{"status": "bad_arguments", "message": ""}'
	exit 0
fi

# Prints the status on the first line and the message on the second.
current_status() {
	juju status "$APP_NAME" --model "$MODEL_UUID" --format=json 2>>"$LOG" |
		jq -r '.applications[env.APP_NAME]["application-status"] | (.current // "unknown"), (.message // "")'
}

deadline=$(($(date +%s) + TIMEOUT))
status="unknown"
message=""
while [ "$(date +%s)" -lt "$deadline" ]; do
	{
		read -r status
		read -r message
	} < <(current_status)
	status=${status:-unknown}
	echo "[$(date)] $APP_NAME: $status: $message" >>"$LOG"
	case "$status" in
	active | error) break ;;
	esac
	sleep 20
done

jq -cn --arg status "$status" --arg message "$message" '{status: $status, message: $message}'
