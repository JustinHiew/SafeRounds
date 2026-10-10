"""Admin CLI. Run on the server; there is no admin API, so no admin credential can leak over HTTP.

    python -m safety_eval.service.admin create-clinic harbourline
    python -m safety_eval.service.admin create-key --clinic harbourline     # prints the key once
    python -m safety_eval.service.admin revoke-key sek_AbCd1234
"""

import argparse
import sys

import sqlalchemy as sa

from safety_eval.clinic import Clinic, ClinicConfigError, available

from .auth import new_key, revoke
from .db import api_keys, clinics, make_engine, utcnow
from .settings import Settings


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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("create-clinic").add_argument("clinic")
    sub.add_parser("create-key").add_argument("--clinic", required=True)
    sub.add_parser("revoke-key").add_argument("prefix")
    args = ap.parse_args(argv)
    engine = make_engine(Settings.from_env().database_url)
    if args.command == "create-clinic":
        print(f"created clinic {create_clinic(engine, args.clinic)}")
    elif args.command == "create-key":
        key = create_key(engine, args.clinic)
        print(f"{key}\nStore this now: it is not shown again, and only its hash is kept.")
    else:
        with engine.begin() as conn:
            if not revoke(conn, args.prefix):
                sys.exit(f"no live key with prefix {args.prefix!r}")
        print(f"revoked {args.prefix}")


if __name__ == "__main__":
    main()
