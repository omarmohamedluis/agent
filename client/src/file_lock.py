import fcntl
import os
import time
from pathlib import Path
from contextlib import contextmanager
import logging

LOGGER = logging.getLogger("omimidi.file_lock")

@contextmanager
def file_lock(lock_path: Path, timeout: float = 5.0):
    """
    Simple cross-process file lock using fcntl.flock.
    
    Usage:
        with file_lock(Path("data/structure.json.lock")):
            # do something with structure.json
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Open the lock file. We use 'a' to avoid truncating if it exists, 
    # but 'w' is also fine as we don't care about content.
    f = open(lock_path, "a")
    try:
        start_time = time.time()
        while True:
            try:
                # LOCK_EX: Exclusive lock
                # LOCK_NB: Non-blocking (we handle the loop ourselves for timeout)
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                if time.time() - start_time > timeout:
                    LOGGER.error(f"Timeout acquiring lock on {lock_path}")
                    raise TimeoutError(f"Could not acquire lock on {lock_path} after {timeout}s")
                time.sleep(0.05)
        
        yield
        
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()
