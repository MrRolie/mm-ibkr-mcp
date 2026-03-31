<#
.SYNOPSIS
    Interactive configuration script for mm-ibkr-gateway Windows deployment.

.DESCRIPTION
    Collects required settings and generates config.json for the execution node.
    Creates directory structure for logs and the audit database, and a minimal .env for secrets.

.PARAMETER NonInteractive
    Skip prompts and use environment variables or defaults.

.EXAMPLE
    .\configure.ps1
#>

[CmdletBinding()]
param(
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

# Script paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Get-Item $ScriptDir).Parent.Parent.FullName
$EnvFile = Join-Path $RepoRoot ".env"
$SecretsDir = Join-Path $RepoRoot "secrets"
$ConfigFile = if ($env:MM_IBKR_CONFIG_PATH) { $env:MM_IBKR_CONFIG_PATH } else { "C:\ProgramData\mm-ibkr-gateway\config.json" }

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  mm-ibkr-gateway Configuration Wizard" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Helper function to prompt with default
function Read-HostWithDefault {
    param(
        [string]$Prompt,
        [string]$Default,
        [switch]$Required,
        [switch]$Secret
    )
    
    $displayDefault = if ($Default) { " [$Default]" } else { "" }
    $fullPrompt = "$Prompt$displayDefault"
    
    if ($Secret) {
        $secureValue = Read-Host -Prompt $fullPrompt -AsSecureString
        $value = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
        )
    } else {
        $value = Read-Host -Prompt $fullPrompt
    }
    
    if ([string]::IsNullOrWhiteSpace($value)) {
        if ($Required -and [string]::IsNullOrWhiteSpace($Default)) {
            Write-Host "This value is required." -ForegroundColor Red
            return Read-HostWithDefault -Prompt $Prompt -Default $Default -Required:$Required -Secret:$Secret
        }
        return $Default
    }
    return $value
}

# Detect LAN IP
function Get-LanIP {
    $lanIP = Get-NetIPAddress -AddressFamily IPv4 | 
        Where-Object { 
            $_.InterfaceAlias -notmatch "Loopback" -and 
            $_.IPAddress -notmatch "^169\." -and
            $_.IPAddress -notmatch "^127\." -and
            $_.PrefixOrigin -ne "WellKnown"
        } | 
        Select-Object -First 1 -ExpandProperty IPAddress
    return $lanIP
}

# Detect IBKR Gateway installation
function Find-IBKRGateway {
    $possiblePaths = @(
        "$env:USERPROFILE\Jts\ibgateway\*",
        "C:\Jts\ibgateway\*",
        "${env:ProgramFiles}\Jts\ibgateway\*",
        "${env:ProgramFiles(x86)}\Jts\ibgateway\*"
    )
    
    foreach ($pattern in $possiblePaths) {
        $found = Get-Item $pattern -ErrorAction SilentlyContinue | 
            Sort-Object Name -Descending | 
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }
    return $null
}


function Ensure-Tzdata {
    param([string]$VenvPath)

    $pipExe = Join-Path $VenvPath "Scripts\\pip.exe"
    if (-not (Test-Path $pipExe)) {
        return
    }

    & $pipExe show tzdata | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing tzdata (Windows time zone database)..." -ForegroundColor Yellow
        & $pipExe install tzdata
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: Failed to install tzdata; run window checks may fail." -ForegroundColor Yellow
        }
    }
}

# Step 1: Repository Path
Write-Host "Step 1: Repository Path" -ForegroundColor Yellow
Write-Host "Detected: $RepoRoot"
$confirmRepo = Read-HostWithDefault -Prompt "Use this path? (Y/n)" -Default "Y"
if ($confirmRepo -eq "n" -or $confirmRepo -eq "N") {
    $RepoRoot = Read-HostWithDefault -Prompt "Enter repository path" -Required
    $EnvFile = Join-Path $RepoRoot ".env"
    $SecretsDir = Join-Path $RepoRoot "secrets"
}
Write-Host ""

# Step 2: Network Configuration
Write-Host "Step 2: Network Configuration" -ForegroundColor Yellow
$detectedIP = Get-LanIP
Write-Host "Detected LAN IP: $detectedIP"

