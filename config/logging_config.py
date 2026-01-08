# Centralized logging configuration for RXinDexer
# Provides structured logging with severity levels and correlation IDs

import os
import sys
import json
import logging
import threading
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from functools import wraps
import time

# Thread-local storage for correlation IDs
_thread_local = threading.local()


def get_correlation_id() -> str:
    """Get the current correlation ID or generate a new one."""
    if not hasattr(_thread_local, 'correlation_id'):
        _thread_local.correlation_id = str(uuid.uuid4())[:8]
    return _thread_local.correlation_id


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """Set a new correlation ID for the current thread."""
    _thread_local.correlation_id = correlation_id or str(uuid.uuid4())[:8]
    return _thread_local.correlation_id


class StructuredFormatter(logging.Formatter):
    """
    Structured JSON formatter for production logging.
    Includes timestamp, level, correlation_id, component, and message.
    """
    
    def __init__(self, component: str = "rxindexer"):
        super().__init__()
        self.component = component
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "component": getattr(record, 'component', self.component),
            "correlation_id": get_correlation_id(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields if present
        if hasattr(record, 'extra_data') and record.extra_data:
            log_entry["data"] = record.extra_data
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Add location info for errors
        if record.levelno >= logging.ERROR:
            log_entry["location"] = {
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName
            }
        
        return json.dumps(log_entry)


class HumanReadableFormatter(logging.Formatter):
    """
    Human-readable formatter for development/console logging.
    """
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'
    }
    
    def __init__(self, component: str = "rxindexer", use_colors: bool = True):
        super().__init__()
        self.component = component
        self.use_colors = use_colors and sys.stdout.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        correlation_id = get_correlation_id()
        component = getattr(record, 'component', self.component)
        message = record.getMessage()
        
        if self.use_colors:
            color = self.COLORS.get(level, '')
            reset = self.COLORS['RESET']
            formatted = f"{timestamp} [{color}{level:8}{reset}] [{component}] [{correlation_id}] {message}"
        else:
            formatted = f"{timestamp} [{level:8}] [{component}] [{correlation_id}] {message}"
        
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        
        return formatted


