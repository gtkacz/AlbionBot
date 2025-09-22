#!/bin/sh

echo "Running dependency compilation..."
uv pip compile requirements.dev.in -o requirements.dev.txt
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "Compilation failed. Fix errors and try again." >&2
  exit $rc
fi

echo "Compilation successful. Syncing dependencies..."
uv pip sync requirements.dev.txt
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "Sync failed." >&2
  exit $rc
fi

echo "Dependencies successfully compiled and synced."
