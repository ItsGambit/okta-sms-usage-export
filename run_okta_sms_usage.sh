#!/usr/bin/env bash
# UNOFFICIAL launcher (use at your own risk). Checks for Python 3.8+ with SSL,
# offers to install it if missing (asks first), then runs okta_sms_usage.py
# with whatever arguments you pass. macOS and Linux.
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_python() {
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys,ssl; sys.exit(0 if sys.version_info>=(3,8) else 1)' >/dev/null 2>&1; then
      echo "$c"; return 0
    fi
  done
  return 1
}

install_python() {
  local sudo=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo="sudo"
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then cmd="brew install python"
      else echo "Homebrew not found. Install Python from https://www.python.org/downloads/ and re-run."; return 1; fi ;;
    Linux)
      if   command -v apt-get >/dev/null 2>&1; then cmd="$sudo apt-get update && $sudo apt-get install -y python3 ca-certificates"
      elif command -v dnf     >/dev/null 2>&1; then cmd="$sudo dnf install -y python3 ca-certificates"
      elif command -v yum     >/dev/null 2>&1; then cmd="$sudo yum install -y python3 ca-certificates"
      elif command -v zypper  >/dev/null 2>&1; then cmd="$sudo zypper install -y python3 ca-certificates"
      elif command -v pacman  >/dev/null 2>&1; then cmd="$sudo pacman -S --noconfirm python ca-certificates"
      elif command -v apk     >/dev/null 2>&1; then cmd="$sudo apk add python3 ca-certificates"
      else echo "No known package manager. Install Python 3.8+ from https://www.python.org/downloads/."; return 1; fi ;;
    *) echo "Unsupported OS: $(uname -s)"; return 1 ;;
  esac
  echo "Python 3.8+ (with SSL) was not found. Proposed install command:"
  echo "    $cmd"
  read -r -p "Run it now? [y/N] " ans </dev/tty
  case "$ans" in y|Y|yes|YES) eval "$cmd" ;; *) echo "Not installing. Install Python 3.8+ and re-run."; return 1 ;; esac
}

py="$(find_python)" || { install_python || exit 1; py="$(find_python)" || { echo "Python still not usable."; exit 1; }; }
echo "Using $($py --version 2>&1) at $(command -v "$py")" >&2
exec "$py" "$here/okta_sms_usage.py" "$@"
