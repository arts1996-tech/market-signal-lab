"""Validate host exposure, then replace this process with Streamlit."""

from __future__ import annotations

import argparse
import os

from app.core.network import validate_streamlit_host_bind_address


APP_PATHS = {
    "lab": "app/dashboard/streamlit_app.py",
    "lite": "app/lite_dashboard/streamlit_app.py",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", choices=tuple(APP_PATHS), required=True)
    parser.add_argument("--port", choices=(8501, 8502), required=True, type=int)
    args = parser.parse_args()
    deployment_target = os.environ.get("DEPLOYMENT_TARGET", "mac")
    host_bind_address = os.environ.get("STREAMLIT_HOST_BIND_ADDRESS", "127.0.0.1")
    validate_streamlit_host_bind_address(
        host_bind_address,
        deployment_target=deployment_target,
    )
    os.execvp(
        "streamlit",
        [
            "streamlit",
            "run",
            APP_PATHS[args.app],
            "--server.address=0.0.0.0",
            f"--server.port={args.port}",
            f"--browser.serverAddress={host_bind_address}",
        ],
    )


if __name__ == "__main__":
    main()
