# ATM & Çekirdek Bankacılık Güvenlik / Mutabakat Test Framework'ü — Proje Planı

> **Amaç:** ATM/CDM cihaz katmanı (XFS) ile Çekirdek Bankacılık (Core Banking)
> sistemi arasında oluşan **asenkronizasyon, race condition ve mutabakat (reconciliation)
> boşluklarını** güvenli bir test ortamında **üretmek, tespit etmek ve raporlamak** için
> açık kaynak bir savunma (defensive security) framework'ü.
>
> **Bu proje bir saldırı aracı DEĞİLDİR.** Amaç dolandırıcılığı gerçekleştirmek değil,
> tıpkı sizin tespit ettiğiniz "sınırsız para yatırma/çekme" olayı gibi zafiyetleri
> **önceden yakalamak, izlemek ve kanıtlamaktır.**

---

## 0. Somut Olay Analizi — Sizin Tespit Ettiğiniz Zafiyet

Anlattığınız olayı bir zafiyet sınıfına oturtalım, çünkü tüm mimari bunun etrafında kurulacak.

**Olay:** ATM'nin XFS/CDM katmanında üretilen **iptal (cancel/reversal) sinyalleri** ile
Çekirdek Bankacılık sistemi arasındaki **asenkronizasyon**. Kişi ~100.000 € işlem yaptı.

**Zafiyet sınıfı:** *Transaction Reconciliation Gap / Distributed Race Condition (TOCTOU)*

Tipik akış (para yatırma senaryosu):

```
1. Kullanıcı CDM'e nakit koyar
2. CDM banknotları sayar        -> XFS: "CashAccepted" event
3. Switch/Host -> Core Banking  -> hesap ALACAKLANDIRILIR (+credit)
4. Bu sırada bir CANCEL/timeout -> XFS: "CashReturned" (banknotlar geri verilir)
5. İptal sinyali Core'a GEÇ ulaşır veya HİÇ ulaşmaz (async desync)
   -> Hesapta credit KALIR, nakit de kullanıcıda
   -> Kullanıcı hem parayı aldı hem hesabı alacaklandı = çift para
```

Para çekme senaryosunda ise: Core hesabı borçlandırır (-debit), dispense sırasında
cancel/geri alma bir sistemde reversal yapar ama nakit fiziksel olarak dağıtılmıştır.

**Kök nedenler (bunların hepsi test edilecek):**
- İki sistem arasında **atomik olmayan** işlem (two-phase commit / saga eksikliği)
- **Idempotency (tekrar-güvenliği) anahtarı** yokluğu — aynı iptal iki kez işlenebiliyor
- **Timeout/retry** mantığındaki tutarsızlık (host cevabı gecikince cihaz iptal ediyor,
  Core işlemi tamamlıyor)
- **Fiziksel olay ≠ mantıksal kayıt**: banknot sensörünün gerçekte ne yaptığı
  (kabul/iade) ile ledger hareketinin eşleşmemesi
- **Reconciliation penceresi** ya çok geniş ya da hiç yok — gün sonu mutabakatı
  gecikmeli olduğu için istismar penceresi açık kalıyor

---

## 1. Proje Vizyonu ve Kapsam

Framework üç yetenek sunar:

| Yetenek | Ne yapar | Neden |
|---|---|---|
| **Simülatör** | Sahte XFS/CDM + Switch + Core Banking ortamı kurar, race condition'ları güvenli üretir | Gerçek ATM'ye/bankaya dokunmadan zafiyet üretmek |
| **Detektör** | Cihaz olaylarını ledger hareketleriyle karşılaştırır, mutabakatsızlıkları yakalar | Sizin gördüğünüz olayı gerçek zamanlı yakalamak |
| **Tarayıcı/Denetçi** | Bilinen ATM/bankacılık zafiyetlerini test senaryosu olarak koşar, rapor üretir | Sistematik güvenlik denetimi |

