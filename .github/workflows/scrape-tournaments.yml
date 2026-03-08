name: Scrape Chess Tournaments

on:
  schedule:
    - cron: '0 11 * * 0'  # Every Sunday at 11:00 UTC (6am EST)
  workflow_dispatch:       # Manual trigger

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 30    # Allow up to 30 min for full 50-state pagination
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: pip install requests beautifulsoup4 anthropic supabase
      
      - name: Run tournament scraper
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python scrape_tournaments.py
