# Health and performance monitoring for RXinDexer
import logging
import time
from database.session import SessionLocal
from indexer.sync import rpc_call
from sqlalchemy import text

logger = logging.getLogger("rxindexer.monitor")

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
    # Automated alerting thresholds
    alerts = []
    if status.get("sync_lag", {}).get("lag", 0) > 10000:
        alerts.append(f"Sync lag critical: {status['sync_lag']['lag']}")
    if psutil and status.get("cpu", 0) > 90:
        alerts.append(f"CPU usage critical: {status['cpu']}%")
    if psutil and status.get("mem", {}).get("percent", 0) > 90:
        alerts.append(f"Memory usage critical: {status['mem']['percent']}%")
    if alerts:
        for alert in alerts:
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
