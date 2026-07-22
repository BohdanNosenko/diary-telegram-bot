import argparse
from vlog_journal.config import load_config
import sys

def main():
    parser = argparse.ArgumentParser(description="Vlog Journal CLI")
    parser.add_argument("--config", type=str, default="config.toml", help="Path to configuration TOML file")
    
    args = parser.parse_args()
    
    try:
        config = load_config(args.config)
        print("vlog-journal CLI started. Configuration loaded successfully.")
        print(f"Vault: {config.app.vault_name} at {config.app.vault_path}")
        print(f"Pipelines registered: {len(config.pipelines.video_diary)} steps in video_diary")
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
