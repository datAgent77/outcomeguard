"""Verification worker process.

    python -m outcomeguard.verification.worker [--once]

Holds database access (ideally a restricted role) and the sink secret; never PayPal
credentials — it refuses to start if any PAYPAL_* variable is in its environment, and its
settings class has no PayPal fields.
"""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from outcomeguard.verification.service import Orchestrator, claim_job, new_worker_id
from outcomeguard.verification.settings import assert_no_paypal_credentials, get_verifier_settings

log = logging.getLogger("outcomeguard.verifier")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="process queued jobs, then exit")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()
    assert_no_paypal_credentials()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_verifier_settings()
    make_session = sessionmaker(
        bind=create_engine(settings.database_url, pool_pre_ping=True), expire_on_commit=False
    )
    worker_id = new_worker_id()
    log.info("verifier %s started", worker_id)
    while True:
        session = make_session()
        try:
            job = claim_job(session, worker_id, settings.lease_seconds)
            if job is None:
                if args.once:
                    return
                time.sleep(args.poll)
                continue
            run = Orchestrator(session, settings, worker_id).process(job)
            log.info(
                "run %s -> %s / %s",
                run.id,
                run.status.value,
                run.verdict.value if run.verdict else None,
            )
        finally:
            session.close()


if __name__ == "__main__":
    main()
