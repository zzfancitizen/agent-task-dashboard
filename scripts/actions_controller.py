"""Actions entry point; inputs come from the trusted runner environment."""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskboard.controller import main

if __name__ == "__main__":
    host = urlsplit(os.environ.get("GITHUB_SERVER_URL", "https://github.com")).hostname
    raise SystemExit(
        main(
            [
                "--repo",
                os.environ["GITHUB_REPOSITORY"],
                "--hostname",
                host,
                "--publish-pages",
            ]
        )
    )
