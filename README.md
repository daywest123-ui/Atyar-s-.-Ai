# At Yarisi AI

Çok kaynaklı uluslararası at yarışı analiz sistemi. TJK yalnızca Türkiye veri sağlayıcısıdır; model dış kaynak sinyallerini tek sağlayıcıya bağımlı olmadan kullanabilir.

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
- External Intelligence Layer: Timeform/Racing TV/Racing Post/ATR/Sporting Life/resmi ve yerel kaynaklardan normalize edilebilen sinyaller
- Hidden Value / Surprise Alert: Nzuri benzeri düşük piyasa atlarının bağımsız kaynaklarla desteklenip desteklenmediğini kontrol eden katman
- Kaynaklar arası consensus ve warning sinyali

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
- data/external_source_report.json — dış sinyal kaynak özeti
- data/external_signals.json — opsiyonel normalize edilmiş dış kaynak sinyalleri

### LightGBM eğitim verisi

Gerçek sonuçlardan oluşturulmuş data/training.csv sağlanırsa ML katmanı otomatik devreye girer. Beklenen kolonlar:

race_id, finish_position, recent_form, track_form, distance_form, jockey_form, trainer_form, weight_score, agf_score

Bugünün sonuçları eğitim verisine dahil edilmez; böylece doğrudan veri sızıntısı engellenir.

## Kaynak

Ana veri akışı mevcut TJK/Nal Sesleri collector'ı üzerinden korunmuştur. Gelecekte Sportily bağlantısı ayrıca dış sinyal olarak eklenebilir; ana modelin içine zorunlu bağımlılık yapılmamıştır.

> Tahminler garanti değildir. Özellikle ROI/backtest sonuçları gerçek para yatırımı için tek başına yeterli kanıt değildir.


### External Intelligence

The core model does not assume TJK is the only source. The optional data/external_signals.json file accepts normalized observations from multiple providers. This lets source-specific adapters feed the same model for UK, Ireland, Chile, US and other jurisdictions.

Supported signal concepts include Timeform rating/timefigure, pace, sectionals, course/distance fit, trainer/jockey uplift, horse-in-focus, warning flags, analyst/tipster support and source consensus. Missing external data is treated as missing data; it is never fabricated.

A horse is not added merely because it is a longshot. A surprise_alert is produced only when independent external support and/or pace/value evidence crosses a threshold. This is specifically designed to reduce the failure mode seen when a longshot such as Nzuri is removed too early.

Timeform's commercial API documents racecards, analyst verdicts, ratings, horse-by-horse comments and Smart Stats; its sectional archive is a separate product. Racing TV's RaceiQ provides horse/furlong sectional metrics for British and Irish races. These are source capabilities, not guarantees of availability for every jurisdiction.
