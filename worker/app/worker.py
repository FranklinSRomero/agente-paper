import logging
import os

from redis import Redis
from rq import Connection, Worker

from .logging_conf import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    conn = Redis.from_url(redis_url)
    logger.info("starting_worker")
    with Connection(conn):
        w = Worker(["vision"])
        w.work()


if __name__ == "__main__":
    main()
