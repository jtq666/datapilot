import argparse
import getpass
import os

from argon2 import PasswordHasher

from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Create two local demo workspace accounts")
    parser.add_argument("--analyst", default="analyst")
    parser.add_argument("--reviewer", default="reviewer")
    args = parser.parse_args()
    if args.analyst == args.reviewer:
        parser.error("accounts must be different")
    passwords = [os.getenv(name) or getpass.getpass(f"{user} password: ") for name, user in (
        ("DEMO_ANALYST_PASSWORD", args.analyst), ("DEMO_REVIEWER_PASSWORD", args.reviewer)
    )]
    if any(len(p) < 8 for p in passwords):
        parser.error("demo passwords must have at least 8 characters")
    store = Store(os.environ["APP_DATABASE_URL"])
    store.setup()
    hasher = PasswordHasher()
    store.seed_user(args.analyst, hasher.hash(passwords[0]), True)
    store.seed_user(args.reviewer, hasher.hash(passwords[1]), False)
    print("Demo workspace accounts initialized")


if __name__ == "__main__":
    main()
