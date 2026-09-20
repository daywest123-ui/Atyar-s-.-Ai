$ErrorActionPreference="Stop"
$env:BACKFILL_DAYS=if($env:BACKFILL_DAYS){$env:BACKFILL_DAYS}else{"30"}
$PY=if(Test-Path ".venv\Scripts\python.exe"){".\.venv\Scripts\python.exe"}else{"python"}
& $PY -m pip install -q requests
& $PY src/local_backfill.py
if(!(Test-Path "data\historical_results.csv")){throw "historical_results.csv yok."}
$lines=(Get-Content "data\historical_results.csv").Count
if($lines -le 1){throw "historical_results.csv boş."}
git add data/historical_results.csv
git commit -m "Update historical race data from local TJK collector"
git push
Write-Host "TAMAMLANDI - GitHub'a veri gönderildi: $lines satır"
