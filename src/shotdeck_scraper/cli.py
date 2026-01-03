"""Command-line interface for shotdeck_scraper."""

from __future__ import annotations

from .scraper import main as scraper_main


def main() -> int:
    """Entry point for `python -m shotdeck_scraper` and `shotdeck-scrape`."""
    scraper_main()
    return 0
