#!/usr/bin/bash
set -e

echo "Fixing AOT Group Control..."

cd ~/.aot-group-control
mkdir -p releases/worker-v2026.08.14.02
cd releases/worker-v2026.08.14.02

FILES=(
    "relay.py"
    "runtime.py"
    "controller.py"
    "updater.py"
    "e2e.py"
    "worker_smoke_test.py"
    "worker-release-schema.json"
    "msetup_registration.py"
    "legacy_relay_bridge.py"
)

for file in "${FILES[@]}"; do
    echo "Downloading $file..."
    curl -fsSLO "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/$file"
done

cd ../..
ln -sfn releases/worker-v2026.08.14.02 releases/current
ln -sfn releases/worker-v2026.08.14.02 releases/last_good

echo "Restarting worker..."
python "$HOME/.aot-group-control/bootstrap_launcher.py" start

echo "Done!"
