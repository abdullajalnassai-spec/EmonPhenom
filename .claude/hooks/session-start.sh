#!/bin/bash
# Prepare an EmonPhenom session: install the package and its test tooling, then
# install the specialist roster (which this repository deliberately does not
# vendor -- see README, "The specialist roster").
#
# Idempotent: safe to run on startup, resume, clear and compact alike.
set -euo pipefail

# Local machines are already set up by whoever set them up; this is for
# Claude Code on the web, where the container starts empty.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  echo "session-start: not a remote session, nothing to do"
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

echo "session-start: installing emonphenom and test tooling"
python3 -m pip install --quiet --disable-pip-version-check -e ".[dev]"

# The roster needs the network and is optional -- nothing in the package
# imports it and the test suite passes without it. Never fail the session
# over it.
echo "session-start: installing the specialist roster"
if python3 -m emonphenom.cli agents sync; then
  :
else
  echo "session-start: roster sync failed (offline?) -- continuing without it."
  echo "session-start: run 'munder agents sync' later to install it."
fi

echo "session-start: ready -- try 'munder demo' or 'python -m pytest'"
