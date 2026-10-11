"""Admin CLI. Run on the server; there is no admin API, so no admin credential can leak over HTTP.

    python -m safety_eval.service.admin create-clinic harbourline
    python -m safety_eval.service.admin create-key --clinic harbourline     # prints the key once
    python -m safety_eval.service.admin revoke-key sek_AbCd1234
    python -m safety_eval.service.admin spend                    # Claude API spend against SPEND_LIMIT_USD
    python -m safety_eval.service.admin release-reservations     # after checking the real bill
    python -m safety_eval.service.admin create-reviewer --clinic harbourline --email a@b.org --role reviewer
    python -m safety_eval.service.admin disable-reviewer --clinic harbourline --email a@b.org
"""

import argparse
import getpass
import secrets
import sys

import sqlalchemy as sa

from safety_eval.clinic import Clinic, ClinicConfigError, available

from .auth import new_key, revoke
from .db import api_keys, clinics, make_engine, utcnow
from .settings import Settings
from .ui_db import ROLES, create_reviewer, disable_reviewer


def create_clinic(engine, clinic_id):
    try:
        clinic = Clinic.load(clinic_id)
    except (ClinicConfigError, OSError):
        raise SystemExit(f"no clinic {clinic_id!r} in clinics/ (have: {', '.join(available())})")
    with engine.begin() as conn:
        if conn.execute(sa.select(clinics.c.id).where(clinics.c.id == clinic.id)).first():
            raise SystemExit(f"clinic {clinic.id!r} already exists")
        conn.execute(clinics.insert().values(id=clinic.id, name=clinic.name, created_at=utcnow()))
    return clinic.id


def create_key(engine, clinic_id):
    """Returns the new key. This is the only moment it exists in plain text."""
    key, prefix, key_hash = new_key()
    with engine.begin() as conn:
        if not conn.execute(sa.select(clinics.c.id).where(clinics.c.id == clinic_id)).first():
            raise SystemExit(f"clinic {clinic_id!r} is not in the database: run create-clinic first")
        conn.execute(api_keys.insert().values(clinic_id=clinic_id, key_hash=key_hash, prefix=prefix,
                                              created_at=utcnow()))
    return key


def add_reviewer(engine, clinic_id, email, role, password):
    with engine.begin() as conn:
        if not conn.execute(sa.select(clinics.c.id).where(clinics.c.id == clinic_id)).first():
            raise SystemExit(f"clinic {clinic_id!r} is not in the database: run create-clinic first")
        try:
            return create_reviewer(conn, clinic_id, email, role, password)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except sa.exc.IntegrityError:
            raise SystemExit(f"{email!r} is already a reviewer for {clinic_id!r}") from None


def print_spend(engine, limit):
    """Spend is the operator's business, not a clinic's, so it is shown here and not over the API."""
    from .budget import Budget
    s = Budget(engine, limit or float("inf")).summary()
    print(f"limit {s['limit_usd']:.2f} USD: settled {s['settled_usd']:.2f}, reserved {s['reserved_usd']:.2f} "
          f"({s['open_reservations']} open), remaining {s['remaining_usd']:.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("create-clinic").add_argument("clinic")
    sub.add_parser("create-key").add_argument("--clinic", required=True)
    sub.add_parser("revoke-key").add_argument("prefix")
    cr = sub.add_parser("create-reviewer")
    cr.add_argument("--clinic", required=True)
    cr.add_argument("--email", required=True)
    cr.add_argument("--role", choices=ROLES, default="reviewer")
    cr.add_argument("--generate", action="store_true", help="make a random password and print it once")
    dr = sub.add_parser("disable-reviewer")
    dr.add_argument("--clinic", required=True)
    dr.add_argument("--email", required=True)
    sub.add_parser("spend")
    sub.add_parser("release-reservations")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    if args.command == "spend":
        print_spend(engine, settings.spend_limit_usd)
    elif args.command == "release-reservations":
        from .budget import Budget
        n = Budget(engine, float("inf")).release_open()
        print(f"released {n} open reservation(s); check the Anthropic Console bill first")
    elif args.command == "create-clinic":
        print(f"created clinic {create_clinic(engine, args.clinic)}")
    elif args.command == "create-key":
        key = create_key(engine, args.clinic)
        print(f"{key}\nStore this now: it is not shown again, and only its hash is kept.")
    elif args.command == "create-reviewer":
        generated = args.generate
        password = secrets.token_urlsafe(12) if generated else getpass.getpass("Password (10+ characters): ")
        if not generated and getpass.getpass("Again: ") != password:
            sys.exit("passwords do not match")
        add_reviewer(engine, args.clinic, args.email, args.role, password)
        print(f"created {args.role} {args.email.strip().lower()} for {args.clinic}")
        if generated:
            print(f"Password: {password}\nStore this now: it is not shown again, and only its hash is kept.")
    elif args.command == "disable-reviewer":
        with engine.begin() as conn:
            if not disable_reviewer(conn, args.clinic, args.email):
                sys.exit(f"no live reviewer {args.email!r} in {args.clinic!r}")
        print(f"disabled {args.email.strip().lower()}")
    else:
        with engine.begin() as conn:
            if not revoke(conn, args.prefix):
                sys.exit(f"no live key with prefix {args.prefix!r}")
        print(f"revoked {args.prefix}")


if __name__ == "__main__":
    main()
