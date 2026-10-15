"""Pure observation policy. No screen or network side effects."""
from dataclasses import dataclass
import time

COOLDOWNS = {'quiet': 45.0, 'balanced': 20.0, 'active': 8.0}

@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    process_id: int
    application: str = ""

class ObservationPolicy:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.enabled = False
        self.generation = 0
        self.latest = None
        self.signature = None
        self.last_comment = float('-inf')
        self.last_text = ''
        self.style = 'balanced'
        self.retry_attempts = 0
        self.retry_at = 0.0

    def invalidate(self):
        self.generation += 1
        self.latest = None
        self.signature = None
        self.retry_attempts = 0
        self.retry_at = 0.0

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.invalidate()

    def changed(self, signature, threshold=12):
        old = self.signature
        self.signature = signature
        return old is None or (len(old) == len(signature) and
            sum(abs(a-b) for a,b in zip(old, signature))/max(1,len(signature)) > threshold)

    def ready(self):
        return (self.enabled and self.clock() >= self.retry_at
                and self.clock()-self.last_comment >= COOLDOWNS[self.style])

    def new_frame(self, frame):
        self.latest = frame
        self.retry_attempts = 0
        self.retry_at = 0.0

    def retry(self, frame):
        delays = (5.0, 15.0, 30.0)
        if not self.enabled or self.retry_attempts >= len(delays):
            self.latest = None
            return False
        self.retry_at = self.clock() + delays[self.retry_attempts]
        self.retry_attempts += 1
        # Keep a newer frame if observation replaced the failed one.
        if self.latest is None: self.latest = frame
        return True

    def accept_comment(self, text):
        clean = ''.join(text.split())
        if not clean or clean == '[[SILENT]]' or clean == ''.join(self.last_text.split()):
            return False
        self.last_comment = self.clock()
        self.last_text = text
        return True
