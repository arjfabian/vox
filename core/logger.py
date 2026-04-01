# ANSI Colors
SYSTEM_CLR = "\033[94m"   # Blue
AGENT_CLR  = "\033[95m"   # Magenta
FAIL_CLR   = "\033[91m"   # Red
WARN_CLR   = "\033[33m"   # Orange
INFO_CLR   = "\033[96m"   # Cyan
OK_CLR     = "\033[92m"   # Green
BOLD       = "\033[1m"
RESET      = "\033[0m"

def _systemd_log(level: str, message: str, sender: str = ""):
    """
    Core Logger logic.
    """
    level_colors = {
        "OK": OK_CLR,
        "INFO": INFO_CLR,
        "WARN": WARN_CLR,
        "FAIL": FAIL_CLR
    }
    color = level_colors.get(level, RESET)
    status_text = f"[{color}{BOLD}  {level:^4}  {RESET}]"

    # If prefix is not specified, treat it as a System message
    prefix = f" {BOLD}{AGENT_CLR}{sender}{RESET}:" if sender else ""

    # Output the formatted message
    print(f"{status_text}{prefix} {message}")

# --- Wrappers for System messages ---

def log_ok(msg: str, agent: str = ""): _systemd_log("OK", msg, agent)
def log_info(msg: str, agent: str = ""): _systemd_log("INFO", msg, agent)
def log_warn(msg: str, agent: str = ""): _systemd_log("WARN", msg, agent)
def log_fail(msg: str, agent: str = ""): _systemd_log("FAIL", msg, agent)
