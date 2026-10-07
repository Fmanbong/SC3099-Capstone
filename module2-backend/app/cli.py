"""Run with python -m app.cli create-admin or cleanup."""
import argparse
import getpass
import json
import os

from sqlalchemy import select

from app.config import Settings
from app.database import build_database
from app.models import User
from app.routers.auth import RegisterRequest
from app.security import hash_password
from app.services.audit import audit_event


def main():
    parser = argparse.ArgumentParser(description='SAIV backend maintenance')
    commands = parser.add_subparsers(dest='command', required=True)
    admin = commands.add_parser('create-admin', help='Create the initial administrator in a migrated database')
    admin.add_argument('--email', required=True)
    admin.add_argument('--name', default='Administrator')
    commands.add_parser('cleanup', help='Apply the configured retention policy now')
    args = parser.parse_args()
    settings = Settings()
    engine, factory = build_database(settings.database_url)
    try:
        with factory() as db:
            db.info['settings'] = settings
            if args.command == 'create-admin':
                password = os.getenv('SAIV_BOOTSTRAP_PASSWORD') or getpass.getpass('Admin password: ')
                data = RegisterRequest(email=args.email, full_name=args.name, password=password, role='admin')
                email = str(data.email).lower()
                if db.scalar(select(User.id).where(User.email == email)):
                    parser.error('This email is already registered; no account was changed')
                user = User(email=email, full_name=data.full_name, role='admin',
                            hashed_password=hash_password(data.password, settings.bcrypt_rounds))
                db.add(user)
                db.flush()
                audit_event(db, 'admin_bootstrapped', user.id, 'user', user.id)
                db.commit()
                print('Administrator created. Sign in through /api/v1/auth/login.')
            else:
                from app.services.retention import cleanup_expired_records
                print(json.dumps(cleanup_expired_records(db)))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