class RXLogger(logging.Logger):
    """Extended logger with structured data support."""
    
    def _log_with_data(self, level: int, msg: str, data: Optional[Dict[str, Any]] = None, 
                       *args, **kwargs):
        if data:
            kwargs.setdefault('extra', {})['extra_data'] = data
        super()._log(level, msg, args, **kwargs)
    
    def info_data(self, msg: str, data: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self._log_with_data(logging.INFO, msg, data, *args, **kwargs)
    
    def warning_data(self, msg: str, data: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self._log_with_data(logging.WARNING, msg, data, *args, **kwargs)
    
    def error_data(self, msg: str, data: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self._log_with_data(logging.ERROR, msg, data, *args, **kwargs)


# Replace default logger class
logging.setLoggerClass(RXLogger)


def setup_logging(
    component: str = "rxindexer",
    level: str = None,
    json_format: bool = None
) -> logging.Logger:
    """
    Configure logging for a component.
    
    Args:
        component: Name of the component (api, indexer, daemon, etc.)
        level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to env var LOG_LEVEL or INFO.
        json_format: Use JSON format. Defaults to env var LOG_JSON_FORMAT or False.
    
    Returns:
        Configured logger instance.
    """
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    if json_format is None:
        json_format = os.getenv("LOG_JSON_FORMAT", "0").lower() in ("1", "true", "yes")
    
    # Get or create logger
    logger = logging.getLogger(f"rxindexer.{component}")
    logger.setLevel(getattr(logging, level, logging.INFO))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level, logging.INFO))
    
    # Set formatter
    if json_format:
        handler.setFormatter(StructuredFormatter(component))
    else:
        handler.setFormatter(HumanReadableFormatter(component))
    
    logger.addHandler(handler)
    logger.propagate = False
    
    return logger


def log_operation(operation_name: str, logger: logging.Logger = None):
    """
    Decorator to log operation start/end with timing.
    
    Usage:
        @log_operation("sync_blocks")
        def sync_blocks(...):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal logger
            if logger is None:
                logger = logging.getLogger("rxindexer")
            
            start_time = time.time()
            logger.info(f"[{operation_name}] Starting...")
            
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                logger.info(f"[{operation_name}] Completed in {elapsed:.2f}s")
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"[{operation_name}] Failed after {elapsed:.2f}s: {e}")
                raise
        
        return wrapper
    return decorator


# Alert levels for the alerting system
class AlertLevel:
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertManager:
    """
    Centralized alert management system.
    Collects and dispatches alerts to configured channels.
    """
    
    def __init__(self):
        self.alerts: list = []
        self.logger = setup_logging("alerts")
        self._lock = threading.Lock()
        
        # Configuration from environment
        self.webhook_url = os.getenv("ALERT_WEBHOOK_URL")
        self.email_enabled = os.getenv("ALERT_EMAIL_ENABLED", "0").lower() in ("1", "true", "yes")
        self.email_to = os.getenv("ALERT_EMAIL_TO")
        self.smtp_server = os.getenv("ALERT_SMTP_SERVER")
        self.smtp_port = int(os.getenv("ALERT_SMTP_PORT", "587"))
        self.smtp_user = os.getenv("ALERT_SMTP_USER")
        self.smtp_pass = os.getenv("ALERT_SMTP_PASS")
    
    def alert(self, level: str, message: str, data: Optional[Dict[str, Any]] = None):
        """Raise an alert."""
        alert_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level,
            "message": message,
            "data": data or {},
            "correlation_id": get_correlation_id()
        }
        
        with self._lock:
            self.alerts.append(alert_entry)
            # Keep only last 100 alerts in memory
            if len(self.alerts) > 100:
                self.alerts = self.alerts[-100:]
        
        # Log the alert
        if level == AlertLevel.CRITICAL:
            self.logger.critical(f"ALERT: {message}", extra={'extra_data': data})
        elif level == AlertLevel.WARNING:
            self.logger.warning(f"ALERT: {message}", extra={'extra_data': data})
        else:
            self.logger.info(f"ALERT: {message}", extra={'extra_data': data})
        
        # Dispatch to configured channels
        self._dispatch_alert(alert_entry)
    
    def _dispatch_alert(self, alert: Dict[str, Any]):
        """Dispatch alert to configured channels."""
        # Webhook notification
        if self.webhook_url and alert["level"] in (AlertLevel.WARNING, AlertLevel.CRITICAL):
            self._send_webhook(alert)
        
        # Email notification for critical alerts
        if self.email_enabled and alert["level"] == AlertLevel.CRITICAL:
            self._send_email(alert)
    
    def _send_webhook(self, alert: Dict[str, Any]):
        """Send alert to webhook URL."""
        try:
            import requests
            requests.post(
                self.webhook_url,
                json=alert,
                timeout=5
            )
        except Exception as e:
            self.logger.error(f"Failed to send webhook alert: {e}")
    
    def _send_email(self, alert: Dict[str, Any]):
        """Send alert via email."""
        if not all([self.email_to, self.smtp_server, self.smtp_user, self.smtp_pass]):
            return
        
        try:
            import smtplib
            from email.message import EmailMessage
            
            msg = EmailMessage()
            msg.set_content(f"Alert Level: {alert['level']}\n\nMessage: {alert['message']}\n\nData: {json.dumps(alert.get('data', {}), indent=2)}")
            msg["Subject"] = f"RXinDexer ALERT: {alert['message'][:50]}"
            msg["From"] = self.smtp_user
            msg["To"] = self.email_to
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as s:
                s.starttls()
                s.login(self.smtp_user, self.smtp_pass)
                s.send_message(msg)
        except Exception as e:
            self.logger.error(f"Failed to send email alert: {e}")
    
    def get_recent_alerts(self, count: int = 20) -> list:
        """Get recent alerts."""
        with self._lock:
            return list(self.alerts[-count:])


# Global alert manager instance
alert_manager = AlertManager()


def get_logger(component: str) -> logging.Logger:
    """Get or create a logger for a component."""
    return setup_logging(component)
