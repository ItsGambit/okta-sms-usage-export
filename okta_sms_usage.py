#!/usr/bin/env python3
"""
okta_sms_usage.py - export SMS (and optionally Voice) telephony events from an
Okta tenant's System Log to CSV.

*** THIS IS NOT AN OFFICIAL OKTA SCRIPT. ***
It is provided as-is, with no warranty or support. You use it entirely at your
own risk. Review it before running it and test it in a non-production org first.

The script is READ-ONLY: it only issues GET requests to /api/v1/logs.

Requirements: Python 3.8+, standard library only (the run_okta_sms_usage.sh /
.ps1 launchers check for Python and offer to install it).

Event types (source: https://developer.okta.com/docs/okta-event-types.csv):
  SMS   system.sms.send_*          one event per message Okta attempts to send
  Voice system.voice.send_*        one event per call (use --include-voice)
  system.sms.receive_status / system.voice.receive_status are provider delivery
  receipts (one per send event) and are NOT counted as usage.
  Since release 2022.06.0 a send event with outcome DENY means Okta blocked the
  message. Those rows are exported but reported separately from sent messages.

Limits: the System Log keeps 90 days of events. Run this regularly and keep the
CSVs if you need a longer history.

Usage
  Interactive:      python okta_sms_usage.py
  Non-interactive:  set OKTA_API_TOKEN, then
                    python okta_sms_usage.py --non-interactive --accept-risk \
                        --org-url https://example.okta.com \
                        --start 2026-09-01 --end 2026-11-01 --output sms.csv
  Dry run:          add --dry-run to either form. Validates every setting, prints
                    exactly what WOULD be requested (token never shown), then exits
                    WITHOUT contacting Okta or writing the CSV.
  Settings may also be edited in the CONFIGURATION block below.
  Precedence: command-line flag > environment variable > this file.

Rate limiting: every response's X-Rate-Limit-Limit / -Remaining / -Reset headers
are checked. Once 90% of the window's budget is used the script WAITS until the window
resets, then continues, BEFORE Okta rejects a call. An HTTP 429 is waited out and retried.

Logging: progress and errors go to the console (stderr) and to a rotating log file
(default okta_sms_usage.log, 5 x 1 MB, always DEBUG). --verbose adds DEBUG to the
console. The API token is never logged; anything that looks like one is redacted.

Exit codes: 0 success, 1 runtime/API error, 2 bad arguments/configuration,
130 interrupted.

The API token needs read access to the System Log (okta.logs.read, e.g. a token
from a read-only administrator). Pass it via the OKTA_API_TOKEN environment
variable or the interactive hidden prompt; there is deliberately no --token flag
because command lines are visible to other users and shell history.
"""

import sys

if sys.version_info < (3, 8):
    sys.exit("error: Python 3.8 or newer is required (found %d.%d). Use run_okta_sms_usage.sh "
             "(macOS/Linux) or run_okta_sms_usage.ps1 (Windows) to check for / install Python."
             % sys.version_info[:2])

import argparse
import csv
import email.utils
import getpass
import json
import logging
import logging.handlers
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

try:
    import ssl  # missing on a few minimal/custom Python builds
except ImportError:
    sys.exit("error: this Python has no SSL support, so it cannot make https calls to Okta. "
             "Install a standard Python build from https://www.python.org/downloads/.")

# ============================== CONFIGURATION ==============================
# Edit these if you do not want to pass flags / answer prompts.
ORG_URL = ""            # e.g. "https://example.okta.com" (custom/oktapreview domains work too)
API_TOKEN = ""          # Prefer the OKTA_API_TOKEN environment variable. Never commit a token.
START_DATE = ""         # "YYYY-MM-DD" (UTC, inclusive). Blank = 90 days ago.
END_DATE = ""           # "YYYY-MM-DD" (UTC, inclusive). Blank = now.
OUTPUT_CSV = "okta_sms_usage.csv"
INCLUDE_VOICE = False   # True also exports voice-call events
INCLUDE_DEBUG_DATA = False  # True adds raw debugContext.debugData (may contain phone numbers)
LOG_FILE = "okta_sms_usage.log"  # rotating log file; "" disables file logging
# ===========================================================================

