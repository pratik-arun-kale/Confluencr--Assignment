<#
.SYNOPSIS
    Developer task runner (Windows PowerShell).
.EXAMPLE
    .\tasks.ps1 setup    # venv + dependencies + databases + migrations
    .\tasks.ps1 run      # start the API on http://127.0.0.1:8000
    .\tasks.ps1 test     # run the test suite with coverage
    .\tasks.ps1 lint     # ruff + mypy
    .\tasks.ps1 verify   # reconcile balances against the ledger
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "migrate", "run", "test", "lint", "format", "verify")]
    [string]$Task = "run"
)

$ErrorActionPreference = "Stop"
$py = ".\.venv\Scripts\python.exe"

function Invoke-Step([string]$exe, [string[]]$argList) {
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Task) {
    "setup" {
        if (-not (Test-Path $py)) { Invoke-Step "python" @("-m", "venv", ".venv") }
        Invoke-Step $py @("-m", "pip", "install", "--upgrade", "pip")
        Invoke-Step $py @("-m", "pip", "install", "-r", "requirements-dev.txt")
        if (-not (Test-Path ".env")) {
            Copy-Item ".env.example" ".env"
            Write-Host "Created .env from .env.example - edit DATABASE_URL credentials, then re-run setup." -ForegroundColor Yellow
            exit 0
        }
        Invoke-Step $py @("-m", "scripts.create_db")
        Invoke-Step $py @("-m", "alembic", "upgrade", "head")
    }
    "migrate" { Invoke-Step $py @("-m", "alembic", "upgrade", "head") }
    "run"     { Invoke-Step $py @("-m", "uvicorn", "app.main:app", "--reload") }
    "test"    { Invoke-Step $py @("-m", "pytest", "--cov", "--cov-report=term-missing") }
    "lint" {
        Invoke-Step $py @("-m", "ruff", "check", ".")
        Invoke-Step $py @("-m", "ruff", "format", "--check", ".")
        Invoke-Step $py @("-m", "mypy")
    }
    "format" {
        Invoke-Step $py @("-m", "ruff", "check", "--fix", ".")
        Invoke-Step $py @("-m", "ruff", "format", ".")
    }
    "verify" { Invoke-Step $py @("-m", "scripts.verify_ledger") }
}
