#!/bin/sh
# Hourly on the deploy host: resolve finished put.io transfers so History grows
# a Download button, and email whoever asked for each one.
#
# Held under flock because an hourly job plus one slow put.io morning is
# otherwise two runs racing over the same rows — both seeing notified_at unset,
# both sending. flock -n exits non-zero rather than queueing, which is right:
# the next hour will pick the work up anyway.
#
# Install with:
#   15 * * * * /opt/stacks/dragnet/deploy/notify-ready.sh 2>&1 | logger -t dragnet-notify
#
# Run once by hand BEFORE adding that line:
#   docker compose exec web python manage.py notify_ready --catch-up
# otherwise the first tick mails everyone about downloads they already have.
set -eu

cd /opt/stacks/dragnet
exec flock -n /tmp/dragnet-notify.lock \
    docker compose exec -T web python manage.py notify_ready