$defaultBindHost = "127.0.0.1"
$LanIP = Read-HostWithDefault -Prompt "API bind host (default ${defaultBindHost}; detected LAN IP: ${detectedIP})" -Default $defaultBindHost -Required
$AllowedIPs = Read-HostWithDefault -Prompt "Allowed remote IPs/CIDR for inbound API (comma-separated)" -Default "127.0.0.1"
$ApiPort = Read-HostWithDefault -Prompt "API Port" -Default "8000"
Write-Host ""

# Step 3: Storage Path
Write-Host "Step 3: Storage Configuration" -ForegroundColor Yellow
Write-Host "Default storage location: C:\ProgramData\mm-ibkr-gateway\storage" -ForegroundColor Gray
Write-Host "This location is recommended for Windows services." -ForegroundColor Gray
$defaultStoragePath = "C:\ProgramData\mm-ibkr-gateway\storage"
$StorageBasePath = Read-HostWithDefault -Prompt "Storage base path (logs, audit db)" -Default $defaultStoragePath -Required
Write-Host ""

# Step 4: IBKR Gateway Configuration
Write-Host "Step 4: IBKR Gateway Configuration" -ForegroundColor Yellow
$detectedGateway = Find-IBKRGateway
if ($detectedGateway) {
    Write-Host "Detected IBKR Gateway: $detectedGateway"
}
$GatewayPath = Read-HostWithDefault -Prompt "IBKR Gateway installation path" -Default $detectedGateway -Required
$IbkrGatewayHost = Read-HostWithDefault -Prompt "IBKR Gateway host (usually 127.0.0.1)" -Default "127.0.0.1"
$PaperPort = Read-HostWithDefault -Prompt "Paper trading port" -Default "4002"
Write-Host ""
Write-Host "Client ID guidance:" -ForegroundColor Gray
Write-Host "  - The long-lived gateway service should use the master client ID." -ForegroundColor Gray
Write-Host "  - If another direct client is the master, pick a different ID here." -ForegroundColor Gray
$PaperClientId = Read-HostWithDefault -Prompt "Paper client ID (master if gateway is master)" -Default "1"
$LiveGatewayPort = Read-HostWithDefault -Prompt "Live trading port" -Default "4001"
$LiveClientId = Read-HostWithDefault -Prompt "Live client ID (master if gateway is master)" -Default "777"
Write-Host ""

# Step 5: API + Logging Configuration
Write-Host "Step 5: API + Logging Configuration" -ForegroundColor Yellow
$ApiRequestTimeout = Read-HostWithDefault -Prompt "API request timeout (seconds)" -Default "30.0"
$LogLevel = (Read-HostWithDefault -Prompt "Log level (DEBUG, INFO, WARNING, ERROR)" -Default "INFO").ToUpper()
$LogFormat = (Read-HostWithDefault -Prompt "Log format (json or text)" -Default "json").ToLower()
$WatchdogLogDir = Read-HostWithDefault -Prompt "Watchdog log directory" -Default "C:\ProgramData\mm-ibkr-gateway\logs"
$enableAdminRestart = Read-HostWithDefault -Prompt "Enable /admin/restart endpoint? (y/N)" -Default "N"
$AdminRestartEnabled = if ($enableAdminRestart -eq "y" -or $enableAdminRestart -eq "Y") { $true } else { $false }
Write-Host ""

# Step 6: IBKR Credentials
Write-Host "Step 6: IBKR Credentials" -ForegroundColor Yellow
Write-Host "These credentials are for IBKR Gateway auto-login (paper trading)." -ForegroundColor Gray
Write-Host "WARNING: Credentials will be stored in jts.ini (plaintext - IBKR limitation)" -ForegroundColor Yellow
Write-Host ""

$configureCredentials = Read-HostWithDefault -Prompt "Configure IBKR credentials now? (Y/n)" -Default "Y"
if ($configureCredentials -eq "Y" -or $configureCredentials -eq "y") {
    $IBKRUsername = Read-HostWithDefault -Prompt "IBKR Paper Username" -Required
    $IBKRPassword = Read-HostWithDefault -Prompt "IBKR Paper Password" -Required -Secret
} else {
    $IBKRUsername = ""
    $IBKRPassword = ""
    Write-Host "Skipped. Run setup-ibkr-autologin.ps1 later to configure." -ForegroundColor Gray
}
Write-Host ""

