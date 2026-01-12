# Standardized logging configuration
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Path configuration
BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs" / "components"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "components.log"

# Custom Formatter to match previous style
class CustomFormatter(logging.Formatter):
    def format(self, record):
        record.caller = record.name  # Use logger name as caller
        return super().format(record)

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S,%f"

def configure_logging():
    """Configures the root logger to write to components.log"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # File Handler
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATE_FORMAT[:-3])) # Truncate micros
    root_logger.addHandler(file_handler)
    
    # Console Handler (Optional, for dev)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATE_FORMAT[:-3]))
    root_logger.addHandler(console_handler)

def get_logger(name: str) -> logging.Logger:
    """Returns a configured logger"""
    return logging.getLogger(name)

# Backward compatibility wrappers
def log_event(level: str, caller: str, message: str) -> Path:
    logger = logging.getLogger(caller)
    lvl = getattr(logging, (level or "INFO").upper(), logging.INFO)
    logger.log(lvl, message)
    return LOG_FILE

def log_print(level: str, caller: str, message: str) -> Path:
    log_event(level, caller, message)
    # Print is handled by StreamHandler if added, or we can force print here if needed
    # For now, relying on logging handlers is cleaner.