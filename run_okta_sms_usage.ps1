# UNOFFICIAL launcher (use at your own risk). Windows PowerShell 5.1 or PowerShell 7.
# Checks for Python 3.8+ with SSL, offers to install it via winget if missing
# (asks first), then runs okta_sms_usage.py with whatever arguments you pass.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$check = 'import sys,ssl; sys.exit(0 if sys.version_info>=(3,8) else 1)'

function Find-Python {
    # Real interpreters only: skip the Microsoft Store "python.exe" alias stub.
    $candidates = @(
        @{ Exe = 'py';      Pre = @('-3') },
        @{ Exe = 'python';  Pre = @() },
        @{ Exe = 'python3'; Pre = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd -or $cmd.Source -like '*\WindowsApps\*') { continue }
        & $cmd.Source @($c.Pre) -c $check 2>$null
        if ($LASTEXITCODE -eq 0) { return @{ Exe = $cmd.Source; Pre = $c.Pre } }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host 'Python 3.8+ (with SSL) was not found.'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $cmdline = 'winget install -e --id Python.Python.3.12 --scope user'
        Write-Host "Proposed install command:`n    $cmdline"
        $ans = Read-Host 'Run it now? [y/N]'
        if ($ans -match '^(y|yes)$') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            # Refresh PATH for this session so the new install is found.
            $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                        [Environment]::GetEnvironmentVariable('Path','User')
            $py = Find-Python
        }
    } else {
        Write-Host 'winget not available. Install Python 3.8+ from https://www.python.org/downloads/ and re-run.'
    }
    if (-not $py) { Write-Host 'Python is not available; exiting.'; exit 1 }
}

Write-Host "Using $(& $py.Exe @($py.Pre) --version 2>&1) at $($py.Exe)"
& $py.Exe @($py.Pre) (Join-Path $here 'okta_sms_usage.py') @args
exit $LASTEXITCODE
