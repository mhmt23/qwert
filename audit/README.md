# audit/ — Katman bazlı Solidity açık tarayıcı

Akıllı kontrat güvenlik denetimi için hafif, bağımlılıksız (yalnızca Python
stdlib) bir ilk-geçiş aracı. Kaynak `.sol` dosyalarını okur, açıkları altı
katmana ayrılmış heuristik dedektörlerle tarar ve bulguları önem sırasına göre
raporlar.

**Ne yapar:** kaynak kodu okur, şüpheli kalıpları işaretler.
**Ne yapmaz:** hiçbir zincire bağlanmaz, işlem göndermez, fona dokunmaz.
Tamamen statik ve savunma amaçlı. Amaç: bug bounty / audit yarışması öncesi
hızlı, tekrarlanabilir bir tarama.

## Katmanlar

| # | Katman | Örnek açıklar |
|---|---|---|
| 1 | Erişim kontrolü | korumasız ayrıcalıklı fonksiyon, tx.origin, açık initialize |
| 2 | Aritmetik / muhasebe | <0.8 overflow, precision loss, yanlış yuvarlama |
| 3 | Dış çağrı / reentrancy | CEI ihlali, guard eksikliği, düşük seviye call |
| 4 | Oracle / fiyat | spot fiyat manipülasyonu, staleness kontrolü yok |
| 5 | Ekonomik / mantık | share inflation, likidasyon mantığı, fee-on-transfer |
| 6 | Başlatma / upgrade | delegatecall, selfdestruct, initializer koruması |

## Kullanım

```bash
# Tek dosya
python -m audit.cli path/to/Contract.sol

# Klasör (tüm .sol'ları tarar)
python -m audit.cli contracts/

# Markdown rapor üret + sadece medium ve üstünü göster
python -m audit.cli contracts/ --md rapor.md --min medium

# CI'da kritik bulguda hata döndür
python -m audit.cli contracts/ --fail-on high
```

Örnek fixture üzerinde dene:

```bash
python -m audit.cli audit/samples/VulnerableLendingPool.sol
```

## Önemli: heuristik = inceleme noktası, kanıt değil

Bu araç **kesin sonuç vermez**. İşaretlediği her satır elle doğrulanmalı;
işaretlemediği yerlerde de açık olabilir. Gerçek açıkların çoğu iş
mantığındadır ve otomatik tarayıcıların göremeyeceği bağlam gerektirir. Aracı
bir *başlangıç haritası* olarak kullan, `CHECKLIST.md`'yi elle uygula.

## İş akışı

1. `python -m audit.cli` ile hızlı tarama → sıcak noktaları bul.
2. `CHECKLIST.md`'yi kontratın tamamına elle uygula.
3. Şüpheli bir açık için **mainnet fork'unda** (Foundry) exploit yazıp doğrula
   — gerçek fon çekmeden, sadece "çekilebilir" olduğunu ispatla.
4. Programın kurallarına göre raporla (Immunefi / Code4rena / Sherlock).

Bug bounty'de ödül açığı **bulup raporlayana** ödenir. Fonu hareket ettirmek
ya da açığı pazarlık aracı yapmak hem kuralları ihlal eder hem suçtur — ve
zaten ödülü sıfırlar. Denetim fork'ta ispatlanır, canlıda değil.

## Dosya yapısı

```
audit/
├── __init__.py
├── scanner.py      # çekirdek: dosya okuma, blok çıkarma, tarama döngüsü
├── layers.py       # 6 katmanın dedektörleri
├── report.py       # terminal + markdown çıktı
├── cli.py          # komut satırı arayüzü
├── CHECKLIST.md    # elle denetim listesi
├── samples/        # kasıtlı-açıklı eğitim fixture'ları
└── tests/          # duman testleri
```
