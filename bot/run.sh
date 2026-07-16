#!/bin/bash
# Activate the virtual environment and run the Shopee video downloader bot
cd "$(dirname "$0")"
source .venv/bin/activate
python main.py
