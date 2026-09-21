$ErrorActionPreference="Stop"
$env:BACKFILL_DAYS=if($env:BACKFILL_DAYS){$env:BACKFILL_DAYS}else{"30"}
$PY=if(Test-Path ".venv\Scripts\python.exe"){".\.venv\Scripts\python.exe"}else{"python"}

& $PY -m pip install -q requests
if($LASTEXITCODE -ne 0){throw "Python requests kurulumu başarısız."}

& $PY src/local_backfill.py
if($LASTEXITCODE -ne 0){throw "Yerel backfill başarısız."}

if(!(Test-Path "data\historical_results.csv")){throw "historical_results.csv yok."}
$lines=(Get-Content "data\historical_results.csv").Count
if($lines -le 1){throw "historical_results.csv boş."}

git add data/historical_results.csv
if($LASTEXITCODE -ne 0){throw "git add başarısız."}

git commit -m "Update historical race data from local TJK collector"
$commitCode=$LASTEXITCODE
if($commitCode -ne 0){
  if($commitCode -eq 1){
    Write-Host "Yeni veri commit gerektirmiyor; mevcut dosya zaten güncel."
  } else {
    throw "git commit başarısız. Git kimliği veya repo durumunu kontrol edin."
  }
}

git push origin main
if($LASTEXITCODE -ne 0){throw "git push başarısız. GitHub kimlik doğrulamasını kontrol edin."}

Write-Host "TAMAMLANDI - GitHub'a veri gönderildi: $lines satır"