# Step 7: Time Window Configuration
Write-Host "Step 7: Time Window Configuration" -ForegroundColor Yellow
Write-Host "Services will only run during this window on specified days."
$RunWindowStart = Read-HostWithDefault -Prompt "Start time (HH:MM, 24-hour)" -Default "04:00"
$RunWindowEnd = Read-HostWithDefault -Prompt "End time (HH:MM, 24-hour)" -Default "20:00"
$RunWindowDays = Read-HostWithDefault -Prompt "Active days (comma-separated)" -Default "Mon,Tue,Wed,Thu,Fri"
$RunWindowTimezone = Read-HostWithDefault -Prompt "Timezone" -Default "America/Toronto"
Write-Host ""

# Step 8: Trading Control
Write-Host "Step 8: Trading Control" -ForegroundColor Yellow
Write-Host "Trading controls are managed via control.json (mm-ibkr-gateway)." -ForegroundColor Gray
Write-Host "Default location: C:\ProgramData\mm-ibkr-gateway" -ForegroundColor Gray
$defaultControlDir = "C:\ProgramData\mm-ibkr-gateway"
$ControlDir = Read-HostWithDefault -Prompt "Control.json directory" -Default $defaultControlDir -Required
Write-Host ""

# Step 9: Safety Confirmation
Write-Host "Step 9: Safety Settings" -ForegroundColor Yellow
Write-Host "SAFETY: Trading controls are managed via control.json (centralized)" -ForegroundColor Green
Write-Host "Default: Paper mode, orders DISABLED, dry-run enabled." -ForegroundColor Green
Write-Host "Use the UI (/ui) or /admin/control to change trading settings." -ForegroundColor Gray
Write-Host ""

# Create directories
Write-Host "Creating directories..." -ForegroundColor Cyan

# Storage directories
$LogDir = Join-Path $StorageBasePath "logs"
$AuditDbPath = Join-Path $StorageBasePath "audit.db"

if (-not (Test-Path $StorageBasePath)) {
    New-Item -ItemType Directory -Path $StorageBasePath -Force | Out-Null
    Write-Host "  Created: $StorageBasePath"
}
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Host "  Created: $LogDir"
}

# Secrets directory
if (-not (Test-Path $SecretsDir)) {
    New-Item -ItemType Directory -Path $SecretsDir -Force | Out-Null
    Write-Host "  Created: $SecretsDir"
}

# Create Python virtual environment for service (venv)
$venvPath = Join-Path $StorageBasePath "venv"
if (Test-Path $venvPath) {
    Write-Host "Python virtual environment already exists at: $venvPath" -ForegroundColor Gray
    $recreateVenv = Read-HostWithDefault -Prompt "Recreate venv? (y/N)" -Default "N"
    if ($recreateVenv -eq "y" -or $recreateVenv -eq "Y") {
        Write-Host "Removing existing venv..." -NoNewline
        Remove-Item -Recurse -Force $venvPath
        Write-Host " OK" -ForegroundColor Green
    }
}