**Kapsam DIŞI (etik sınır):** Jackpotting malware, XFS injection exploit'i, kart klonlama,
gerçek ATM'ye yetkisiz erişim aracı. Bu proje **tespit ve savunma** odaklıdır.

---

## 2. Mimari

```
                    ┌──────────────────────────────────────────┐
                    │            ATM-GUARD FRAMEWORK             │
                    └──────────────────────────────────────────┘

  [ Simülasyon Katmanı ]              [ Tespit Katmanı ]         [ Raporlama ]
  ┌─────────────────┐                 ┌──────────────────┐       ┌───────────┐
  │ XFS/CDM Device  │──events────────▶│ Event Collector  │       │  Rapor    │
  │ (nakit sensör,  │                 │  (normalize)     │       │  Motoru   │
  │  cancel sinyal) │                 └────────┬─────────┘       │ (JSON/MD) │
  └────────┬────────┘                          │                └─────▲─────┘
           │                          ┌────────▼─────────┐             │
  ┌────────▼────────┐                 │ Reconciliation   │─────────────┤
  │  Switch / Host  │──ISO8583────────▶│ Engine           │  bulgular   │
  │  (yönlendirme,  │                 │ (cihaz vs ledger)│             │
  │   timeout/retry)│                 └────────┬─────────┘             │
  └────────┬────────┘                          │                       │
  ┌────────▼────────┐                 ┌────────▼─────────┐             │
  │ Core Banking    │──ledger─────────▶│ Anomaly / Rule   │─────────────┘
  │ (hesap, ledger, │                 │ Detectors        │
  │  reversal)      │                 └──────────────────┘
  └─────────────────┘
```

**Ana veri modeli — her şeyin merkezi:** `TransactionEvent`
- `event_id`, `correlation_id` (tüm zinciri bağlar), `idempotency_key`
- `source` (XFS_DEVICE | SWITCH | CORE_BANKING)
- `type` (CASH_ACCEPTED, CASH_RETURNED, DISPENSE, CANCEL, REVERSAL, CREDIT, DEBIT, TIMEOUT)
- `physical` (bool — banknot gerçekten hareket etti mi?)
- `amount`, `account`, `timestamp`, `sequence`

Mutabakat motoru şu değişmezliği (invariant) doğrular:
> **Her fiziksel nakit hareketinin tam olarak bir ledger hareketi karşılığı olmalı,
> ve her iptal/reversal'ın hem fiziksel hem mantıksal ayağı eşleşmeli.**

---

## 3. Zafiyet Kataloğu (Test Senaryoları)

Detektörün yakalaması gereken zafiyet sınıfları. Her biri simülatörde üretilebilir + testi yazılır.

### A. Asenkronizasyon / Race Condition (sizin olayınız)
- **A1** — Deposit cancel race: nakit iade edilir ama credit kalır
- **A2** — Withdrawal reversal race: nakit dağıtılır ama debit reverse edilir
- **A3** — Timeout/retry double-credit: host timeout'ta cihaz iptal eder, Core tamamlar
- **A4** — Partial dispense mismatch: 500€ istenir, 300€ dağıtılır, 500€ debit edilir
- **A5** — Idempotency eksikliği: aynı reversal iki kez uygulanır

### B. Mutabakat (Reconciliation) Boşlukları
- **B1** — Cihaz olayı var, ledger hareketi yok (veya tersi)
- **B2** — Gün sonu mutabakat penceresi çok geniş → istismar penceresi
- **B3** — Cassette/kaset sayımı ile ledger toplamı uyuşmuyor
- **B4** — Sıra numarası (sequence) atlaması / tekrarı

### C. Protokol / Mesaj Katmanı (ISO 8583 & XFS)
- **C1** — Reversal mesajı (0400/0420) kayıp veya geç
- **C2** — MAC/imza doğrulama eksik → mesaj değiştirilebilir
- **C3** — Yeniden yürütme (replay) — eski onay mesajı tekrar gönderilir
- **C4** — Field 39 (response code) tutarsızlığı

