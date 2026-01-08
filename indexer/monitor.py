# Health and performance monitoring for RXinDexer
import logging
import time
import os
from database.session import SessionLocal
from indexer.sync import rpc_call
from sqlalchemy import text

logger = logging.getLogger("rxindexer.monitor")

# Try to import metrics and alerts
try:
    from config.metrics import record_sync_metrics, record_alert
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    def record_sync_metrics(*args, **kwargs): pass
    def record_alert(*args, **kwargs): pass

try:
    from config.logging_config import alert_manager, AlertLevel
    ALERTS_AVAILABLE = True
except ImportError:
    ALERTS_AVAILABLE = False
    class AlertLevel:
        INFO = "info"
        WARNING = "warning"
        CRITICAL = "critical"

# Alert thresholds (configurable via environment)
SYNC_LAG_WARNING_THRESHOLD = int(os.getenv("SYNC_LAG_WARNING_THRESHOLD", "1000"))
SYNC_LAG_CRITICAL_THRESHOLD = int(os.getenv("SYNC_LAG_CRITICAL_THRESHOLD", "10000"))
CPU_WARNING_THRESHOLD = int(os.getenv("CPU_WARNING_THRESHOLD", "90"))
MEMORY_WARNING_THRESHOLD = int(os.getenv("MEMORY_WARNING_THRESHOLD", "90"))

# Check block sync lag
def get_sync_lag():
    db = SessionLocal()
    try:
        latest_height = rpc_call("getblockcount")
        res = db.execute(text("SELECT MAX(height) FROM blocks"))
        db_height = res.scalar() or 0
        lag = latest_height - db_height
        return {"node_height": latest_height, "db_height": db_height, "lag": lag}
    except Exception as e:
        logger.error(f"Sync lag check failed: {e}")
        return {"error": str(e)}
    finally:
        db.close()

# Check DB connectivity
def db_health():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False
    finally:
        db.close()

# Check API health (calls /health/db endpoint)
def api_health(api_url="http://api:8000/health/db"):
    import requests
    try:
        r = requests.get(api_url, timeout=15)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"API health check failed: {e}")
        return False

import threading
import sys
try:
    import psutil
except ImportError:
    psutil = None

def monitor_all():
    status = {
        "sync_lag": get_sync_lag(),
        "db": db_health(),
        "api": api_health(),
        "timestamp": time.time(),
    }
    # Add resource usage if psutil is available
    if psutil:
        status["cpu"] = psutil.cpu_percent(interval=1)
        status["mem"] = psutil.virtual_memory()._asdict()
    logger.info(f"Monitor status: {status}")
    
    # Record metrics if available
    if METRICS_AVAILABLE:
        sync_lag_info = status.get("sync_lag", {})
        if isinstance(sync_lag_info, dict) and "lag" in sync_lag_info:
            record_sync_metrics(
                db_height=sync_lag_info.get("db_height", 0),
                node_height=sync_lag_info.get("node_height", 0)
            )
    
    # Automated alerting with configurable thresholds
    sync_lag = status.get("sync_lag", {}).get("lag", 0)
    cpu_usage = status.get("cpu", 0) if psutil else 0
    mem_usage = status.get("mem", {}).get("percent", 0) if psutil else 0
    
    # Check sync lag
    if sync_lag > SYNC_LAG_CRITICAL_THRESHOLD:
        alert_msg = f"Sync lag critical: {sync_lag} blocks (threshold: {SYNC_LAG_CRITICAL_THRESHOLD})"
        logger.warning(f"ALERT: {alert_msg}")
        if ALERTS_AVAILABLE:
            alert_manager.alert(AlertLevel.CRITICAL, alert_msg, {"sync_lag": sync_lag})
        if METRICS_AVAILABLE:
            record_alert("critical")
    elif sync_lag > SYNC_LAG_WARNING_THRESHOLD:
        alert_msg = f"Sync lag warning: {sync_lag} blocks (threshold: {SYNC_LAG_WARNING_THRESHOLD})"
        logger.warning(f"ALERT: {alert_msg}")
        if ALERTS_AVAILABLE:
            alert_manager.alert(AlertLevel.WARNING, alert_msg, {"sync_lag": sync_lag})
        if METRICS_AVAILABLE:
            record_alert("warning")
    
    # Check CPU usage
    if cpu_usage > CPU_WARNING_THRESHOLD:
        alert_msg = f"CPU usage high: {cpu_usage}% (threshold: {CPU_WARNING_THRESHOLD}%)"
        logger.warning(f"ALERT: {alert_msg}")
        if ALERTS_AVAILABLE:
            alert_manager.alert(AlertLevel.WARNING, alert_msg, {"cpu_percent": cpu_usage})
        if METRICS_AVAILABLE:
            record_alert("warning")
    
    # Check memory usage
    if mem_usage > MEMORY_WARNING_THRESHOLD:
        alert_msg = f"Memory usage high: {mem_usage}% (threshold: {MEMORY_WARNING_THRESHOLD}%)"
        logger.warning(f"ALERT: {alert_msg}")
        if ALERTS_AVAILABLE:
            alert_manager.alert(AlertLevel.WARNING, alert_msg, {"memory_percent": mem_usage})
        if METRICS_AVAILABLE:
            record_alert("warning")
    
    # Check database health
    if not status.get("db"):
        alert_msg = "Database health check failed"
        logger.error(f"ALERT: {alert_msg}")
        if ALERTS_AVAILABLE:
            alert_manager.alert(AlertLevel.CRITICAL, alert_msg)
        if METRICS_AVAILABLE:
            record_alert("critical")
    
    # Check API health
    if not status.get("api"):
        alert_msg = "API health check failed"
        logger.error(f"ALERT: {alert_msg}")
        if ALERTS_AVAILABLE:
            alert_manager.alert(AlertLevel.WARNING, alert_msg)
        if METRICS_AVAILABLE:
            record_alert("warning")
    
    return status

