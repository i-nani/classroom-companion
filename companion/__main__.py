import argparse
import logging
import os
from datetime import datetime
from pathlib import Path
from contextlib import closing
from . import demo
from .jobs import process_reminders,run_worker
from .model import LanguageModel
from .store import connect,load_env,now,seed,transaction
from .web import serve

def main():
    load_env()
    parser=argparse.ArgumentParser(description="Classroom Companion")
    parser.add_argument("command",choices=["setup","web","worker","remind","demo","status","clock"])
    parser.add_argument("--at",help="Set the rehearsal clock to an ISO date/time with UTC offset.")
    parser.add_argument("--db",help="SQLite path. Use a separate database for offline replay.")
    parser.add_argument("--port",type=int,default=8080)
    args=parser.parse_args()
    if args.command=="clock":
        clock=os.getenv("DEMO_CLOCK_FILE")
        if not clock or not args.at:
            parser.error("Set DEMO_CLOCK_FILE in .env and pass --at with an explicit UTC offset.")
        instant=datetime.fromisoformat(args.at)
        if instant.tzinfo is None:
            parser.error("Include a UTC offset such as +05:30.")
        dest=Path(clock)
        dest.parent.mkdir(parents=True,exist_ok=True)
        pending=dest.with_suffix(".tmp")
        pending.write_text(str(int(instant.timestamp())))
        pending.replace(dest)
        print("Rehearsal clock:",instant.isoformat())
        return
    path=args.db or (".state/replay.sqlite3" if args.command=="demo" else os.getenv("CLASSROOM_DB",".state/classroom.sqlite3"))
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    if args.command=="web":
        serve(path,args.port)
        return
    with closing(connect(path)) as db:
        if args.command in ("setup","demo"):
            if args.command=="setup":
                with transaction(db):
                    accounts=seed(db,now())
            else:
                accounts=demo.print_replay(db)
            print("\nPrivate local test accounts. Keep these keys outside git.")
            for name,user in accounts.items():
                print(f"{name}  web key: {user['login']}")
                if args.command=="setup":
                    print(f"  Private Telegram command: /link {user['invite']}")
            print(f"\nStart the UI: python -m companion web --db {path}")
        elif args.command=="worker":
            run_worker(db,LanguageModel())
        elif args.command=="remind":
            print("Messages queued:",process_reminders(db,LanguageModel(),now()))
        elif args.command=="status":
            for row in db.execute("SELECT state,kind,count(*) n FROM outbox GROUP BY state,kind"):
                print(row["state"],row["kind"],row["n"])

if __name__=="__main__":
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc))