SCRIPT_VERSION = "1.1.0"

# Event types come from Okta's published catalogue (see module docstring).
SMS_EVENT_TYPES = [
    "system.sms.send_account_unlock_message",
    "system.sms.send_factor_verify_message",
    "system.sms.send_okta_push_verify_message",
    "system.sms.send_password_reset_message",
    "system.sms.send_phone_verification_message",
]
VOICE_EVENT_TYPES = [
    "system.voice.send_account_unlock_call",
    "system.voice.send_call",
    "system.voice.send_mfa_challenge_call",
    "system.voice.send_password_reset_call",
    "system.voice.send_phone_verification_call",
]
RETENTION_DAYS = 90      # System Log retention
PAGE_LIMIT = 1000        # max page size for /api/v1/logs
MAX_RETRIES = 6          # per request, for 429 / 5xx / network errors
REQUEST_TIMEOUT = 60     # seconds
# Pacing: use at most 90% of each rate limit. Once only ceil(10% x limit) calls remain, send nothing more
# until the window resets (WAIT, not stop), then carry on. Limit 100 -> pause after 90
# calls; limit 60 -> after 54. A 429 is only a safety net.
RATE_LIMIT_RESERVE_PERCENT = 10
RESET_GUARD_SECS = 1         # added to every computed wait
DEFAULT_WINDOW_SECS = 60     # used only when the reset time is missing/unusable
BLIND_INTERVAL_SECS = 2      # spacing between requests when no rate-limit headers are sent
MAX_RESET_WAIT = 900         # a computed wait longer than this fails the run instead

DISCLAIMER = """\
================================================================================
 IMPORTANT - PLEASE READ BEFORE CONTINUING

 This is NOT an official Okta script. It is not created, reviewed, supported or
 endorsed by Okta. It is provided "as is", without warranty of any kind.

 You use this script AT YOUR OWN RISK. You are responsible for reviewing it,
 for testing it in a non-production org first, and for the handling of the
 output file, which contains user identifiers (e.g. login names, IP addresses).

 The script only READS the System Log (GET /api/v1/logs). It makes no changes
 to your Okta org.
================================================================================
"""

CSV_COLUMNS = [
    "published_utc", "channel", "event_type", "outcome", "outcome_reason",
    "counted_as_sent", "actor_login", "target_user_login", "client_ip",
    "client_country", "transaction_id", "event_id", "display_message",
]

log = logging.getLogger("okta_sms_usage")


class OktaError(Exception):
    """Any Okta/API/network failure we want to report cleanly (exit 1)."""


class ConfigError(Exception):
    """Bad user-supplied configuration (exit 2)."""


# ------------------------------- logging -----------------------------------

class _RedactFilter(logging.Filter):
    """Last line of defence: scrub the API token and SSWS/Bearer values from every record,
    even if a future code path accidentally formats them into a message."""
    _pat = re.compile(r"(SSWS|Bearer)\s+[A-Za-z0-9_\-.]+", re.I)
    secrets = set()

    def filter(self, record):
        msg = record.getMessage()
        for sec in self.secrets:
            if sec:
                msg = msg.replace(sec, "***REDACTED***")
        record.msg, record.args = self._pat.sub(r"\1 ***REDACTED***", msg), None
        return True


def setup_logging(log_file, verbose):
    """Console (stderr) at INFO, or DEBUG with --verbose; rotating file always at DEBUG so a
    failed run can be diagnosed afterwards."""
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    redact = _RedactFilter()
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    console.addFilter(redact)
    log.addHandler(console)
    if log_file:
        try:
            fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000,
                                                      backupCount=5, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
            fh.addFilter(redact)
            log.addHandler(fh)
        except OSError as e:  # an unwritable log location must not stop the export
            log.warning("cannot open log file %s (%s); continuing with console logging only",
                        log_file, e)


