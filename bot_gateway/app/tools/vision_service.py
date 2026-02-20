import os

from redis import Redis
from rq import Queue


class VisionToolService:
    def __init__(self):
        self.enabled = os.getenv("VISION_ENABLE", "true").lower() == "true"
        self.timeout_seconds = int(os.getenv("VISION_TIMEOUT_SECONDS", "20"))
        self.redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        self.queue = Queue("vision", connection=self.redis)

    def submit_image(self, image_b64: str):
        return self.queue.enqueue("app.tasks.process_image_payload", image_b64, job_timeout=self.timeout_seconds)
