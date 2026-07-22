import argparse
import asyncio
import sys
import structlog

from vlog_journal.bot.app import start_bot
from vlog_journal.config import load_config
from vlog_journal.logging import setup_logging

logger = structlog.get_logger(__name__)

def main() -> None:
    parser = argparse.ArgumentParser(description="Vlog Journal CLI")
    parser.add_argument("--config", type=str, default="config.toml", help="Path to configuration TOML file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    setup_logging(debug=args.debug)

    try:
        config = load_config(args.config)
        logger.info("Configuration loaded successfully", vault=config.app.vault_name, path=str(config.app.vault_path))
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(start_bot(config))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")
        print("\nBot stopped successfully.")

if __name__ == "__main__":
    main()
