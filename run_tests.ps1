# run_tests.ps1
# Запуск локальных тестов Tsclp_v2.0.
# Не требует токена и не подключается к брокеру.

if (-not (Test-Path ".\venv\Scripts\Activate.ps1")) {
    Write-Host "❌ venv не найден. Сначала создайте: python -m venv venv" -ForegroundColor Red
    exit 1
}

.\venv\Scripts\Activate.ps1
python -m pytest tests -v
