# ATM-Guard

**ATM/CDM cihaz katmanı (XFS) ile Çekirdek Bankacılık sistemi arasındaki
asenkronizasyon, race condition ve mutabakat boşluklarını güvenli bir test
ortamında üreten, tespit eden ve raporlayan açık kaynak savunma framework'ü.**

> Bu proje bir güvenlik/dolandırıcılık **tespit** aracıdır — saldırı aracı değildir.
> Kullanımdan önce [`RESPONSIBLE_USE.md`](RESPONSIBLE_USE.md) dosyasını okuyun.

## Neden?

ATM'lerde tespit edilen gerçek bir olay: XFS/CDM katmanında üretilen iptal
sinyalleri ile Core Banking arasındaki desync sayesinde biri "sınırsız" para
yatırıp çekebildi. ATM-Guard bu ve benzeri zafiyet sınıflarını (bkz.
[`docs/vulnerability_catalog.md`](docs/vulnerability_catalog.md)) yakalar.

## Hızlı başlangıç

```bash
cd atm-security
python cli.py demo          # A1 (deposit cancel race) üret + tespit et
python cli.py run all       # tüm senaryolar
python cli.py list          # senaryoları listele
python tests/test_reconciliation.py   # testler (bağımlılık yok)
```

Beklenen demo çıktısı: A1 senaryosu CRITICAL bulgu olarak yakalanır —
`fiziksel_net=+0€, ledger_net=+1000€` (nakit iade edildi ama hesap alacaklı kaldı).

## Mimari

- **`simulator/`** — XFS/CDM cihazı, Core Banking ledger'ı, zafiyet senaryoları.
- **`detector/`** — mutabakat motoru (fiziksel↔mantıksal invariant) + kural motoru.
- **`docs/`** — zafiyet kataloğu (katman bazlı) ve ekosistem taraması.
- **`tests/`** — detektörün zafiyetleri yakaladığını doğrulayan testler.

Detaylı yol haritası: [`PLAN.md`](PLAN.md).

## Kapsanan zafiyet sınıfları (özet)

| Sınıf | Katman | Örnek |
|---|---|---|
| A | XFS↔Core mutabakat/race | deposit cancel race (sizin olayınız) |
| B | Ledger/mutabakat penceresi | kaset sayımı ≠ ledger, hız istismarı |
| C | Switch/ISO 8583 | kayıp reversal, MAC eksik, replay |
| D | OS/endpoint | EOL Windows, varsayılan şifre |
| E | Fiziksel/cihaz | black box, jackpotting izi |
| F | İzleme/SIEM | log korelasyonu, alarm |

Tam liste ve tespit yöntemleri: [`docs/vulnerability_catalog.md`](docs/vulnerability_catalog.md).

## Durum

Faz 0 (iskelet) tamamlandı: çalışan simülatör + mutabakat motoru + A1/A2/A4/A5/B4
tespiti + testler. Sonraki fazlar `PLAN.md` içinde.

## Lisans

Önerilen: MIT (topluluk katkısı için). Kullanmadan önce sorumlu kullanım şartları.
