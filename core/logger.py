"""
VOX System Logger
Internal Name: THE SCRIBE

Provides standardized, color-coded telemetry for the entire VOX ecosystem.
Designed to mimic the clarity of systemd logs while providing clear 
differentiation between System-level and Agent-level events.
"""

from datetime import datetime

# --- ANSI Terminal Colors (Professional Palette) ---
SYSTEM_CLR = "\033[94m"   # Blue (Orchestrator)
AGENT_CLR  = "\033[95m"   # Magenta (Individual Entities)
FAIL_CLR   = "\033[91m"   # Red (Critical Failures)
WARN_CLR   = "\033[33m"   # Orange (Security/Limit Warnings)
INFO_CLR   = "\033[96m"   # Cyan (Operational Data)
OK_CLR     = "\033[92m"   # Green (Success Signals)
TIMESTAMP  = "\033[90m"   # Grey (Temporal Metadata)
BOLD       = "\033[1m"
RESET      = "\033[0m"

def _systemd_log(level: str, message: str, sender: str = ""):
    """
    Core Logging Dispatcher.
    Formats and prints telemetry signals with millisecond precision.
    """
    # Color Mapping based on Severity
    level_colors = {
        "OK": OK_CLR,
        "INFO": INFO_CLR,
        "WARN": WARN_CLR,
        "FAIL": FAIL_CLR
    }
    
    color = level_colors.get(level, RESET)
    
    # Generate Metadata
    # Timestamp: [YYYY-MM-DD HH:MM:SS.mmm]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    ts_text = f"{TIMESTAMP}{now}{RESET}"
    
    # Status Tag: [  LEVEL  ]
    status_text = f"[{color}{BOLD}  {level:^4}  {RESET}]"

    # Context Prefix: Determines if it's a System-level or Agent-specific message
    if sender:
        prefix = f" {BOLD}{AGENT_CLR}{sender}{RESET}:"
    else:
        prefix = f" {BOLD}{SYSTEM_CLR}SYSTEM{RESET}:"

    # Final Output Assembly
    print(f"{ts_text} {status_text}{prefix} {message}")

# --- API Wrappers ---

def log_ok(msg: str, agent: str = ""): 
    """Signals successful operation completion."""
    _systemd_log("OK", msg, agent)

def log_info(msg: str, agent: str = ""): 
    """Standard operational telemetry."""
    _systemd_log("INFO", msg, agent)

def log_warn(msg: str, agent: str = ""): 
    """Signals non-critical anomalies or rate-limit warnings."""
    _systemd_log("WARN", msg, agent)

def log_fail(msg: str, agent: str = ""): 
    """Reports critical errors or security breaches."""
    _systemd_log("FAIL", msg, agent)