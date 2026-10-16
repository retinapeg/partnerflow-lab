"""Start the API and the dashboard together, and stop both with Ctrl+C.

Usage:
    python run.py               start both and open the dashboard in a browser
    python run.py --no-browser  start both without opening a browser
    python run.py --reset       wipe recorded data first, then start
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser

import httpx

from partnerflow import config, db

API_URL = f"http://{config.API_HOST}:{config.API_PORT}"
DASHBOARD_URL = f"http://{config.API_HOST}:{config.DASHBOARD_PORT}"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((config.API_HOST, port)) == 0


def wait_until_up(url: str, process: subprocess.Popen, seconds: int = 60) -> bool:
    """Poll a URL until it answers 200, the process dies, or time runs out."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def stop(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def stop_requested(signum, frame):
    """Treat `kill <pid>` like Ctrl+C, so the two child processes are stopped too."""
    raise KeyboardInterrupt


def main() -> int:
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    parser = argparse.ArgumentParser(description="Start PartnerFlow Lab (API + dashboard).")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--reset", action="store_true", help="delete recorded data before starting")
    args = parser.parse_args()

    os.chdir(config.PROJECT_DIR)
    for name, port, variable in (("API", config.API_PORT, "PARTNERFLOW_API_PORT"),
                                 ("dashboard", config.DASHBOARD_PORT, "PARTNERFLOW_DASHBOARD_PORT")):
        if port_in_use(port):
            print(f"Port {port} is already in use, so the {name} cannot start.\n"
                  f"Is PartnerFlow Lab already running? If not, pick another port:\n"
                  f"    {variable}={port + 1} python run.py")
            return 1

    if args.reset:
        db.reset_data()
        print("Recorded data deleted. Release v1.0.0, no faults.")

    # Both child processes must agree on where the API is.
    env = {**os.environ, "PARTNERFLOW_API_URL": API_URL}
    processes: list[subprocess.Popen] = []
    try:
        print(f"Starting the API on {API_URL} ...")
        api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", config.API_HOST, "--port", str(config.API_PORT)], env=env)
        processes.append(api)
        if not wait_until_up(f"{API_URL}/health", api):
            print("The API did not start. Read the messages above for the reason.")
            return 1

        print(f"Starting the dashboard on {DASHBOARD_URL} ...")
        dashboard = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "dashboard.py",
             "--server.port", str(config.DASHBOARD_PORT),
             "--server.address", config.API_HOST,
             "--server.headless", "true",
             "--browser.gatherUsageStats", "false"], env=env)
        processes.append(dashboard)
        if not wait_until_up(f"{DASHBOARD_URL}/_stcore/health", dashboard):
            print("The dashboard did not start. Read the messages above for the reason.")
            return 1

        print("\n" + "=" * 62)
        print("  PartnerFlow Lab is running")
        print(f"  Dashboard : {DASHBOARD_URL}")
        print(f"  API docs  : {API_URL}/docs")
        print(f"  {config.BANNER}")
        print("  Press Ctrl+C to stop both.")
        print("=" * 62 + "\n")
        if not args.no_browser:
            webbrowser.open(DASHBOARD_URL)

        # Keep running until one of the two stops or the user presses Ctrl+C.
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        print("One of the two processes stopped, so the other is being stopped too.")
        return 1
    except KeyboardInterrupt:
        print("\nStopping ...")
        return 0
    finally:
        stop(processes)


if __name__ == "__main__":
    sys.exit(main())
