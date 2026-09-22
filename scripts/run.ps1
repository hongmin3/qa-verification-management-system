$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
# 포트·호스트는 여기 적지 않는다. `config.yaml` 의 app.host / app.port 가 단일 원본이고
# `app/serve.py` 가 그 값을 읽어 uvicorn 에 넘긴다.
& "$root\.venv\Scripts\python.exe" -m app.serve
