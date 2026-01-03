# Shotdeck Scraper (Selenium)

Scrapes still thumbnails, full-resolution images, and metadata from Shotdeck **after logging in with your own account**.

> **Important**: Make sure you have permission to scrape and that your usage complies with Shotdeck's Terms of Service and applicable copyright/licensing rules.

## Features

- Logs in via Selenium (Chrome)
- Scrolls the browse gallery and opens each shot modal
- Extracts metadata fields from the modal
- Downloads the image via `requests` using Selenium session cookies
- Saves incremental progress to an Excel file (`.xlsx`)
- Saves images to a folder on disk

## Requirements

- Python 3.10+
- Google Chrome installed
- ChromeDriver available on your PATH (version-compatible with your Chrome)

## Install

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt

# Optional (installs the `shotdeck-scrape` command)
pip install -e .
```

## Configuration

Create a `.env` file (do **not** commit it). You can start from `.env.example`.

Required:
- `SHOTDECK_EMAIL`
- `SHOTDECK_PASSWORD`

Optional:
- `SHOTDECK_BROWSE_URL` (default: `https://shotdeck.com/browse/stills`)
- `SHOTDECK_OUTPUT_DIR` (default: current directory)

## Usage

Run as a module:

```bash
python -m shotdeck_scraper --max-shots 200 --out-xlsx results.xlsx --images-dir images
```

Or run the wrapper script:

```bash
python shotdeck_scraper.py --max-shots 200
```

CLI options (see `--help`):

- `--max-shots` : how many shots to scrape
- `--out-xlsx` : output Excel file name
- `--images-dir` : directory to save images
- `--headless` : run Chrome headless
- `--timeout` : page-load timeout
- `--retries` : retries for loading gallery
- `--batch-size` : save progress every N items
- `--scroll-pause` : pause between scrolls

## Output

- Excel file: columns include IDs, titles, URLs, and parsed metadata fields
- Images: saved to the configured images directory

## Development

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
ruff format .
```

## Disclaimer

This repository is provided for educational purposes. You are responsible for complying with the terms of the service you interact with and any relevant laws.
