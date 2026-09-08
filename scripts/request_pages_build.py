"""Explicit build avoids relying on a GITHUB_TOKEN push to trigger Pages."""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskboard.github import GitHub
from taskboard.protocol import ProtocolError

if __name__ == "__main__":
    api = GitHub(
        os.environ["GITHUB_REPOSITORY"],
        urlsplit(os.environ.get("GITHUB_SERVER_URL", "https://github.com")).hostname,
    )
    try:
        api.request("POST", f"repos/{api.repo}/pages/builds", {})
        print(
            "Pages build requested. Check repository Pages settings for the published URL."
        )
    except ProtocolError as error:
        print(
            f"{error.code}: Configure Pages from gh-pages / (root), then retry. {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)