if (-not (Test-Path $venvPath)) {
    # Detect Python installations
    function Find-PythonInstallations {
        $pythonVersions = @()

        # Try same approach as mm-trading: check well-known install locations first
        $searchPaths = @(
            "C:\\Python3*\\python.exe",
            "C:\\Program Files\\Python3*\\python.exe",
            "C:\\Program Files (x86)\\Python3*\\python.exe",
            "$env:LOCALAPPDATA\\Programs\\Python\\Python3*\\python.exe"
        )

        foreach ($pattern in $searchPaths) {
            Get-Item $pattern -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    $versionOutput = & $_.FullName --version 2>&1
                    if ($versionOutput -match 'Python (\d+\.\d+\.\d+)') {
                        $version = $Matches[1]
                        $pythonVersions += @{
                            Path = $_.FullName
                            Version = $version
                            VersionObj = [version]$version
                        }
                    }
                } catch {}
            }
        }

        # Check PATH via Get-Command for python and python3
        $candidates = @('python','python3')
        foreach ($cmd in $candidates) {
            try {
                $ci = Get-Command $cmd -ErrorAction SilentlyContinue
                if ($ci -and $ci.Source) {
                    try {
                        $versionOutput = & $ci.Source --version 2>&1
                        if ($versionOutput -match 'Python (\d+\.\d+\.\d+)') {
                            $version = $Matches[1]
                            if (-not ($pythonVersions | Where-Object { $_.Path -eq $ci.Source })) {
                                $pythonVersions += @{
                                    Path = $ci.Source
                                    Version = $version
                                    VersionObj = [version]$version
                                }
                            }
                        }
                    } catch {}
                }
            } catch {}
        }

        # Try where.exe as an additional PATH resolver
        try {
            $whereOut = & where.exe python 2>$null
            if ($whereOut) {
                foreach ($p in $whereOut) {
                    $path = $p.Trim()
                    if ($path -and -not ($pythonVersions | Where-Object { $_.Path -eq $path })) {
                        try {
                            $vout = & "$path" --version 2>&1
                            if ($vout -match 'Python (\d+\.\d+\.\d+)') {
                                $ver = $Matches[1]
                                $pythonVersions += @{
                                    Path = $path
                                    Version = $ver
                                    VersionObj = [version]$ver
                                }
                            }
                        } catch {}
                    }
                }
            }
        } catch {}

        # Lastly, check py launcher if available
        try {
            $pyCmd = Get-Command py -ErrorAction SilentlyContinue
            if ($pyCmd) {
                try {
                    $pyverOut = & py -3 --version 2>&1
                    if ($pyverOut -match 'Python (\d+\.\d+\.\d+)') {
                        $ver = $Matches[1]
                        $pyExe = & py -3 -c "import sys; print(sys.executable)" 2>$null
                        $pyExe = $pyExe.Trim()
                        if ($pyExe -and -not ($pythonVersions | Where-Object { $_.Path -eq $pyExe })) {
                            $pythonVersions += @{
                                Path = $pyExe
                                Version = $ver
                                VersionObj = [version]$ver
                            }
                        }
                    }
                } catch {}
            }
        } catch {}

        # Deduplicate and sort
        $pythonVersions = $pythonVersions | Sort-Object -Property VersionObj -Descending | Select-Object -Unique -Property Path, Version, VersionObj

        # Diagnostic: print discovered entries
        if ($pythonVersions.Count -gt 0) {
            Write-Host "  Detected Python installs:" -ForegroundColor Gray
            for ($i=0; $i -lt $pythonVersions.Count; $i++) {
                $p = $pythonVersions[$i]
                Write-Host "    [$($i+1)] $($p.Version) - $($p.Path)" -ForegroundColor Gray
            }
        }

        return $pythonVersions
    }

    Write-Host "Setting up Python virtual environment at: $venvPath" -ForegroundColor Yellow
    $pythonInstalls = Find-PythonInstallations

    # Filter installs to valid paths
    $pythonInstalls = $pythonInstalls | Where-Object { $_.Path -and (Test-Path $_.Path) }

    # If none found after filtering, try Get-Command or where.exe as fallback
    if (-not $pythonInstalls -or $pythonInstalls.Count -eq 0) {
        Write-Host "No valid Python paths found in auto-detection; attempting PATH-based detection..." -ForegroundColor Yellow
        $fallback = @()
        try {
            $ci = Get-Command python -ErrorAction SilentlyContinue
            if ($ci -and $ci.Source -and (Test-Path $ci.Source)) {
                $vout = & "$($ci.Source)" --version 2>&1
                if ($vout -match 'Python (\d+\.\d+\.\d+)') { $ver = $Matches[1] } else { $ver = 'unknown' }
                $fallback += @{ Path = $ci.Source; Version = $ver; VersionObj = [version]$ver }
            }
        } catch {}
        try {
            $whereOut = & where.exe python 2>$null
            if ($whereOut) {
                foreach ($p in $whereOut) {
                    $path = $p.Trim()
                    if ($path -and (Test-Path $path) -and -not ($fallback | Where-Object { $_.Path -eq $path })) {
                        $vout = & "$path" --version 2>&1
                        if ($vout -match 'Python (\d+\.\d+\.\d+)') { $ver = $Matches[1] } else { $ver = 'unknown' }
                        $fallback += @{ Path = $path; Version = $ver; VersionObj = [version]$ver }
                    }
                }
            }
        } catch {}
        if ($fallback.Count -gt 0) {
            $pythonInstalls = $fallback | ForEach-Object { [PSCustomObject]$_ } | Sort-Object -Property VersionObj -Descending
        }
    }

    # Diagnostic output: enumerate discovered installations
    Write-Host "DEBUG: pythonInstalls count: $($pythonInstalls.Count)" -ForegroundColor Cyan
    for ($di = 0; $di -lt $pythonInstalls.Count; $di++) {
        $dp = $pythonInstalls[$di]
        $type = if ($dp -is [string]) { $dp.GetType().FullName } else { $dp.PSObject.TypeNames[0] }
        Write-Host "  DEBUG [$($di+1)] Type: $type Path: '$($dp.Path)' Version: '$($dp.Version)' VersionObj: '$($dp.VersionObj)'" -ForegroundColor Gray
    }

    if ($pythonInstalls.Count -eq 0) {
        Write-Host "ERROR: No Python installations found. Please install Python 3.11+ and re-run configure." -ForegroundColor Red
    } else {
        $selectedPython = $null
        if ($pythonInstalls.Count -eq 1) {
            $selectedPython = $pythonInstalls[0]
            Write-Host "Found Python $($selectedPython.Version) at $($selectedPython.Path)" -ForegroundColor Green
        } else {
            $count = ($pythonInstalls | Measure-Object).Count
            Write-Host "Found multiple Python installations:" -ForegroundColor Yellow
            for ($i = 0; $i -lt $count; $i++) {
                $p = $pythonInstalls[$i]
                $ver = if ($p.Version) { $p.Version } else { "(unknown)" }
                $path = if ($p.Path) { $p.Path } else { "(unknown)" }
                Write-Host "  [$($i+1)] Python $ver - $path"
            }

            $promptText = "Select Python version (1-$count) [1] or enter full path:"
            $choice = Read-HostWithDefault -Prompt $promptText -Default "1"
            if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "1" }

            if ($choice -match '^[0-9]+$') {
                $choiceIdx = [int]$choice - 1
                if ($choiceIdx -ge 0 -and $choiceIdx -lt $count) {
                    $selectedPython = $pythonInstalls[$choiceIdx]
                    Write-Host "Selected: Python $($selectedPython.Version) - $($selectedPython.Path)" -ForegroundColor Green
                } else {
                    Write-Host "Invalid selection index; defaulting to first detected Python" -ForegroundColor Yellow
                    $selectedPython = $pythonInstalls[0]
                }
            } else {
                # Treat input as a path
                if (Test-Path $choice) {
                    try {
                        $vout = & "$choice" --version 2>&1
                        if ($vout -match 'Python (\d+\.\d+\.\d+)') { $ver = $Matches[1] } else { $ver = "unknown" }
                        $selectedPython = @{ Path = $choice; Version = $ver; VersionObj = [version]$ver }
                        Write-Host "Selected Python at path: $choice (version: $ver)" -ForegroundColor Green
                    } catch {
                        Write-Host "Error: Could not execute provided python path; defaulting to first detected Python" -ForegroundColor Yellow
                        $selectedPython = $pythonInstalls[0]
                    }
                } else {
                    Write-Host "Invalid path specified; defaulting to first detected Python" -ForegroundColor Yellow
                    $selectedPython = $pythonInstalls[0]
                }
            }
        }

        if ($selectedPython) {
            $pythonExe = $selectedPython.Path
            Write-Host "Creating venv using: $pythonExe" -ForegroundColor Gray
            & $pythonExe -m venv $venvPath
            if ($LASTEXITCODE -ne 0) {
                Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
                exit 1
            } else {
                Write-Host "Virtual environment created: $venvPath" -ForegroundColor Green
                $venvPython = Join-Path $venvPath "Scripts\\python.exe"
                $venvPip = Join-Path $venvPath "Scripts\\pip.exe"
                Write-Host "Installing packages into venv..." -ForegroundColor Yellow
                
                # Upgrade pip first
                & $venvPip install --upgrade pip
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "ERROR: Failed to upgrade pip" -ForegroundColor Red
                    exit 1
                }

                # Install mm-ibkr-gateway and all dependencies
                Write-Host "Installing mm-ibkr-gateway from $RepoRoot..." -ForegroundColor Yellow
                & $venvPip install -e "$RepoRoot"
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "Package installed into venv successfully" -ForegroundColor Green
                } else {
                    Write-Host "ERROR: Failed to install mm-ibkr-gateway" -ForegroundColor Red
                    Write-Host "Verify all dependencies are available and re-run configure.ps1" -ForegroundColor Yellow
                    exit 1
                }
            }
        }
    }
} else {
    Write-Host "Virtual environment already present at: $venvPath" -ForegroundColor Gray
}