def start_monitoring_thread(interval=60):
    def loop():
        while True:
            monitor_all()
            time.sleep(interval)
    t = threading.Thread(target=loop, daemon=True)
    t.start()

# --- PERIODIC STATUS CHECKS ---
import collections
_lag_history = collections.deque(maxlen=3)

def periodic_status_check(interval=600):  # 10 minutes
    import sys
    def loop():
        last_lag = None
        while True:
            status = monitor_all()
            lag = status.get("sync_lag", {}).get("lag", None)
            db_height = status.get("sync_lag", {}).get("db_height", None)
            logger.info(f"[PERIODIC STATUS] Block height: {db_height}, Sync lag: {lag}, CPU: {status.get('cpu')}, Mem: {status.get('mem', {}).get('percent') if status.get('mem') else None}%")
            if lag is not None:
                _lag_history.append(lag)
                if len(_lag_history) == 3 and all(_lag_history[i] > _lag_history[i-1] for i in range(1, 3)):
                    alert = f"Sync lag is increasing for 3 consecutive checks: {_lag_history}"
                    logger.warning(f"ALERT: {alert}")
                    # --- EMAIL ALERT SNIPPET ---
                    # To enable, fill in SMTP config below and uncomment.
                    # import smtplib
                    # from email.message import EmailMessage
                    # EMAIL_FROM = "monitor@example.com"
                    # EMAIL_TO = "your@email.com"
                    # SMTP_SERVER = "smtp.example.com"
                    # SMTP_PORT = 587
                    # SMTP_USER = "smtp_user"
                    # SMTP_PASS = "smtp_pass"
                    # msg = EmailMessage()
                    # msg.set_content(alert)
                    # msg["Subject"] = f"RXinDexer ALERT: {alert[:40]}"
                    # msg["From"] = EMAIL_FROM
                    # msg["To"] = EMAIL_TO
                    # with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
                    #     s.starttls()
                    #     s.login(SMTP_USER, SMTP_PASS)
                    #     s.send_message(msg)
                    # --- END EMAIL ALERT SNIPPET ---
            time.sleep(interval)
    t = threading.Thread(target=loop, daemon=True)
    t.start()

# To enable periodic status checks, call periodic_status_check() from your daemon entrypoint.
