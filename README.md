# Okta SMS Usage Export

Exports SMS (and optionally voice call) events from your Okta tenant's System Log to a CSV file, with a summary of messages sent per month.

> **This is NOT an official Okta script.**
> It is not created, reviewed, supported or endorsed by Okta. It is provided "as is", without warranty of any kind. **You use it entirely at your own risk.** Review the script before running it, and try it in a non-production org first. You are responsible for how you store and share the output file, which contains user identifiers such as login names and IP addresses.

The script shows this notice every time it runs, before it does anything else, and will not continue until you accept it.

---

## Contents

1. [What it does](#what-it-does)
2. [What is in the package](#what-is-in-the-package)
3. [Requirements](#requirements)
4. [Before you start: the API token](#before-you-start-the-api-token)
5. [Quick start](#quick-start)
6. [Configuration](#configuration)
7. [Command-line options](#command-line-options)
8. [Interactive mode](#interactive-mode)
9. [Non-interactive mode](#non-interactive-mode)
10. [Dry run](#dry-run)
11. [Understanding the output](#understanding-the-output)
12. [Rate limiting](#rate-limiting)
13. [Logging](#logging)
14. [Exit codes](#exit-codes)
15. [Security and privacy](#security-and-privacy)
16. [Limitations](#limitations)
17. [Troubleshooting](#troubleshooting)
18. [Scheduling regular exports](#scheduling-regular-exports)
19. [Reference: Okta documentation](#reference-okta-documentation)

---

## What it does

The script reads your Okta System Log for the events Okta records each time it sends an SMS message or places a voice call, and writes one CSV row per event. When it finishes it prints the number of messages sent, broken down by event type and by month.

- **Read-only.** It only issues `GET` requests to the System Log API (`/api/v1/logs`). It never creates, changes or deletes anything in your org.
- **Your dates.** Choose the date range in the script, on the command line, or when prompted.
- **Two ways to run.** Interactive (it asks you questions) or non-interactive (for automation).
- **Dry run.** Check exactly what would be requested before anything is sent to Okta.
- **Careful with your org.** It watches Okta's rate-limit headers and pauses before it uses up the org's request budget.

### Events counted

| Channel | Event types |
|---|---|
| SMS | `system.sms.send_account_unlock_message`, `system.sms.send_factor_verify_message`, `system.sms.send_okta_push_verify_message`, `system.sms.send_password_reset_message`, `system.sms.send_phone_verification_message` |
| Voice (only with `--include-voice`) | `system.voice.send_account_unlock_call`, `system.voice.send_call`, `system.voice.send_mfa_challenge_call`, `system.voice.send_password_reset_call`, `system.voice.send_phone_verification_call` |

Okta also records delivery receipts (`system.sms.receive_status` and `system.voice.receive_status`), one per send event. They describe the outcome of a message that was already counted, so the script **does not** export or count them.

Okta marks a send event with the outcome `DENY` when Okta itself blocked the message. These rows are written to the CSV but flagged `counted_as_sent = no` and reported separately from messages sent.

---

## What is in the package

| File | Purpose |
|---|---|
| `okta_sms_usage.py` | The script. Python standard library only, nothing to install with `pip`. |
| `run_okta_sms_usage.sh` | Optional launcher for macOS and Linux. Checks that Python is installed and offers to install it if not. |
| `run_okta_sms_usage.ps1` | Optional launcher for Windows. Does the same using `winget`. |
| `README.md` | This document. |

You can run `okta_sms_usage.py` directly if Python 3.8 or newer is already installed. The launchers are a convenience.

---

## Requirements

- **Python 3.8 or newer** with SSL support (any standard build from python.org or your OS package manager has it).
- **Network access** over HTTPS (port 443) from the machine running the script to your Okta org, for example `https://your-org.okta.com`.
- **An Okta API token** that can read the System Log (see below).
- Windows, macOS or Linux.

No third-party Python packages are used.

### Checking for and installing Python

The launchers check for a suitable Python and, if none is found, **show you the install command and ask before running it**. They never install anything without your confirmation.

| Platform | How Python is installed by the launcher |
|---|---|
| Windows | `winget install -e --id Python.Python.3.12 --scope user` |
| macOS | `brew install python` (requires Homebrew; otherwise download from python.org) |
| Debian / Ubuntu | `apt-get install python3 ca-certificates` |
| RHEL / Fedora / Amazon Linux | `dnf` or `yum install python3 ca-certificates` |
| openSUSE | `zypper install python3 ca-certificates` |
| Arch | `pacman -S python ca-certificates` |
| Alpine | `apk add python3 ca-certificates` |

If your platform is not listed, or you prefer to install it yourself, get Python from <https://www.python.org/downloads/> and run the script directly.

---

## Before you start: the API token

The script authenticates with an Okta API token (sent as `Authorization: SSWS <token>`).

1. Sign in to the Okta Admin Console as an administrator.
2. Create an API token (**Security → API → Tokens**). Okta ties a token to the permissions of the admin who creates it, so create it as, or for, an administrator whose role can view the System Log. A read-only administrator role is sufficient for a read-only export and is recommended; do not use a more privileged account than necessary.
3. Copy the token when it is shown. Okta displays it only once.
4. Store it somewhere safe. Revoke it when you no longer need it.

**Give the token to the script through the `OKTA_API_TOKEN` environment variable, or type it at the hidden prompt in interactive mode.** There is deliberately no `--token` command-line option, because command lines are visible to other users on the machine and are saved in shell history.

---

## Quick start

### Interactive (easiest)

```
python okta_sms_usage.py
```

You will see the disclaimer, then be asked for your org URL, API token (hidden), start and end dates, output file, and whether to include voice calls.

### Non-interactive

macOS / Linux:

```bash
export OKTA_API_TOKEN='your-token-here'
python3 okta_sms_usage.py --non-interactive --accept-risk \
    --org-url https://your-org.okta.com \
    --start 2026-09-01 --end 2026-11-01 \
    --output sms_usage.csv
```

Windows PowerShell:

```powershell
$env:OKTA_API_TOKEN = 'your-token-here'
python okta_sms_usage.py --non-interactive --accept-risk `
    --org-url https://your-org.okta.com `
    --start 2026-09-01 --end 2026-11-01 `
    --output sms_usage.csv
```

Windows Command Prompt:

```bat
set OKTA_API_TOKEN=your-token-here
python okta_sms_usage.py --non-interactive --accept-risk --org-url https://your-org.okta.com --start 2026-09-01 --end 2026-11-01 --output sms_usage.csv
```

> On Windows, if `python` is not found, try `py -3` instead. On macOS and Linux the command is usually `python3`.

### Using the launchers

macOS / Linux:

```bash
bash run_okta_sms_usage.sh --dry-run --org-url https://your-org.okta.com --non-interactive --accept-risk
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_okta_sms_usage.ps1
```

Any arguments you give a launcher are passed straight to the script.

**Tip:** run with `--dry-run` first to check your settings.

---

## Configuration

There are three ways to supply settings. When the same setting is given more than once, the order of precedence is:

**command-line option → environment variable → value in the script**

### Editing the script

Near the top of `okta_sms_usage.py` is a clearly marked block:

```python
# ============================== CONFIGURATION ==============================
ORG_URL = ""            # e.g. "https://example.okta.com"
API_TOKEN = ""          # Prefer the OKTA_API_TOKEN environment variable. Never commit a token.
START_DATE = ""         # "YYYY-MM-DD" (UTC, inclusive). Blank = 90 days ago.
END_DATE = ""           # "YYYY-MM-DD" (UTC, inclusive). Blank = now.
OUTPUT_CSV = "okta_sms_usage.csv"
INCLUDE_VOICE = False   # True also exports voice-call events
INCLUDE_DEBUG_DATA = False  # True adds raw debugContext.debugData
LOG_FILE = "okta_sms_usage.log"  # rotating log file; "" disables file logging
# ===========================================================================
```

| Setting | Meaning |
|---|---|
| `ORG_URL` | Your Okta org, such as `https://your-org.okta.com`. Custom domains and `oktapreview.com` orgs work. Enter just the host, with no path. If you paste an Admin Console address containing `-admin`, it is corrected automatically. Plain `http://` is rejected. |
| `API_TOKEN` | Supported for convenience, but **avoid saving a token in a file.** Use `OKTA_API_TOKEN` instead. |
| `START_DATE` | First day to include, `YYYY-MM-DD`, interpreted as 00:00 UTC. Blank means 90 days ago. |
| `END_DATE` | Last day to include, `YYYY-MM-DD`. **Inclusive**: `2026-11-01` includes all of 1 November UTC. Blank means now. |
| `OUTPUT_CSV` | Where to write the CSV. An existing file is overwritten. |
| `INCLUDE_VOICE` | `True` to include voice-call events as well as SMS. |
| `INCLUDE_DEBUG_DATA` | `True` adds a `debug_data` column with Okta's raw debug details for each event. See [Security and privacy](#security-and-privacy). |
| `LOG_FILE` | Path of the log file. Set to `""` to turn file logging off. |

### Environment variables

| Variable | Meaning |
|---|---|
| `OKTA_API_TOKEN` | The API token (recommended way to supply it). |
| `OKTA_ORG_URL` | The org URL. |

### Dates

- Dates are always **UTC**.
- The end date is inclusive. The end of the range is never later than "now".
- The start date must be before the end date.
- Okta keeps System Log events for **90 days**. A start date older than that produces a warning, and events older than the retention period cannot be returned.

---

## Command-line options

| Option | Description |
|---|---|
| `--non-interactive` | Never prompt. Settings come from options, environment variables and the configuration block. |
| `--accept-risk` | Required with `--non-interactive`. Confirms that you have read and accept the disclaimer. Without it, the script exits without contacting Okta. |
| `--dry-run` | Validate settings and show what would be requested. Sends nothing to Okta and writes no CSV. |
| `--org-url URL` | Okta org URL (or `OKTA_ORG_URL`). |
| `--start YYYY-MM-DD` | First day to include (UTC). |
| `--end YYYY-MM-DD` | Last day to include (UTC, inclusive). |
| `--output FILE` | CSV output path. |
| `--include-voice` | Also export voice-call events. |
| `--include-debug-data` | Add the raw `debug_data` column. |
| `--log-file FILE` | Log file path. |
| `--verbose` | Show DEBUG-level detail on the console (the log file always records it). |
| `-h`, `--help` | Show help. |

---

## Interactive mode

Run the script with no arguments from a terminal:

1. The disclaimer is displayed. Type `I AGREE` to continue; anything else exits without making any request.
2. You are prompted for each setting. Pressing Enter accepts the value shown in square brackets, which comes from the configuration block or environment.
3. The API token is read with hidden input (skipped if `OKTA_API_TOKEN` is already set).
4. The export runs and a summary is printed.

Interactive mode needs a real terminal. If the script is started without one (for example from a scheduler), it stops and tells you to use `--non-interactive --accept-risk`.

---

## Non-interactive mode

Use `--non-interactive` for scripts and scheduled jobs. Nothing is ever prompted for, so every required setting must already be available:

- **Required:** the org URL and the token (`OKTA_API_TOKEN`).
- **Required:** `--accept-risk`. This is your confirmation of the disclaimer, which is still printed.
- **Optional:** dates, output path, voice, and the rest fall back to the configuration block, or to the defaults.

Success or failure is reported through the [exit code](#exit-codes).

---

## Dry run

```
python okta_sms_usage.py --non-interactive --accept-risk --org-url https://your-org.okta.com --dry-run
```

A dry run:

- checks the org URL, dates and other settings, exactly as a real run does;
- prints the org, the UTC date window, every event type that would be requested, the CSV path (and whether it would overwrite an existing file), and the exact request URL, with the token shown as `<token not shown>`;
- **does not contact Okta** and **does not create or change the CSV**;
- does not need an API token, although it warns if none is set.

It is a good way to confirm your date range and settings before a first real run.

---

## Understanding the output

### The CSV file

One row per event, oldest first, UTF-8 with a byte-order mark so that Excel opens it correctly.

| Column | Description |
|---|---|
| `published_utc` | When Okta recorded the event (UTC, ISO 8601). |
| `channel` | `SMS` or `Voice`. |
| `event_type` | The Okta event type, for example `system.sms.send_factor_verify_message`. It tells you why the message was sent (MFA challenge, phone verification, password reset, account unlock, Okta Verify activation). |
| `outcome` | Okta's result for the event, such as `SUCCESS`, `FAILURE`, `DENY`. |
| `outcome_reason` | Okta's stated reason, when one is given. |
| `counted_as_sent` | `no` when `outcome` is `DENY` (Okta blocked the message), otherwise `yes`. |
| `actor_login` | The account that triggered the event, when Okta records one. |
| `target_user_login` | The user the message was for. |
| `client_ip` | The IP address of the client that triggered it. |
| `client_country` | The country Okta derived for that client. |
| `transaction_id` | Okta's transaction identifier, useful for tying related events together. |
| `event_id` | Okta's unique event identifier. Duplicates are removed on this value. |
| `display_message` | Okta's human-readable description of the event. |
| `debug_data` | *Only with `--include-debug-data`.* Okta's raw debug data for the event, as JSON. |

Any text that comes from your tenant is written exactly as Okta returned it. Treat the CSV as untrusted input if you open it in a spreadsheet.

### The console summary

```
Wrote 1234 events to /path/to/sms_usage.csv
  counted as sent: 1200   blocked by Okta (DENY): 34

Sent, by event type:
       900  system.sms.send_factor_verify_message
       300  system.sms.send_phone_verification_message

Sent, by month:
  2026-09  SMS        600
  2026-10  SMS        600
```

If nothing matched, the script says "No telephony events found in this window" and writes a CSV containing only the header row.

### How to interpret the numbers

- The script counts **events in your System Log**. Each send event is one message or call attempt recorded by Okta.
- For contractual or billing purposes, always rely on your Okta agreement and Okta's own reporting. The Telephony Usage Report in the Admin Console (**Reports → Reports → Telephony → Usage Report**) is a useful cross-check for the same period.
- Totals for a period can differ from other reports if their date ranges, time zones or filters differ. This script always uses UTC.

---

## Rate limiting

Okta limits how many API requests an org can make in a window of time, and that budget is shared by everything in your org that calls the same API (for example, a SIEM connector reading the System Log). The script is built to leave room for those other users.

After **every** response it reads Okta's `X-Rate-Limit-Limit`, `X-Rate-Limit-Remaining` and `X-Rate-Limit-Reset` headers and applies these rules:

- **90% rule.** It uses at most 90% of the limit. Once only the last 10% (rounded up) remains, it **waits** until the window resets, then carries on. For example, with a limit of 100 it pauses after 90 requests; with a limit of 60, after 54.
- **Server time.** The wait is calculated against the time reported by Okta's servers, so an incorrect clock on your machine cannot cause a wrong wait. One extra second is added as a safety margin.
- **No headers?** If a response carries no rate-limit headers, the script spaces requests 2 seconds apart.
- **HTTP 429 is a safety net.** If Okta still answers "too many requests", the script waits for the reset and retries.
- **Long waits.** If the reset is more than 15 minutes away, the script stops with an error rather than waiting indefinitely.

The script never exceeds the limits on how often it may call Okta, but a large export over many pages will take longer because of the pauses. This is expected. Pause and rate-limit messages are shown on the console and written to the log.

---

## Logging

Logging is set up for both quick checks and later troubleshooting.

| Destination | Level | Notes |
|---|---|---|
| Console (stderr) | `INFO` | `DEBUG` as well with `--verbose`. |
| Log file (default `okta_sms_usage.log`) | `DEBUG` always | Rotates at 1 MB, keeping 5 older files, so it cannot grow without limit. |

What is logged: the script version and Python version, that the disclaimer was accepted and how, each request made, the rate-limit values, each page received, waits and retries, warnings, errors, and the final totals.

What is **never** logged: the API token. As an extra safeguard, anything in a log message that looks like an `SSWS` or `Bearer` credential, or that matches the token, is replaced with `***REDACTED***`.

The log does contain your org URL and the request URLs. Check it before sharing it with anyone.

If the log file cannot be created (for example, a read-only folder), the script warns you and carries on with console logging only. Use `--log-file` to choose another location, or set `LOG_FILE = ""` to turn file logging off.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (or a successful dry run). |
| `1` | Runtime error: Okta or network problem, rejected token, or a file could not be written. |
| `2` | Configuration error: bad date, bad org URL, missing token, missing `--accept-risk`, or no terminal in interactive mode. |
| `130` | Interrupted with Ctrl+C. |

---

## Security and privacy

- **Read-only.** Only `GET` requests are sent.
- **Your org only.** The token is sent only to the org URL you configured. If Okta ever returned a paging link pointing anywhere else, the script refuses to follow it.
- **HTTPS only.** `http://` org URLs are rejected, and TLS certificates are verified.
- **Token handling.** The token is read from the environment or a hidden prompt, never from the command line, and is never written to the log or the CSV. If you save a token in the script's `API_TOKEN` setting, anyone who can read the file can use it, so prefer the environment variable. Revoke the token when you have finished.
- **The CSV contains personal data**, including login names and IP addresses. Store it securely, restrict who can read it, and delete it when it is no longer needed.
- **`--include-debug-data`** adds Okta's raw debug details for each event. These can include additional identifiers such as phone-related values. Leave it off unless you need it, and treat the resulting file as sensitive.
- **Proxy / TLS inspection.** If your network inspects TLS traffic, point Python at your organisation's CA bundle with the `SSL_CERT_FILE` environment variable.

---

## Limitations

- **90 days of history.** Okta retains System Log events for 90 days. To build a longer history, run the export regularly and keep the CSV files.
- **Events, not invoices.** The output is a record of events in your System Log. It is not a billing statement.
- **Telephony provider.** Okta records these events for messages that Okta's telephony service sends, and the same System Log events appear whether or not you have configured your own telephony provider. Check the Okta documentation for how your telephony configuration affects what is recorded.
- **Time zone.** All dates and times are UTC.
- **Large exports.** Very large date ranges involve many pages and are slowed by the rate-limit pauses.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `HTTP 401 ... token invalid, expired or not for this org` | The token is wrong, was revoked, has expired, or belongs to a different org. Create a new token and confirm the org URL. |
| `HTTP 403 ... token lacks permission to read the System Log` | The administrator who owns the token cannot view the System Log. Use a token from an admin whose role includes it, such as a read-only administrator. |
| `HTTP 400` | A date or filter value was not accepted. Check the dates are `YYYY-MM-DD` and run with `--dry-run` to see the request. |
| `could not reach https://...` | Network, firewall or proxy problem, or a mistyped org URL. Confirm you can open the org URL in a browser from the same machine. |
| `TLS certificate verification failed` | On macOS with python.org Python, run **Install Certificates.command** (in `/Applications/Python 3.x/`). Behind a TLS-inspecting proxy, set `SSL_CERT_FILE` to your corporate CA bundle. |
| `configuration error: ... must be YYYY-MM-DD` | Fix the date format. |
| `configuration error: start date must be before end date` | Swap or correct the dates. |
| `the System Log only retains 90 days` (warning) | Your start date is older than the retention period; only the last 90 days can be returned. |
| `no terminal available; use --non-interactive --accept-risk` | You ran in interactive mode without a terminal (for example from a scheduler). Use non-interactive mode. |
| `--non-interactive requires --accept-risk` | Add `--accept-risk` to confirm you accept the disclaimer. |
| `no API token` | Set the `OKTA_API_TOKEN` environment variable in the same shell session that runs the script. |
| The CSV only has a header row | No matching events in that window. Check the dates, whether you need `--include-voice`, and that the tenant actually sends SMS or voice through Okta. |
| Long pauses with `rate-limit window used` messages | Normal. See [Rate limiting](#rate-limiting). |
| `python` is not found on Windows | Try `py -3`, or use the PowerShell launcher. A Microsoft Store "python" shortcut that only opens the Store is not a real installation. |
| PowerShell says scripts are disabled | Run `powershell -ExecutionPolicy Bypass -File .\run_okta_sms_usage.ps1`. |
| `Permission denied` running the `.sh` launcher | Run it with `bash run_okta_sms_usage.sh`, or `chmod +x run_okta_sms_usage.sh` first. |

For anything else, run again with `--verbose` and review `okta_sms_usage.log`.

---

## Scheduling regular exports

Because Okta only keeps 90 days, a regular export lets you build a longer history. Run non-interactively, with the token coming from a secrets store or a protected environment variable, never written into the schedule itself.

**Linux / macOS (cron), 02:00 on the 1st of each month** (runs from the script's folder and writes a CSV named by month; `%` must be escaped in cron):

```cron
0 2 1 * * cd /opt/okta-sms-usage && OKTA_API_TOKEN="$(cat /etc/okta-sms/token)" python3 okta_sms_usage.py --non-interactive --accept-risk --org-url https://your-org.okta.com --output "exports/sms_$(date +\%Y-\%m).csv" >> run.log 2>&1
```

**Windows (Task Scheduler):** create a task that runs `powershell.exe` with arguments such as
`-NoProfile -ExecutionPolicy Bypass -File C:\okta-sms-usage\run_okta_sms_usage.ps1 --non-interactive --accept-risk --org-url https://your-org.okta.com`,
with the token supplied through a protected environment variable or secrets store for the task's account.

Adjust the paths to suit your environment. Each scheduled run covers the default window (the last 90 days) unless you pass `--start` and `--end`, so overlapping runs will contain overlapping events. Use `event_id` to de-duplicate if you combine files.

---

## Reference: Okta documentation

- Telephony usage: [How to identify telephony usage](https://support.okta.com/help/s/article/how-to-identify-telephony-usage?language=en_US)
- [How to view logs for SMS and voice calls](https://support.okta.com/help/s/article/How-To-View-Logs-For-SMS-and-Voice-Calls)
- [Telephony Usage Report](https://help.okta.com/oie/en-us/content/topics/reports/telephony-usage-report.htm)
- [System Log event types](https://developer.okta.com/docs/reference/api/event-types/)
- [System Log API](https://developer.okta.com/docs/reference/api/system-log/)
- [Rate limits](https://developer.okta.com/docs/reference/rate-limits/)

---

*This is an unofficial community script. It is not supported by Okta. Use it at your own risk.*