# ------------------------------- helpers -----------------------------------

def parse_date(value, label):
    """YYYY-MM-DD -> timezone-aware UTC midnight."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ConfigError(f"{label} must be YYYY-MM-DD, got {value!r}")


def iso(dt):
    """Format as the ISO 8601 UTC timestamp the System Log API expects."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def normalise_org_url(value):
    """Accept 'example.okta.com' or a full https URL; reject paths and plain http."""
    value = value.strip().rstrip("/")
    if not value:
        raise ConfigError("Okta org URL is required")
    if not re.match(r"^https?://", value):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https":
        raise ConfigError("the org URL must use https")
    if not parsed.netloc or parsed.path not in ("", "/"):
        raise ConfigError("org URL must be just the host, e.g. https://example.okta.com")
    # The admin console host (-admin) does not serve the API.
    host = parsed.netloc.replace("-admin.", ".")
    return f"https://{host}"


def resolve_window(start_s, end_s):
    """Turn the configured dates into an [start, end) UTC window. End date is inclusive."""
    now = datetime.now(timezone.utc)
    end = parse_date(end_s, "end date") + timedelta(days=1) if end_s else now
    end = min(end, now)  # never ask for the future
    start = parse_date(start_s, "start date") if start_s else now - timedelta(days=RETENTION_DAYS)
    if start >= end:
        raise ConfigError("start date must be before end date")
    if start < now - timedelta(days=RETENTION_DAYS):
        log.warning("the System Log only retains %d days; anything older than that will not "
                    "be returned.", RETENTION_DAYS)
    return start, end


def build_filter(event_types):
    return " or ".join(f'eventType eq "{t}"' for t in event_types)


def build_url(org_url, start, end, event_types):
    params = urllib.parse.urlencode({
        "since": iso(start), "until": iso(end), "sortOrder": "ASCENDING",
        "limit": PAGE_LIMIT, "filter": build_filter(event_types),
    })
    return f"{org_url}/api/v1/logs?{params}"


def parse_next_link(link_headers):
    """Return the rel="next" URL from the Link header(s), or None on the last page."""
    for header in link_headers:
        for part in header.split(","):
            m = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
            if m:
                return m.group(1)
    return None


# --------------------------- rate-limit handling ---------------------------

def _int_header(headers, name):
    value = headers.get(name)
    return int(value) if value is not None and str(value).strip().isdigit() else None


def seconds_until_reset(headers):
    """Seconds until the rate-limit window resets, from X-Rate-Limit-Reset (epoch seconds).
    Uses the server's Date header as 'now' so a skewed local clock can't cause a wrong wait."""
    reset = _int_header(headers, "X-Rate-Limit-Reset")
    if reset is None:
        return None
    now = time.time()
    date = headers.get("Date")
    if date:
        try:
            now = email.utils.parsedate_to_datetime(date).timestamp()
        except (TypeError, ValueError):
            pass
    return max(0, reset - int(now)) + RESET_GUARD_SECS  # reset is the first second of the NEW window


