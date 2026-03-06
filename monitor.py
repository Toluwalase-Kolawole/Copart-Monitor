name: Copart Monitor

on:
  schedule:
    # Runs every 2 hours
    - cron: "0 */2 * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Dry run (no notifications, no state update)"
        required: false
        default: "false"
        type: boolean
      test_telegram:
        description: "Send a test Telegram message only"
        required: false
        default: "false"
        type: boolean

permissions:
  contents: write   # Needed to commit state.json back to the repo

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      # ------------------------------------------------------------------ #
      # 1. Checkout (with full history so we can push back)
      # ------------------------------------------------------------------ #
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 1
          token: ${{ secrets.GITHUB_TOKEN }}

      # ------------------------------------------------------------------ #
      # 2. Set up Python
      # ------------------------------------------------------------------ #
      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      # ------------------------------------------------------------------ #
      # 3. Install Python dependencies
      # ------------------------------------------------------------------ #
      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt

      # ------------------------------------------------------------------ #
      # 4. Install Playwright browsers (Chromium only for speed)
      # ------------------------------------------------------------------ #
      - name: Install Playwright Chromium
        run: playwright install chromium --with-deps

      # ------------------------------------------------------------------ #
      # 5. Test Telegram (only when manually triggered with test_telegram=true)
      # ------------------------------------------------------------------ #
      - name: Test Telegram connection
        if: ${{ github.event.inputs.test_telegram == 'true' }}
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python monitor.py --test-telegram

      # ------------------------------------------------------------------ #
      # 6. Run the monitor
      # ------------------------------------------------------------------ #
      - name: Run Copart monitor
        if: ${{ github.event.inputs.test_telegram != 'true' }}
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          COPART_MAKES: ${{ vars.COPART_MAKES }}
          COPART_DAMAGE_TYPES: ${{ vars.COPART_DAMAGE_TYPES }}
          COPART_MAX_PAGES: ${{ vars.COPART_MAX_PAGES || '3' }}
          COPART_YEAR_MIN: ${{ vars.COPART_YEAR_MIN }}
          COPART_YEAR_MAX: ${{ vars.COPART_YEAR_MAX }}
        run: |
          if [ "${{ github.event.inputs.dry_run }}" = "true" ]; then
            python monitor.py --dry-run
          else
            python monitor.py
          fi

      # ------------------------------------------------------------------ #
      # 7. Commit updated state.json back to the repo
      # ------------------------------------------------------------------ #
      - name: Commit state.json
        if: ${{ github.event.inputs.dry_run != 'true' && github.event.inputs.test_telegram != 'true' }}
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add state.json
          # Only commit if state.json actually changed
          git diff --staged --quiet || git commit -m "chore: update state.json [skip ci]"
          git push
