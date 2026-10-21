$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath '.\venv\Scripts\python.exe')) {
    python -m venv venv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать виртуальное окружение' }
}
& .\venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости' }
& .\venv\Scripts\python.exe manage.py migrate
if ($LASTEXITCODE -ne 0) { throw 'Ошибка миграций' }
& .\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