### D. Kimlik & Erişim (savunma denetimi olarak)
- **D1** — PIN retry limiti yok / yanlış
- **D2** — Varsayılan/servis şifreleri (denetim listesi)
- **D3** — XFS katmanı yetkisiz erişime açık (izleme, exploit değil)

Bu katalog artık **7 katmanlı** tam bir haritaya genişletildi — her katman için
zafiyet sınıfı, tespit yöntemi ve durum: **[`docs/vulnerability_catalog.md`](docs/vulnerability_catalog.md)**.
Mevcut açık/kapalı kaynak projelerle karşılaştırma ve boşluk analizi:
**[`docs/landscape.md`](docs/landscape.md)** (electron-atm, KAL XFS4IoT, xfspp, Jube,
ISO 8583 socket zafiyetleri, jackpotting referansları).

Katman haritası:

```
  Katman 7  Dolandırıcılık İzleme / SIEM        (F)
  Katman 6  Çekirdek Bankacılık / Ledger        (A, B)
  Katman 5  Switch / Host (ISO 8583)            (C)
  Katman 4  ATM-Host Protokolü (NDC/DDC)        (C, E)
  Katman 3  XFS / CDM / CIM Cihaz API           (A, E)  <- sizin olayınız burada başlıyor
  Katman 2  İşletim Sistemi / Endpoint          (D)
  Katman 1  Fiziksel / Donanım                  (E)
```

---

## 4. "Geride Yaşayan / Gelişmekte Olan Ülkeler" Bağlamı

Sorunuzdaki bu nokta önemli — bu pazarlarda risk profili farklıdır. Framework bunları
**denetim kontrol listesi** olarak ele alır (istismar değil, tespit):

- **Eski/yamasız yazılım:** Windows XP/7 tabanlı ATM'ler, EOL XFS sürücüleri.
  → Tarayıcı: sürüm/yama envanteri kontrol listesi.
- **Zayıf/yok şifreleme:** Host bağlantısında TLS yok, MAC yok, açık ISO 8583.
  → Detektör: imzasız/MAC'siz mesaj tespiti (C2).
- **Gecikmeli mutabakat:** Gün sonu batch mutabakatı → geniş istismar penceresi (B2).
  → Detektör: gerçek zamanlı (near-real-time) mutabakat önerisi + gap ölçümü.
- **Fiziksel güvenlik zayıflığı:** Servis modu şifreleri varsayılan, kaset erişimi.
  → Denetim listesi (D2, D3).
- **Zayıf izleme:** SIEM/log korelasyonu yok → dolandırıcılık geç fark edilir.
  → Framework'ün raporlama motoru tam da bu boşluğu doldurur.

Etik not: Belirli ülke/banka adı vererek "şurada şu açık var" demek yerine, framework
**genel zafiyet sınıflarını** ve **kontrol listesini** sunar. Gerçek testler yalnızca
**yazılı yetkiniz olan** sistemlerde yapılır.

---

## 5. Yazılım Taramaları — Ne Olmalı?

Sorunuzdaki "yazılım taramaları ne olmalı" kısmının cevabı, üç katmanlı tarama:

1. **Statik / Envanter Taraması** (pasif, cihaza dokunmadan)
   - XFS sürücü/sürüm envanteri, işletim sistemi yama seviyesi
   - Açık portlar, servis konfigürasyonu (yalnız yetkili ağda)
   - Konfig audit: TLS açık mı, MAC anahtarı rotasyonu, timeout değerleri