Ensure-Tzdata -VenvPath $venvPath
# ProgramData directory for state
$StateDir = "C:\ProgramData\mm-ibkr-gateway"
if (-not (Test-Path $StateDir)) {
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    Write-Host "  Created: $StateDir"
}

# Check control.json location
Write-Host "`nChecking control.json..." -ForegroundColor Cyan
$ControlFile = Join-Path $ControlDir "control.json"
if (-not (Test-Path $ControlFile)) {
    Write-Host "  Missing: $ControlFile" -ForegroundColor Yellow
    Write-Host "  The API will create a default control.json on first start." -ForegroundColor Yellow
} else {
    Write-Host "  Found: $ControlFile" -ForegroundColor Gray
    try {
        $control = Get-Content -Path $ControlFile -Raw | ConvertFrom-Json
        Write-Host "  trading_mode:   $($control.trading_mode)"
        Write-Host "  orders_enabled: $($control.orders_enabled)"
        Write-Host "  dry_run:        $($control.dry_run)"
        Write-Host "  override_file:  $($control.live_trading_override_file)"
    } catch {
        Write-Host "  Warning: Failed to read control.json: $_" -ForegroundColor Yellow
    }
}

# Generate config.json
Write-Host "`nGenerating config.json..." -ForegroundColor Cyan

