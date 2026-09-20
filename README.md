# At Yarisi AI

TJK verileriyle at yarışı analiz sistemi.

## Entegre edilen motorlar

Bu depo, uygun açık kaynak mimarilerinden kod kopyalamak yerine uyumlu işlevleri kendi kod tabanında birleştirir:

- TJK/günlük program + geçmiş form toplama
- Şeffaf temel at skoru
- LightGBM tabanlı opsiyonel ML katmanı (data/training.csv varsa)
- Yarış içi softmax olasılıklandırma
- Bayesian shrinkage ile küçük/seyrek örneklerde temkinli olasılık
- Fair odds
- Piyasa oranı varsa edge hesabı
- Harville sıralı olasılık altyapısı
- Yarış bazlı top-3 ve skip gate
- GitHub Actions ile otomatik günlük çalıştırma

Mimari, TJK odaklı açık kaynak Ganyan projelerindeki ranking/Bayesian/Harville yaklaşımından ve Horse Racing Analytics tarzı fair-odds/edge/backtest bileşenlerinden yararlanacak şekilde uyarlanmıştır. Dış projelerin geçmiş ROI iddiaları sistem tarafından garanti veya doğrulanmış kâr sinyali olarak kullanılmaz.

## Çalıştırma

    pip install -r requirements.txt
    python src/collector.py
    python src/main.py
    python src/advanced_pipeline.py
python src/backtest.py

Üretilen dosyalar:

- data/ranked_horses.json — eski/şeffaf skor
- data/advanced_ranked_horses.json — model olasılığı + fair odds + edge
- data/race_analysis.json — yarış bazlı özet ve skip gate
- data/sixli_coupons.json — 6'lı Ganyan için 240/720/1440 TL bütçe katmanları
- data/backtest_report.json — arşivlenmiş yarışlarda tanısal backtest raporu

### LightGBM eğitim verisi

Gerçek sonuçlardan oluşturulmuş data/training.csv sağlanırsa ML katmanı otomatik devreye girer. Beklenen kolonlar:

race_id, finish_position, recent_form, track_form, distance_form, jockey_form, trainer_form, weight_score, agf_score

Bugünün sonuçları eğitim verisine dahil edilmez; böylece doğrudan veri sızıntısı engellenir.

## Kaynak

Ana veri akışı mevcut TJK/Nal Sesleri collector'ı üzerinden korunmuştur. Gelecekte Sportily bağlantısı ayrıca dış sinyal olarak eklenebilir; ana modelin içine zorunlu bağımlılık yapılmamıştır.

> Tahminler garanti değildir. Özellikle ROI/backtest sonuçları gerçek para yatırımı için tek başına yeterli kanıt değildir.
