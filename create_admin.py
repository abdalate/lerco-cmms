"""Create the first local LERCO CMMS administrator account."""
from getpass import getpass

import database as db


def main():
    db.init_db()
    username = input("Administrator username [admin]: ").strip() or "admin"
    password = getpass("New administrator password (minimum 12 characters): ")
    confirm = getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    try:
        db.create_admin_account(username, password)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(f"Administrator account '{username}' created successfully.")


if __name__ == "__main__":
    main()