$configDir = Split-Path -Parent $ConfigFile
if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    Write-Host "  Created: $configDir" -ForegroundColor Gray
}

$configData = @{
    schema_version = 1
    api_bind_host = $LanIP
    api_port = [int]$ApiPort
    allowed_ips = $AllowedIPs
    api_request_timeout = [double]$ApiRequestTimeout
    ibkr_gateway_host = $IbkrGatewayHost
    paper_gateway_port = [int]$PaperPort
    paper_client_id = [int]$PaperClientId
    live_gateway_port = [int]$LiveGatewayPort
    live_client_id = [int]$LiveClientId
    ibkr_gateway_path = $GatewayPath
    log_level = $LogLevel
    log_format = $LogFormat
    data_storage_dir = $StorageBasePath
    log_dir = $LogDir
    audit_db_path = $AuditDbPath
    watchdog_log_dir = $WatchdogLogDir
    control_dir = $ControlDir
    run_window_start = $RunWindowStart
    run_window_end = $RunWindowEnd
    run_window_days = $RunWindowDays
    run_window_timezone = $RunWindowTimezone
    admin_restart_enabled = $AdminRestartEnabled
}

if (Test-Path $ConfigFile) {
    $backupPath = "$ConfigFile.backup.$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item $ConfigFile $backupPath
    Write-Host "  Backed up existing config.json to: $backupPath" -ForegroundColor Gray
}

$tempConfig = "$ConfigFile.tmp.$([guid]::NewGuid().ToString())"
try {
    $configJson = $configData | ConvertTo-Json -Depth 5
    Set-Content -Path $tempConfig -Value $configJson -Encoding UTF8 -ErrorAction Stop
    Move-Item -Path $tempConfig -Destination $ConfigFile -Force -ErrorAction Stop
    Write-Host "  Created: $ConfigFile" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Failed to write config.json: $_" -ForegroundColor Red
    Remove-Item -Path $tempConfig -ErrorAction SilentlyContinue
}

# Generate minimal .env file for secrets
Write-Host "`nGenerating .env file..." -ForegroundColor Cyan

$envContent = @"
# =============================================================================
# mm-ibkr-gateway Secrets (.env)
# Generated by configure.ps1 on $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
# =============================================================================
#
# Operational settings live in config.json:
#   $ConfigFile
#
# =============================================================================
# API AUTHENTICATION
# =============================================================================
# API key (generated by generate-api-key.ps1)
# API_KEY=
#
# Admin token for /admin/* endpoints (required for admin operations)
# ADMIN_TOKEN=
"@

