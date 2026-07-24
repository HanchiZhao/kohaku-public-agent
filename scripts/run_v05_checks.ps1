param(
    [ValidateSet(
        "quick",
        "live",
        "sse",
        "all"
    )]
    [string]$Mode = "quick",

    [ValidateRange(1, 10)]
    [int]$SseRetries = 5
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (
    Resolve-Path (
        Join-Path $PSScriptRoot ".."
    )
).Path

Set-Location $ProjectRoot


function Write-Section {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    Write-Host ""
    Write-Host (
        "=" * 72
    )

    Write-Host $Title

    Write-Host (
        "=" * 72
    )
}


function Invoke-ExternalStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Write-Section $Name

    & $Command

    $ExitCode = $LASTEXITCODE

    if ($null -eq $ExitCode) {
        $ExitCode = 0
    }

    if ($ExitCode -ne 0) {
        throw (
            "$Name failed with exit code " +
            "$ExitCode"
        )
    }

    Write-Host ""
    Write-Host "[PASS] $Name"
}


function Test-ServerHealth {
    Write-Section "Checking local server health"

    try {
        $Health = Invoke-RestMethod `
            -Uri "http://127.0.0.1:8000/health" `
            -Method Get `
            -TimeoutSec 10
    }
    catch {
        throw @"
The local FastAPI server is not reachable.

Start it in another VS Code terminal with:

uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"@
    }

    if ($Health.status -ne "ok") {
        $HealthJson = (
            $Health |
            ConvertTo-Json `
                -Depth 10
        )

        throw (
            "The /health endpoint returned " +
            "a degraded status:`n" +
            $HealthJson
        )
    }

    Write-Host (
        $Health |
        ConvertTo-Json `
            -Depth 10
    )

    Write-Host ""
    Write-Host "[PASS] Local server health"
}


function Test-SystemStatus {
    Write-Section "Checking persistence system status"

    $Status = Invoke-RestMethod `
        -Uri (
            "http://127.0.0.1:8000/" +
            "api/v1/system/status"
        ) `
        -Method Get `
        -TimeoutSec 15

    Write-Host (
        $Status |
        ConvertTo-Json `
            -Depth 10
    )

    if ($Status.status -ne "ok") {
        throw (
            "The system status endpoint " +
            "reported a degraded state."
        )
    }

    if (
        $Status.database_reachable `
            -ne $true
    ) {
        throw (
            "The application database is " +
            "not reachable."
        )
    }

    if (
        [int]$Status.recovery_failed_conversations `
            -ne 0
    ) {
        throw (
            "One or more conversations are " +
            "marked recovery_failed."
        )
    }

    if (
        [int]$Status.missing_session_files `
            -ne 0
    ) {
        throw (
            "One or more active conversations " +
            "are missing session files."
        )
    }

    if (
        [int]$Status.orphan_session_files `
            -ne 0
    ) {
        throw (
            "One or more orphan .kohakutr " +
            "files were detected."
        )
    }

    Write-Host ""
    Write-Host "[PASS] Persistence system status"
}


function Invoke-QuickChecks {
    Write-Section "V0.5 quick validation"

    Remove-Item `
        Env:RUN_LIVE_AGENT_TESTS `
        -ErrorAction SilentlyContinue

    Invoke-ExternalStep `
        -Name "Python compilation" `
        -Command {
            uv run python -m compileall `
                app `
                scripts `
                tests
        }

    Invoke-ExternalStep `
        -Name "JavaScript syntax check" `
        -Command {
            node --check frontend/app.js
        }

    Invoke-ExternalStep `
        -Name "Git whitespace check" `
        -Command {
            git diff --check `
                origin/main...HEAD
        }

    Invoke-ExternalStep `
        -Name "Complete non-live unittest suite" `
        -Command {
            uv run python -m unittest discover `
                -s tests `
                -t . `
                -v
        }

    Invoke-ExternalStep `
        -Name "Session index shutdown regression test" `
        -Command {
            uv run python -m unittest `
                tests.integration.test_index_shutdown `
                -v
        }

    Invoke-ExternalStep `
        -Name "Persistence doctor strict mode" `
        -Command {
            uv run python `
                scripts/persistence_doctor.py `
                --strict
        }
}


function Invoke-LiveChecks {
    Write-Section "V0.5 live model validation"

    Invoke-ExternalStep `
        -Name "AgentService live smoke test" `
        -Command {
            uv run python `
                -m app.smoke_service
        }

    Invoke-ExternalStep `
        -Name "Restart recovery live smoke test" `
        -Command {
            uv run python `
                -m app.smoke_restart_recovery
        }
}


function Invoke-SseChecks {
    Test-ServerHealth
    Test-SystemStatus

    Invoke-ExternalStep `
        -Name (
            "SSE transport and live interruption " +
            "smoke test"
        ) `
        -Command {
            uv run python `
                scripts/smoke_sse_client.py `
                --interrupt-attempts `
                $SseRetries
        }
}


try {
    Write-Section (
        "Kohaku Public Agent v0.5 checks"
    )

    Write-Host (
        "Project root: " +
        $ProjectRoot
    )

    Write-Host (
        "Selected mode: " +
        $Mode
    )

    switch ($Mode) {
        "quick" {
            Invoke-QuickChecks
        }

        "live" {
            Invoke-LiveChecks
        }

        "sse" {
            Invoke-SseChecks
        }

        "all" {
            Invoke-QuickChecks
            Invoke-LiveChecks
            Invoke-SseChecks
        }
    }

    Write-Section "Validation completed successfully"

    Write-Host (
        "All checks in mode '$Mode' passed."
    )

    exit 0
}
catch {
    Write-Host ""
    Write-Host (
        "=" * 72
    )

    Write-Host "VALIDATION FAILED"

    Write-Host (
        "=" * 72
    )

    Write-Host $_.Exception.Message

    exit 1
}