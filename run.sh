#!/bin/bash
cd "$(dirname "$0")"
LEDGER_ENV=development venv/bin/python app.py
