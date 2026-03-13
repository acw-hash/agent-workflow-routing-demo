$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

python -m venv .venv

if ($IsWindows) {
    .\.venv\Scripts\Activate.ps1
} else {
    . ./.venv/bin/activate
}

pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
