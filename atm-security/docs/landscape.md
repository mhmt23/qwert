# Ekosistem Taraması — Açık & Kapalı Kaynak ATM/Bankacılık Sistemleri

> Bu doküman, framework'ü "daha kapsamlı" yapmak için mevcut açık/kapalı kaynak
> projeleri, standartları ve onların hangi katmanları kapsadığını inceler.
> Amaç: tekerleği yeniden icat etmemek, boşlukları (özellikle **mutabakat/desync
> tespiti**) doldurmak ve gerçek log formatlarıyla uyumlu olmak.

## 1. Standartlar & Referans Mimariler

| Proje / Standart | Katman | Lisans | Bizim için önemi |
|---|---|---|---|
| **CEN/XFS (WOSA/XFS)** | Cihaz API (klasik) | Açık standart | ATM donanım komutlarının fiili standardı. Olay adlarımız (CashAccepted, Dispense) buradan türetilmeli. |
| **XFS4IoT** | Cihaz API (yeni nesil) | Açık (CEN, GitHub'da) | REST/JSON tabanlı, OS-bağımsız, "built-in security". Gelecek adaptörümüz bunun mesajlarını parse etmeli. |
| **KAL XFS4IoT SP-Dev** | XFS SP servis geliştirme | MIT (açık) | XFS4IoT servis sağlayıcı iskeleti. Simülatörümüz için gerçekçi olay üretici referansı. |
| **xfspp** | CEN XFS C++ çerçevesi | Açık | Klasik XFS'in C++ implementasyonu — mesaj yapısı referansı. |
| **ISO 8583** | Switch/host mesajlaşma | Açık standart | Reversal (0400/0420), response code (F39), MAC. Switch adaptörümüzün temeli. |
| **NDC / DDC (Diebold/NCR)** | ATM-host protokolü | Kapalı (üretici) | Gerçek ATM log formatı. Adaptör Faz 4'te bunları normalize etmeli. |

## 2. Simülatörler & Emülatörler

| Proje | Ne yapar | Bizim farkımız |
|---|---|---|
| **electron-atm** (timgabets) | Açık kaynak NDC ATM emülatörü (JS/Electron). Kart, PIN, dağıtım akışını simüle eder. | O bir **ATM davranış** emülatörü; biz **mutabakat/desync tespiti** yapıyoruz. İyi bir **olay kaynağı** olarak entegre edilebilir. |
| **Serquo / KAL ATM Simülatörü** | Ticari CEN/XFS test simülatörleri | Kapalı, pahalı. Biz açık kaynak, tespit odaklı ve XFS↔Core desync'e özel. |
| **Ticari test araçları (Paragon, FIS, Clear2Pay)** | Uçtan uca ödeme test setleri | Kapalı. Bizim niş: cihaz-fiziksel ↔ ledger-mantıksal invariant kontrolü. |

## 3. Dolandırıcılık Tespiti / İzleme

| Proje | Katman | Bizim farkımız |
|---|---|---|
| **Jube** (aml-fraud-transaction-monitoring) | Açık kaynak AML/fraud, ML + kural motoru, velocity/threshold | Genel işlem izleme. Biz **ATM'ye özgü fiziksel-mantıksal mutabakat** ekliyoruz — Jube'nin kural motoruna bizim bulgularımız beslenebilir. |
| **Feedzai / DataVisor / Unit21** | Ticari fraud platformları | Kapalı, kart/hesap odaklı. XFS/CDM fiziksel katmanını görmezler — bizim boşluğumuz tam burası. |

## 4. Bilinen Saldırı/Zafiyet Kaynakları (referans)

- **Jackpotting / Ploutus / black box:** XFS katmanına yetkisiz komut → banka
  yetkilendirmesi olmadan dağıtım. 2025'te ABD'de kayıplar 20M$'ı aştı, 2020'den
  bu yana ~1900 saldırı. *(Bizim projede: XFS'e yetkisiz erişimin İZİNİ değil, bu
  komutların ledger'da karşılığı olmayan dağıtım olarak TESPİTİ.)*
- **ISO 8583 socket zafiyetleri:** reversal PAN doğrulaması yok, refund tutarı
  orijinali aşabiliyor, MAC eksik → mesaj değiştirme, replay.
- **Timeout/clock desync:** terminal (10s), switch (3s), issuer (4s) saatleri
  koordinesiz → aynı işlem timeout/approve/reversal olarak farklı görünür.
  Ara durumlar (LATE_APPROVED, REVERSAL_PENDING) modellenmezse mutabakat "tahmin
  eder". *Bu, sizin Kosova olayınızın protokol-katmanı kardeşidir.*

## 5. ATM-Guard'ın Konumu (boşluk analizi)

Yukarıdaki hiçbir açık kaynak proje **"cihazın fiziksel nakit hareketi ile core
banking ledger'ının mantıksal hareketi arasındaki invariant'ı"** doğrulamıyor.
Simülatörler ATM'yi taklit eder, fraud araçları kart/hesabı izler, ama XFS↔Core
**desync tespiti** boşlukta. ATM-Guard tam bu boşluğu doldurur ve diğerleriyle
**entegre olur**: electron-atm olay üretir → ATM-Guard mutabakatı denetler →
bulgular Jube kural motoruna gider.

## Kaynaklar
- KAL XFS4IoT SP-Dev — https://github.com/KAL-ATM-Software/KAL_XFS4IoT_SP-Dev
- XFS4IoT spec — https://xfs4iot.github.io/Specifications-Preview.github.io/html/index.html
- xfspp — https://github.com/becrux/xfspp
- electron-atm — https://github.com/timgabets/electron-atm
- Jube fraud monitoring — https://github.com/jube-home/aml-fraud-transaction-monitoring
- ISO 8583 payment socket vulns — https://m4kr0.vercel.app/posts/iso-8583-under-fire-finding-vulnerabilities-in-a-payment-socket
- Payment switch reconciliation/failover — Medium (Umut Akbulut)
- ATM jackpotting 2025 surge — https://www.darkreading.com/cyber-risk/atm-jackpotting-attacks-surged-2025
- FBI/IC3 jackpotting advisory — https://www.ic3.gov/CSA/2026/260219.pdf
