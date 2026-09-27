"""Generate backdated commits in a repo to fill the GitHub contribution graph."""

import argparse
import random
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

MESSAGES = [
    "update notes",
    "refactor",
    "tidy up",
    "fix typo",
    "small improvements",
    "update docs",
    "wip",
    "cleanup",
    "adjust config",
    "minor tweaks",
]

EPILOG = """\
GitHub only counts a commit when:
  - it is on the default branch of a repo that is not a fork, and
  - the author email is a verified email on your GitHub account.
The graph can take a little while to update after a push.
"""


def build_schedule(
    start: date,
    end: date,
    rng: random.Random,
    max_per_day: int = 4,
    skip_chance: float = 0.3,
    weekend_factor: float = 0.3,
    hours: tuple[int, int] = (9, 18),
) -> list[datetime]:
    """Return sorted, local-timezone commit timestamps between start and end (inclusive)."""
    if end < start:
        raise ValueError("end date is before start date")
    if max_per_day < 1:
        raise ValueError("max_per_day must be at least 1")
    first_hour, last_hour = hours
    if not 0 <= first_hour < last_hour <= 24:
        raise ValueError("hours must satisfy 0 <= start < end <= 24")

    window = (last_hour - first_hour) * 3600
    schedule: list[datetime] = []

    day = start
    while day <= end:
        # Weekends keep only weekend_factor of the weekday activity chance.
        active_chance = 1 - skip_chance
        if day.weekday() >= 5:
            active_chance *= weekend_factor
        if rng.random() < active_chance:
            base = datetime.combine(day, time(first_hour))
            for _ in range(rng.randint(1, max_per_day)):
                # astimezone() on a naive datetime applies the local offset for that date (DST-aware).
                stamp = base + timedelta(seconds=rng.randrange(window))
                schedule.append(stamp.astimezone())
        day += timedelta(days=1)

    return sorted(schedule)


class Progress:
    """Single-line progress bar on stderr; verbose lines are printed above it."""

    def __init__(self, total: int, verbose: bool = False, width: int = 30) -> None:
        self.total = total
        self.verbose = verbose
        self.width = width
        self.done = 0
        self.enabled = sys.stderr.isatty()

    def step(self, detail: str) -> None:
        self.done += 1
        if self.verbose:
            self._clear()
            print(f"[{self.done}/{self.total}] {detail}", file=sys.stderr)
        self._draw()

    def finish(self) -> None:
        if self.enabled:
            print(file=sys.stderr)

    def _clear(self) -> None:
        if self.enabled:
            print("\r\033[K", end="", file=sys.stderr)

    def _draw(self) -> None:
        if not self.enabled:
            return
        ratio = self.done / self.total if self.total else 1
        filled = int(ratio * self.width)
        bar = "#" * filled + "-" * (self.width - filled)
        print(f"\r[{bar}] {self.done}/{self.total} ({ratio:.0%})", end="", file=sys.stderr, flush=True)


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def ensure_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        git(repo, "init", "-b", "main")
        print(f"Initialised new repo at {repo}")


def data(payload: str) -> bytes:
    """A fast-import `data` block with an exact byte count."""
    raw = payload.encode()
    return b"data %d\n%s\n" % (len(raw), raw)


def write_commits(
    repo: Path, file: Path, schedule: list[datetime], rng: random.Random, verbose: bool = False
) -> None:
    """Stream every commit into one `git fast-import` process instead of running git per commit."""
    ensure_repo(repo)

    name = git(repo, "config", "user.name", check=False)
    email = git(repo, "config", "user.email", check=False)
    if not name or not email:
        raise SystemExit("git user.name and user.email must be set before running.")
    branch = git(repo, "symbolic-ref", "--quiet", "HEAD", check=False)
    if not branch:
        raise SystemExit("HEAD is detached; check out a branch first.")
    parent = git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    print(f"Committing to {branch} as {email} (must be a verified email on your GitHub account).")

    importer = subprocess.Popen(
        ["git", "-C", str(repo), "fast-import", "--quiet", "--done"],
        stdin=subprocess.PIPE,
    )
    assert importer.stdin is not None
    progress = Progress(len(schedule), verbose)
    try:
        for i, stamp in enumerate(schedule):
            message = rng.choice(MESSAGES)
            ident = f"{name} <{email}> {int(stamp.timestamp())} {stamp:%z}"
            chunk = b"commit %s\n" % branch.encode()
            chunk += f"author {ident}\ncommitter {ident}\n".encode()
            chunk += data(message)
            if i == 0 and parent:
                chunk += f"from {parent}\n".encode()
            # The dummy file only holds the latest timestamp so each commit stays a one-line change.
            chunk += f"M 100644 inline {file.as_posix()}\n".encode()
            chunk += data(f"{stamp.isoformat()}\n")
            importer.stdin.write(chunk)
            progress.step(f"{stamp:%a %Y-%m-%d %H:%M:%S %z} {message}")
        importer.stdin.write(b"done\n")
        importer.stdin.close()
    finally:
        progress.finish()
        if importer.wait() != 0:
            raise SystemExit("git fast-import failed; the branch was not updated.")

    # fast-import only moves the branch ref; sync the dummy file's index and working copy to it.
    if schedule:
        git(repo, "checkout", "HEAD", "--", file.as_posix())


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_hours(value: str) -> tuple[int, int]:
    first, _, last = value.partition("-")
    return int(first), int(last)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", type=parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, default=date.today(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--repo", type=Path, default=Path("."), help="target repo (default: current directory)")
    parser.add_argument(
        "--file", type=Path, default=Path("activity/history"), help="dummy file, relative to the repo"
    )
    parser.add_argument("--max-per-day", type=int, default=28)
    parser.add_argument("--skip-chance", type=float, default=0.3, help="chance a weekday has no commits")
    parser.add_argument("--weekend-factor", type=float, default=0.3, help="weekend activity relative to weekdays")
    parser.add_argument("--hours", type=parse_hours, default=(9, 18), help="commit time window, e.g. 9-18")
    parser.add_argument("--seed", type=int, help="seed for reproducible output")
    parser.add_argument("-v", "--verbose", action="store_true", help="print each commit as it is created")
    parser.add_argument("--dry-run", action="store_true", help="print the schedule without committing")
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    try:
        schedule = build_schedule(
            args.start,
            args.end,
            rng,
            max_per_day=args.max_per_day,
            skip_chance=args.skip_chance,
            weekend_factor=args.weekend_factor,
            hours=args.hours,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.dry_run:
        for stamp in schedule:
            print(stamp.strftime("%a %Y-%m-%d %H:%M:%S %z"))
    else:
        write_commits(args.repo.expanduser().resolve(), args.file, schedule, rng, args.verbose)

    active_days = len({stamp.date() for stamp in schedule})
    verb = "Planned" if args.dry_run else "Created"
    print(f"{verb} {len(schedule)} commits across {active_days} days.")


if __name__ == "__main__":
    main()