2. **Davranışsal / Runtime Taraması** (detektör — canlı izleme)
   - Cihaz olay akışı ↔ ledger hareketi korelasyonu (mutabakat motoru)
   - Anomali: imkânsız hız (aynı ATM'den saniyeler içinde tekrar), tutar sapması
   - Reversal/cancel eşleşme oranı, orphan (yetim) işlem tespiti

3. **Aktif Test Taraması** (yalnız simülatörde veya yetkili test ortamında)
   - Zafiyet kataloğundaki senaryoları otomatik koşan test suite
   - Fuzzing: timeout değerleri, mesaj sırası, cancel timing permütasyonları
   - Sonuç: her senaryo için "tespit edildi mi / edilemedi mi" raporu

---

## 6. Yol Haritası (Aşamalar)

### Faz 0 — İskelet (BU COMMIT'te teslim edildi) ✅
- Proje yapısı, plan, veri modeli
- Çalışan simülatör (XFS/CDM + Core Banking)
- A1 (deposit cancel race) senaryosunu **üreten ve tespit eden** mutabakat motoru
- Çalışan test ve CLI demo

### Faz 1 — Simülatör Derinliği
- Switch/Host katmanı + basit ISO 8583 mesaj modeli
- A2–A5 senaryolarının hepsi
- Timeout/retry ve idempotency modellemesi

### Faz 2 — Detektör Olgunluğu
- Kural motoru (rules.py) — konfigüre edilebilir invariant'lar
- İstatistiksel anomali (hız, tutar, frekans)
- Gerçek zamanlı olay akışı (stream) desteği

### Faz 3 — Tarayıcı & Raporlama
- Zafiyet kataloğu → otomatik test runner
- Konfig/envanter denetim kontrol listeleri
- JSON + Markdown rapor üretimi, önem derecesi (severity) skorlama

### Faz 4 — Entegrasyon & Topluluk
- Gerçek log formatları için adaptör (NDC/DDC, XFS log, ISO 8583 dump)
- SIEM/Splunk/ELK çıktısı
- Dokümantasyon, katkı rehberi, örnek veri setleri (anonim)

---

## 7. Teknoloji Seçimi

- **Dil:** Python 3.11+ (hızlı prototip, geniş güvenlik ekosistemi, okunur)
- **Test:** pytest
- **Veri:** dataclass tabanlı olay modeli; ileride Pydantic
- **CLI:** stdlib argparse (bağımlılık minimal)
- **İleride:** Kafka/Redis stream (gerçek zamanlı), Grafana (dashboard)

Neden Python? Prototip ve topluluk katkısı için ideal. Yüksek hacimli gerçek-zamanlı
üretimde kritik yollar Go/Rust'a taşınabilir.

---

## 8. Etik, Yasal ve Sorumluluk Çerçevesi

Bu bir güvenlik projesidir; sınırlar nettir:

- ✅ Simülasyon ortamında zafiyet üretmek/tespit etmek
- ✅ **Yazılı yetki** ile yapılan pentest/denetimde kullanmak
- ✅ Mutabakat izleme ile dolandırıcılığı erken yakalamak (bankanın kendi sistemi)
- ❌ Yetkisiz sisteme erişim, jackpotting, kart dolandırıcılığı aracı
- ❌ Gerçek istismar payload'ı / malware üretmek

Her repo'ya `RESPONSIBLE_USE.md` ve tarayıcıya "yetki onayı" kontrolü eklenecek.

---

## 9. Hemen Nereden Başlamalı? (Sizin için somut adımlar)

1. **Bu iskeleti çalıştırın:** `python -m atm-security.cli demo` — A1 senaryosunun
   nasıl üretilip yakalandığını görün.
2. **Kendi olayınızı modelleyin:** Kosova olayındaki tam zamanlamayı `simulator/scenarios.py`
   içine bir senaryo olarak ekleyin — böylece detektörünüz onu yakalıyor mu doğrulayın.
3. **Gerçek log formatınızı belirleyin:** Elinizdeki ATM/switch loglarının formatını
   çıkarın; Faz 4 adaptörü buna göre yazılır.
4. **Katalog'u genişletin:** Sahada gördüğünüz her zafiyeti `docs/vulnerability_catalog.md`
   içine test senaryosu olarak ekleyin.
5. **Mutabakat penceresini ölçün:** Detektörle gerçek sistemdeki "cihaz olayı ↔ ledger"
   gecikmesini ölçün — istismar penceresi budur.

---

*Bu doküman yaşayan bir plandır. Her faz sonunda güncellenir.*
