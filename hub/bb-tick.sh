#!/bin/sh
# BB captures both streams. Keep provider startup notices out of successful silent ticks.
case "${WORK_HUB_TICK:-}" in
    health-check|reply-check|dispatch) ;;
    *) echo "Unknown Work Hub schedule" >&2; exit 2 ;;
esac
if output=$("${BB_CLI:-bb}" work-hub "$WORK_HUB_TICK" 2>/dev/null); then
    if [ -n "$output" ]; then printf '%s\n' "$output"; fi
else
    echo "Work Hub $WORK_HUB_TICK failed. Check plugin status and source coverage." >&2
    exit 1
fi
