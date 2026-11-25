#!/usr/bin/env python3
"""Restore desktop idle settings saved before Cammy feedback testing."""
import os
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
allowed = {('org.gnome.desktop.session', 'idle-delay'),
           ('org.gnome.settings-daemon.plugins.power', 'sleep-inactive-ac-type')}
subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True, stdout=subprocess.DEVNULL)
rows = json.loads((root / 'private/feedback-test/sleep-settings-before.json').read_text())
for row in rows:
    if (row['schema'], row['key']) not in allowed:
        raise ValueError('Unexpected setting in saved baseline')
for row in rows:
    subprocess.run(['gsettings', 'set', row['schema'], row['key'], row['value']], check=True)
print('Original desktop idle settings restored.')