def reserve(limit):
    """ceil(RATE_LIMIT_RESERVE_PERCENT% x limit), in integer arithmetic."""
    return -(-limit * RATE_LIMIT_RESERVE_PERCENT // 100)


def pause_for(seconds, why):
    """Sleep out a rate-limit wait. A wait longer than MAX_RESET_WAIT fails the run (rather
    than sleeping for ages or pressing on into a 429)."""
    if seconds > MAX_RESET_WAIT:
        raise OktaError(f"rate-limit reset is {seconds}s away (more than {MAX_RESET_WAIT}s); "
                        "stopping. Try again later.")
    log.warning("%s; waiting %ds", why, seconds)
    time.sleep(seconds)


def check_rate_limit(headers):
    """Read the rate-limit headers after every response. Once only the 10% reserve is left,
    wait for the window to reset so the org's budget is never exhausted by this script."""
    limit = _int_header(headers, "X-Rate-Limit-Limit")
    remaining = _int_header(headers, "X-Rate-Limit-Remaining")
    if limit is None or remaining is None:
        log.debug("no rate-limit headers on response; spacing requests %ds apart",
                  BLIND_INTERVAL_SECS)
        time.sleep(BLIND_INTERVAL_SECS)
        return
    wait = seconds_until_reset(headers)
    log.debug("rate limit: %d/%d remaining (reserve %d), resets in %ss",
              remaining, limit, reserve(limit), wait)
    if limit > 0 and remaining <= reserve(limit):
        if wait is None:  # reset time missing/unusable
            wait = DEFAULT_WINDOW_SECS + RESET_GUARD_SECS
        used = limit - remaining
        pause_for(wait, "%d%% of the rate-limit window used (%d of %d calls)"
                  % (used * 100 // limit, used, limit))


def api_get(url, token, org_url):
    """GET one System Log page. Returns (json_body, Link headers). Retries 429/5xx/network."""
    # Never send the token anywhere except the org it was configured for.
    if not url.startswith(org_url + "/"):
        raise OktaError(f"refusing to follow a link outside the org: {url}")
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"SSWS {token}",
        "User-Agent": f"okta-sms-usage-script/{SCRIPT_VERSION} (unofficial)",
    })
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.debug("GET %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = json.load(resp)
                check_rate_limit(resp.headers)
                return body, resp.headers.get_all("Link") or []
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES:
                wait = seconds_until_reset(e.headers) or DEFAULT_WINDOW_SECS + RESET_GUARD_SECS
                pause_for(wait, "rate limited (HTTP 429, attempt %d/%d)" % (attempt, MAX_RETRIES))
                continue
            if e.code in (500, 502, 503, 504) and attempt < MAX_RETRIES:
                log.warning("HTTP %s from Okta; retrying in %ds (attempt %d/%d)",
                            e.code, 2 ** attempt, attempt, MAX_RETRIES)
                time.sleep(2 ** attempt)
                continue
            body = e.read().decode("utf-8", "replace")[:300]
            hint = {401: " (token invalid, expired or not for this org)",
                    403: " (token lacks permission to read the System Log)"}.get(e.code, "")
            raise OktaError(f"HTTP {e.code} from Okta{hint}: {body}")
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                raise OktaError(
                    "TLS certificate verification failed (%s). On macOS with python.org Python "
                    "run 'Install Certificates.command' in /Applications/Python 3.x/. Behind a "
                    "TLS-inspecting proxy, set SSL_CERT_FILE to your corporate CA bundle."
                    % e.reason)
            if attempt < MAX_RETRIES:
                log.warning("network error (%s); retrying in %ds (attempt %d/%d)",
                            e.reason, 2 ** attempt, attempt, MAX_RETRIES)
                time.sleep(2 ** attempt)
                continue
            raise OktaError(f"could not reach {org_url}: {e.reason}")
    raise OktaError("retries exhausted")


# ------------------------------- export ------------------------------------

def fetch_events(org_url, token, start, end, event_types):
    """Yield every matching event, following Link rel="next" until a page comes back empty."""
    url = build_url(org_url, start, end, event_types)
    page = 0
    while url:
        events, links = api_get(url, token, org_url)
        page += 1
        log.info("page %d: %d events", page, len(events))
        yield from events
        url = parse_next_link(links) if events else None


def to_row(ev, include_debug):
    """Flatten one System Log event into a CSV row."""
    outcome = ev.get("outcome") or {}
    actor = ev.get("actor") or {}
    client = ev.get("client") or {}
    geo = client.get("geographicalContext") or {}
    targets = [t.get("alternateId") or "" for t in ev.get("target") or []
               if t.get("type") == "User"]
    etype = ev.get("eventType", "")
    result = outcome.get("result", "")
    row = {
        "published_utc": ev.get("published", ""),
        "channel": "SMS" if etype.startswith("system.sms.") else "Voice",
        "event_type": etype,
        "outcome": result,
        "outcome_reason": outcome.get("reason") or "",
        # DENY = blocked by Okta (not delivered); count everything else.
        "counted_as_sent": "no" if result == "DENY" else "yes",
        "actor_login": actor.get("alternateId") or "",
        "target_user_login": ";".join(targets),
        "client_ip": client.get("ipAddress") or "",
        "client_country": geo.get("country") or "",
        "transaction_id": (ev.get("transaction") or {}).get("id", ""),
        "event_id": ev.get("uuid", ""),
        "display_message": ev.get("displayMessage") or "",
    }
    if include_debug:
        row["debug_data"] = json.dumps((ev.get("debugContext") or {}).get("debugData") or {},
                                       sort_keys=True)
    return row


def run_export(org_url, token, start, end, output, include_voice, include_debug):
    """Fetch, de-duplicate and write events to CSV, then print a summary."""
    types = SMS_EVENT_TYPES + (VOICE_EVENT_TYPES if include_voice else [])
    columns = CSV_COLUMNS + (["debug_data"] if include_debug else [])
    log.info("Querying %s from %s to %s (%s)", org_url, iso(start), iso(end),
             "SMS + Voice" if include_voice else "SMS only")
    by_month, by_type = Counter(), Counter()
    total = denied = 0
    seen = set()
    # utf-8-sig so Excel opens the CSV with correct encoding.
    with open(output, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for ev in fetch_events(org_url, token, start, end, types):
            if ev.get("uuid") in seen:  # guard against overlap between pages
                log.debug("skipping duplicate event %s", ev.get("uuid"))
                continue
            seen.add(ev.get("uuid"))
            row = to_row(ev, include_debug)
            writer.writerow(row)
            total += 1
            if row["counted_as_sent"] == "yes":
                by_month[(row["published_utc"][:7], row["channel"])] += 1
                by_type[row["event_type"]] += 1
            else:
                denied += 1
    log.info("Done: %d events written to %s (sent=%d, blocked/DENY=%d)",
             total, os.path.abspath(output), total - denied, denied)
    print(f"\nWrote {total} events to {os.path.abspath(output)}")
    print(f"  counted as sent: {total - denied}   blocked by Okta (DENY): {denied}")
    if by_type:
        print("\nSent, by event type:")
        for t, n in sorted(by_type.items()):
            print(f"  {n:>8}  {t}")
        print("\nSent, by month:")
        for (month, channel), n in sorted(by_month.items()):
            print(f"  {month}  {channel:<5} {n:>8}")
    else:
        print("No telephony events found in this window.")
    return total


def print_dry_run(org_url, start, end, output, include_voice, include_debug):
    """Show exactly what a real run would do. Makes no network calls and writes no CSV."""
    types = SMS_EVENT_TYPES + (VOICE_EVENT_TYPES if include_voice else [])
    print("\nDRY RUN - nothing will be sent to Okta and no CSV will be written.\n")
    print(f"  Org            : {org_url}")
    print(f"  Window (UTC)   : {iso(start)}  ->  {iso(end)}")
    print(f"  Event types    : {len(types)} ({'SMS + Voice' if include_voice else 'SMS only'})")
    for t in types:
        print(f"                   - {t}")
    print(f"  Output CSV     : {os.path.abspath(output)}"
          f"{'  (already exists - would be overwritten)' if os.path.exists(output) else ''}")
    print(f"  Debug column   : {'yes' if include_debug else 'no'}")
    print(f"  Request        : GET {build_url(org_url, start, end, types)}")
    print("                   Authorization: SSWS <token not shown>\n")
    log.info("dry run complete; no requests made")


# ------------------------------ entry point --------------------------------

def confirm_interactive():
    print(DISCLAIMER)
    answer = input('Type "I AGREE" to accept and continue (anything else exits): ').strip()
    if answer != "I AGREE":
        raise SystemExit("Not accepted - exiting without making any requests.")


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def main(argv=None):
    p = argparse.ArgumentParser(description="Export Okta SMS usage from the System Log to CSV "
                                            "(UNOFFICIAL script - use at your own risk).")
    p.add_argument("--non-interactive", action="store_true",
                   help="never prompt; take everything from flags, env vars and the config block")
    p.add_argument("--accept-risk", action="store_true",
                   help="required with --non-interactive: confirms you accept the disclaimer")
    p.add_argument("--dry-run", action="store_true",
                   help="validate and show what would be requested; no network, no CSV")
    p.add_argument("--org-url", help="https://example.okta.com (env: OKTA_ORG_URL)")
    p.add_argument("--start", help="start date YYYY-MM-DD, UTC (default: 90 days ago)")
    p.add_argument("--end", help="end date YYYY-MM-DD, UTC, inclusive (default: now)")
    p.add_argument("--output", help=f"CSV path (default: {OUTPUT_CSV})")
    p.add_argument("--include-voice", action="store_true", default=None,
                   help="also export voice-call events")
    p.add_argument("--include-debug-data", action="store_true", default=None,
                   help="add raw debugData column (may contain phone numbers)")
    p.add_argument("--log-file", help=f"log file path (default: {LOG_FILE or 'disabled'})")
    p.add_argument("--verbose", action="store_true", help="DEBUG output on the console")
    a = p.parse_args(argv)

    setup_logging(a.log_file if a.log_file is not None else LOG_FILE, a.verbose)
    log.info("okta_sms_usage %s starting (python %s, %s)", SCRIPT_VERSION,
             sys.version.split()[0], sys.platform)

    interactive = not a.non_interactive
    if interactive and not sys.stdin.isatty():
        log.error("no terminal available; use --non-interactive --accept-risk")
        return 2

    # The disclaimer is always shown before anything runs.
    if interactive:
        confirm_interactive()
    else:
        print(DISCLAIMER, file=sys.stderr)
        if not a.accept_risk:
            log.error("--non-interactive requires --accept-risk to confirm you have read the "
                      "disclaimer. Exiting without making any requests.")
            return 2
    log.info("disclaimer accepted (%s)", "typed confirmation" if interactive else "--accept-risk")

    org = a.org_url or os.environ.get("OKTA_ORG_URL") or ORG_URL
    token = os.environ.get("OKTA_API_TOKEN") or API_TOKEN
    start_s = a.start if a.start is not None else START_DATE
    end_s = a.end if a.end is not None else END_DATE
    output = a.output or OUTPUT_CSV
    voice = INCLUDE_VOICE if a.include_voice is None else a.include_voice
    debug = INCLUDE_DEBUG_DATA if a.include_debug_data is None else a.include_debug_data

    try:
        if interactive:
            org = ask("Okta org URL (e.g. https://example.okta.com)", org)
            if not token and not a.dry_run:
                token = getpass.getpass("Okta API token (input hidden): ").strip()
            start_s = ask("Start date YYYY-MM-DD (blank = 90 days ago)", start_s)
            end_s = ask("End date YYYY-MM-DD, inclusive (blank = now)", end_s)
            output = ask("Output CSV file", output)
            voice = ask("Include voice calls too? (y/N)", "y" if voice else "n").lower().startswith("y")

        _RedactFilter.secrets.add(token)
        org = normalise_org_url(org)
        start, end = resolve_window(start_s, end_s)
        if not token and not a.dry_run:
            raise ConfigError("no API token. Set the OKTA_API_TOKEN environment variable.")

        if a.dry_run:
            print_dry_run(org, start, end, output, voice, debug)
            if not token:
                log.warning("no API token is set; a real run would need OKTA_API_TOKEN")
            return 0

        run_export(org, token, start, end, output, voice, debug)
        return 0
    except ConfigError as e:
        log.error("configuration error: %s", e)
        return 2
    except OktaError as e:
        log.error("%s", e)
        return 1
    except OSError as e:
        log.error("file error (%s): %s", output, e)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.error("interrupted by user") if log.handlers else None
        sys.exit(130)
