param(
    [string]$Python = "",
    [string]$EnvFile = ".env",
    [int]$Port = 3000
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not $Python) {
    $LocalPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
    if (Test-Path $LocalPython) {
        $Python = $LocalPython
    } else {
        $Python = "python"
    }
}

function Import-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $Line = $_.Trim()
        if (-not $Line -or $Line.StartsWith("#")) {
            return
        }

        if ($Line.StartsWith("export ")) {
            $Line = $Line.Substring(7).Trim()
        }

        $Index = $Line.IndexOf("=")
        if ($Index -lt 1) {
            return
        }

        $Name = $Line.Substring(0, $Index).Trim()
        $Value = $Line.Substring($Index + 1).Trim()

        if (
            ($Value.StartsWith('"') -and $Value.EndsWith('"')) -or
            ($Value.StartsWith("'") -and $Value.EndsWith("'"))
        ) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }

        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$PortNumber
    )

    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $Async = $Client.BeginConnect($HostName, $PortNumber, $null, $null)
        if (-not $Async.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $Client.EndConnect($Async)
        return $true
    } catch {
        return $false
    } finally {
        $Client.Close()
    }
}

function Resolve-BlenderPath {
    if ($env:BLENDER_PATH) {
        return $env:BLENDER_PATH
    }

    $Command = Get-Command blender -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    $Candidates = @(
        "C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 4.0\blender.exe"
    )

    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            return $Candidate
        }
    }

    return ""
}

Import-DotEnv -Path (Join-Path $ScriptDir $EnvFile)

$BlenderPath = Resolve-BlenderPath
if (-not $BlenderPath -or -not (Test-Path $BlenderPath)) {
    Write-Error "Blender not found. Set BLENDER_PATH in $EnvFile or add blender.exe to PATH."
}

if (-not $env:LLM_PROVIDER) { $env:LLM_PROVIDER = "openai" }

if ($env:LLM_PROVIDER -eq "anthropic") {
    if (-not $env:ANTHROPIC_API_KEY) {
        Write-Error "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY not set. Configure it in $EnvFile."
    }
    $Model    = if ($env:ANTHROPIC_MODEL)    { $env:ANTHROPIC_MODEL }    else { "claude-sonnet-4-20250514" }
    $ApiBase  = if ($env:ANTHROPIC_API_BASE) { $env:ANTHROPIC_API_BASE } else { "https://api.anthropic.com" }
    $Provider = "anthropic"
} else {
    if (-not $env:OPENAI_API_KEY) {
        Write-Error "OPENAI_API_KEY not set. Configure it in $EnvFile (or set LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY)."
    }
    $Model    = if ($env:OPENAI_MODEL)    { $env:OPENAI_MODEL }    else { "gpt-4o" }
    $ApiBase  = if ($env:OPENAI_API_BASE) { $env:OPENAI_API_BASE } else { "https://api.openai.com" }
    $Provider = "openai"
}

Write-Host "Blender:   $BlenderPath"
Write-Host "Python:    $Python"
Write-Host "Provider:  $Provider"
Write-Host "Model:     $Model"
Write-Host "API base:  $ApiBase"
Write-Host ""

Remove-Item -LiteralPath (Join-Path $ScriptDir "sessions") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ScriptDir "renders") -Recurse -Force -ErrorAction SilentlyContinue

$BlenderProcess = $null
$ServerProcess = $null

try {
    Write-Host "Starting Blender with addon..."
    $BlenderProcess = Start-Process -FilePath $BlenderPath -ArgumentList @("--python", $Addon) -PassThru

    Write-Host "Waiting for addon to listen on 127.0.0.1:9876..."
    $Ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ($BlenderProcess.HasExited) {
            throw "Blender process exited before the addon started. Check the Blender window for errors."
        }
        if (Test-TcpPort -HostName "127.0.0.1" -PortNumber 9876) {
            $Ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $Ready) {
        throw "Addon did not start listening on 127.0.0.1:9876 within 30 seconds."
    }

    Write-Host "Addon ready"
    Write-Host ""
    Write-Host "Starting web server on http://localhost:$Port ..."
    Write-Host "Press Ctrl+C to stop both."
    Write-Host ""

    $ServerProcess = Start-Process -FilePath $Python -ArgumentList @("-m", "blender_scene.main") -WorkingDirectory $ScriptDir -NoNewWindow -PassThru

    while (-not $ServerProcess.HasExited -and -not $BlenderProcess.HasExited) {
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host ""
    Write-Host "Shutting down..."

    if ($ServerProcess -and -not $ServerProcess.HasExited) {
        Stop-Process -Id $ServerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($BlenderProcess -and -not $BlenderProcess.HasExited) {
        Stop-Process -Id $BlenderProcess.Id -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Done"
}
