"Helper function for Sentry interception."

def redact_params(event, hint):
    # Redact parameters from captured events
    if "exception" not in event:
        return event
    if "values" not in event["exception"]:
        return event

    for exc in event["exception"]["values"]:
        if "stacktrace" not in exc:
            continue
        for frame in exc["stacktrace"]["frames"]:
            # Filter out specific parameter keys
            if "vars" in frame:
                frame["vars"] = {key: "REDACTED" for key in frame["vars"]}

    return event