# Backup existing .env if present
if (Test-Path $EnvFile) {
    $backupPath = "$EnvFile.backup.$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item $EnvFile $backupPath
    Write-Host "  Backed up existing .env to: $backupPath" -ForegroundColor Gray
}

# Atomic write with retries to handle file locks
$tempEnv = "$EnvFile.tmp.$([guid]::NewGuid().ToString())"
try {
    Set-Content -Path $tempEnv -Value $envContent -Encoding UTF8 -ErrorAction Stop
    $maxAttempts = 5
    $moved = $false
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            Move-Item -Path $tempEnv -Destination $EnvFile -Force -ErrorAction Stop
            Write-Host "  Created: $EnvFile" -ForegroundColor Green
            $moved = $true
            break
        } catch {
            Write-Host "  WARNING: Could not move .env to destination (attempt $attempt/$maxAttempts): $_" -ForegroundColor Yellow
            if ($attempt -lt $maxAttempts) { Start-Sleep -Seconds (2 * $attempt) }
        }
    }
    if (-not $moved) {
        Write-Host "  ERROR: Failed to write .env after $maxAttempts attempts. Close editors or other programs that may hold the file and re-run configure.ps1" -ForegroundColor Red
        Remove-Item -Path $tempEnv -ErrorAction SilentlyContinue
    }
} catch {
    Write-Host "  ERROR: Failed to write temporary .env file: $_" -ForegroundColor Red
    Remove-Item -Path $tempEnv -ErrorAction SilentlyContinue
}

# Store IBKR credentials for autologin setup
if ($IBKRUsername -and $IBKRPassword) {
    $credFile = Join-Path $SecretsDir "ibkr_credentials.json"
    $credContent = @{
        username = $IBKRUsername
        password = $IBKRPassword
        timestamp = (Get-Date -Format "o")
    } | ConvertTo-Json
    Set-Content -Path $credFile -Value $credContent -Encoding UTF8
    Write-Host "  Stored credentials for autologin setup: $credFile" -ForegroundColor Gray
    Write-Host "  Run setup-ibkr-autologin.ps1 to configure IBKR Gateway." -ForegroundColor Gray
}

# Summary
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Configuration Complete!" -ForegroundColor Green
Write-Host "========================================`n" -ForegroundColor Green

Write-Host "Summary:" -ForegroundColor Cyan
Write-Host "  Repository:      $RepoRoot"
Write-Host "  API Endpoint:    http://${LanIP}:${ApiPort}"
Write-Host "  Allowed IPs:     $AllowedIPs"
Write-Host "  Storage Path:    $StorageBasePath"
Write-Host "  Config File:     $ConfigFile"
Write-Host "  Secrets File:    $EnvFile"
Write-Host "  Control File:    $ControlFile"
Write-Host "  Run Window:      $RunWindowDays $RunWindowStart - $RunWindowEnd ($RunWindowTimezone)"
Write-Host ""
Write-Host "IMPORTANT: Time Window Enforcement" -ForegroundColor Yellow
Write-Host "  The API enforces run_window settings via middleware." -ForegroundColor Gray
Write-Host "  Requests outside the window (except /health) will return 503 Service Unavailable." -ForegroundColor Gray
Write-Host "  To test outside business hours, set run_window_* to 00:00 - 23:59 and restart the service." -ForegroundColor Gray

Write-Host "`nNext Steps:" -ForegroundColor Yellow
Write-Host "  1. Run: .\setup-ibkr-autologin.ps1    (configure gateway auto-login if not done already)"
Write-Host "  2. Run: .\generate-api-key.ps1       (create API key)"
Write-Host "  3. Run: .\setup-firewall.ps1         (requires admin)"
Write-Host "  4. Run: .\install-nssm.ps1           (install NSSM service manager, requires admin)"
Write-Host "  5. Run: .\install-api-service.ps1    (install API service, requires admin)"
Write-Host "  6. Run: .\install-gateway-service.ps1 (install Gateway service, requires admin)"
Write-Host "  7. Run: .\verify.ps1                 (validate deployment)"
Write-Host ""
