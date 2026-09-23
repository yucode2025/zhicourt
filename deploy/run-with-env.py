#!/usr/bin/env python3
"""Load the external, protected dotenv into a subprocess (for Alembic/admin only)."""
import os
import sys
from dotenv import dotenv_values

if len(sys.argv) < 3:
    raise SystemExit("usage: run-with-env.py /etc/zhicourt/zhicourt.env command [args...]")
values = dotenv_values(sys.argv[1])
for key, value in values.items():
    if value is not None:
        os.environ[key] = value
os.execvp(sys.argv[2], sys.argv[2:])
