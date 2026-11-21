"""Start a local Classroom Companion rehearsal or live session."""
import argparse
from contextlib import closing
from getpass import getpass
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import webbrowser
from wsgiref.simple_server import make_server

from companion import demo
from companion.jobs import DeliveryError, Telegram
from companion.store import connect, load_env, now, seed, transaction
from companion.web import Web


def live_credentials():
    load_env()
    for key, prompt in (("TELEGRAM_BOT_TOKEN", "BotFather token"),
                        ("MODEL_API_KEY", "Model API key")):
        if not os.environ.get(key):
            value = getpass(f"{prompt} (hidden, for this session): ").strip()
            if not value:
                raise ValueError(f"{key} is required for live mode.")
            os.environ[key] = value
    try:
        bot = Telegram().call("getMe", {})
    except DeliveryError as exc:
        raise ValueError(f"Telegram could not verify the bot (code {exc.code}). Check your token and connection.") from None
    print(f"Live bot: @{bot['username']}")


def start(mode, browser=True):
    os.chdir(Path(__file__).resolve().parent)
    if mode == "live":
        live_credentials()
        path = Path(os.getenv("CLASSROOM_DB", ".state/classroom.sqlite3"))
        app = Web(path)
    else:
        Path(".state").mkdir(exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix="practice-", dir=".state")) / "replay.sqlite3"
        app = Web(path, model=demo.FixtureModel())

    # Let the OS select a free loopback port, including on shared laptops.
    with make_server("127.0.0.1", 0, app) as server:
        with closing(connect(path)) as db:
            if mode == "offline":
                accounts = demo.print_replay(db)
            else:
                with transaction(db):
                    accounts = {} if db.execute("SELECT 1 FROM schools").fetchone() else seed(db, now())
        for name, account in accounts.items():
            print(f"{name}  web key: {account['login']}")
            if mode == "live":
                print(f"  Private Telegram command: /link {account['invite']}")
        if mode == "live" and not accounts:
            print("Resuming the existing class. Use a saved web key or send /web from a linked Telegram account.")
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Database: {path}\nDashboard: {url}\nKeep this window open. Press Ctrl+C to stop.", flush=True)
        worker = None
        try:
            if mode == "live":
                worker = subprocess.Popen([sys.executable, "-m", "companion", "worker", "--db", str(path)])
            if browser:
                try:
                    opened = webbrowser.open(url)
                except (OSError, webbrowser.Error):
                    opened = False
                if not opened:
                    print(f"Open this address in your browser: {url}", flush=True)
            server.timeout = 1
            while worker is None or worker.poll() is None:
                server.handle_request()
            raise RuntimeError(f"The Telegram worker stopped (exit {worker.returncode}).")
        finally:
            if worker is not None and worker.poll() is None:
                worker.terminate()
                worker.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["offline", "live"], nargs="?")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        mode = args.mode or input("Type offline or live [offline]: ").strip().lower() or "offline"
        if mode not in {"offline", "live"}:
            raise ValueError("Choose offline or live.")
        start(mode, not args.no_browser)
    except KeyboardInterrupt:
        print("\nStopped.")
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc))
