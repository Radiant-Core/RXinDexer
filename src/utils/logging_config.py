# /Users/radiant/Desktop/RXinDexer/src/utils/logging_config.py
# This file provides enhanced logging configuration for RXinDexer.
# It sets up structured logging with consistent formatting and log levels.

import os
import logging
import json
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
import traceback

# Determine environment
IN_DOCKER = os.environ.get("IN_DOCKER", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
SERVICE_NAME = os.environ.get("SERVICE_NAME", "rxindexer")

# Configure log directory
LOG_DIR = "/app/logs" if IN_DOCKER else os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Custom JSON formatter for structured logging
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "service": SERVICE_NAME
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info)
            }
            
        # Add extra fields if present
        if hasattr(record, "extra"):
            log_data.update(record.extra)
            
        return json.dumps(log_data)

# Setup function for application-wide logging
def setup_logging(service_component=None):
    """
    Configure comprehensive logging for the application
    
    Args:
        service_component: Optional component name to include in log file names
    """
    # Define log file name
    component_suffix = f"-{service_component}" if service_component else ""
    log_file = os.path.join(LOG_DIR, f"{SERVICE_NAME}{component_suffix}.log")
    
    # Create handlers
    console_handler = logging.StreamHandler()
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    
    # Create formatters
    console_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    console_formatter = logging.Formatter(console_format)
    json_formatter = JsonFormatter()
    
    # Set formatters
    console_handler.setFormatter(console_formatter)
    file_handler.setFormatter(json_formatter)
    
    # Set levels
    console_handler.setLevel(logging.getLevelName(LOG_LEVEL))
    file_handler.setLevel(logging.getLevelName(LOG_LEVEL))
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.getLevelName(LOG_LEVEL))
    
    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    # Add handlers to root logger
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Add special warning for debug level in production
    if LOG_LEVEL == "DEBUG" and not os.environ.get("DEVELOPMENT_MODE"):
        root_logger.warning("DEBUG logging enabled in a non-development environment - this may impact performance")
    
    # Return a reference to the configured logger
    logger = logging.getLogger(service_component or SERVICE_NAME)
    logger.info(f"Logging initialized for {SERVICE_NAME}{component_suffix} at {LOG_LEVEL} level")
    return logger

# Context manager for timing operations
class LogTiming:
    def __init__(self, logger, operation_name):
        self.logger = logger
        self.operation_name = operation_name
        self.start_time = None
        
    def __enter__(self):
        self.start_time = time.time()
        self.logger.debug(f"Starting operation: {self.operation_name}")
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        if exc_type:
            self.logger.error(
                f"Operation {self.operation_name} failed after {duration:.2f}s",
                exc_info=(exc_type, exc_val, exc_tb)
            )
        else:
            self.logger.debug(f"Completed operation: {self.operation_name} in {duration:.2f}s")
