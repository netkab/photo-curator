"""Run locally to view the pairing token and optionally allow your exact extension ID."""
import argparse
import re
from .config import BACKEND_DIR
from .security import local_token

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--extension-id", help="The ID shown at chrome://extensions")
    args = p.parse_args()
    if args.extension_id:
        if not re.fullmatch(r"[a-p]{32}", args.extension_id):
            p.error("Extension ID must be 32 lowercase letters a-p")
        path = BACKEND_DIR / ".env"
        lines = path.read_text().splitlines() if path.exists() else []
        lines = [line for line in lines if not line.startswith("PC_EXTENSION_IDS=")]
        path.write_text("\n".join(lines + [f"PC_EXTENSION_IDS={args.extension_id}"]) + "\n")
        print("Allowed extension saved. Restart the backend if it is already running.")
    print("Local pairing token (paste only into Photo Curator's Setup page or local UI):")
    print(local_token())

if __name__ == "__main__":
    main()
