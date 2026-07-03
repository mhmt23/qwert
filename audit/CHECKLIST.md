# Katman bazlı denetim checklist'i (lending odaklı)

Otomatik tarayıcı ilk geçişi yapar; asıl açıklar iş mantığındadır ve elle
bulunur. Bu checklist'i her kontrat için sırayla uygula. Tarayıcının
işaretlediği satırdan başla, ama işaretlemediği yerleri de bu listeyle tara.

Kaynak referanslar: bu maddeler genel güvenlik bilgisinden derlenmiştir;
somut protokolde her zaman kodun kendisini oku.

---

## Katman 1 — Erişim kontrolü
- [ ] Her `public`/`external` fonksiyonun çağrılabilirlik amacı doğru mu? (kim çağırmalı?)
- [ ] Ayrıcalıklı işlemler (mint, pause, setOracle, upgrade, sweep) korumalı mı?
- [ ] `onlyOwner`/rol kontrolleri gerçekten uygulanıyor mu, yoksa sadece tanımlı mı?
- [ ] `initialize()` yalnızca bir kez ve yetkiyle çağrılabiliyor mu?
- [ ] Yetkilendirmede `tx.origin` kullanılıyor mu? (kullanılmamalı)
- [ ] Sahiplik transferi iki adımlı mı? (yanlış adrese kilitlenme riski)

## Katman 2 — Aritmetik / muhasebe
- [ ] Solidity 0.8+ mı? Değilse SafeMath var mı?
- [ ] `unchecked` blokları gerçekten taşamaz mı?
- [ ] Çarpmadan önce bölme (precision loss) var mı?
- [ ] Yuvarlama yönü her zaman protokol lehine mi? (kullanıcı lehine yuvarlama sömürülür)
- [ ] Farklı decimals'lı tokenlar doğru normalize ediliyor mu?
- [ ] Share ↔ asset dönüşümü her iki yönde tutarlı mı?

## Katman 3 — Dış çağrı / reentrancy
- [ ] Checks-Effects-Interactions sırası korunuyor mu? (önce durum, sonra çağrı)
- [ ] Durum değiştiren, dış çağrı yapan fonksiyonlarda `nonReentrant` var mı?
- [ ] Read-only reentrancy riski var mı? (view fonksiyonu tutarsız durum okur)
- [ ] Cross-function / cross-contract reentrancy mümkün mü?
- [ ] ERC777/callback'li tokenlar beklenmedik yeniden giriş açar mı?

## Katman 4 — Oracle / fiyat
- [ ] Fiyat anlık spot'tan mı geliyor? (flash-loan manipülasyonu)
- [ ] TWAP penceresi yeterince uzun mu?
- [ ] Chainlink kullanılıyorsa `latestRoundData` + staleness (`updatedAt`) kontrolü var mı?
- [ ] Fiyat `0` veya negatif dönerse ne oluyor?
- [ ] L2'de sequencer uptime feed kontrolü var mı?

## Katman 5 — Ekonomik / protokol mantığı
- [ ] İlk-depozitör / share inflation koruması var mı? (ERC4626)
- [ ] Likidasyon gerçekten sağlıksız pozisyonda mı tetikleniyor?
- [ ] Kısmi likidasyon ve likidasyon teşviki (bonus) sömürülebilir mi?
- [ ] Fee-on-transfer / rebasing token muhasebeyi bozar mı?
- [ ] Faiz tahakkuku her etkileşimden önce güncelleniyor mu?
- [ ] Borç/teminat oranı sınırları tüm yollarda uygulanıyor mu?
- [ ] Kötü borç (bad debt) senaryosunda sistem ne yapıyor?
- [ ] Flash-loan ile atomik olarak deposit→borrow→manipulate→profit mümkün mü?

## Katman 6 — Başlatma / upgrade
- [ ] Proxy implementasyonu `_disableInitializers` ile kilitli mi?
- [ ] Storage layout upgrade'ler arası uyumlu mu? (collision)
- [ ] `delegatecall` hedefi güvenilir/sabit mi?
- [ ] `selfdestruct` var mı ve gerekçesi ne?
- [ ] Yeni implementasyon initialize edilmeden kalabilir mi?

---

## Bir bulgudan sonra: sorumlu süreç
1. Bulguyu **mainnet fork'unda** exploit yazarak doğrula (gerçek fon çekme YOK).
2. Etkiyi ölç: ne kadar fon risk altında, hangi ön koşullar gerekli.
3. Programın kurallarına göre **raporla** (Immunefi/Code4rena/Sherlock).
4. Sahibi düzeltene kadar detayları **kamuya açma**.
5. Fonu asla hareket ettirme, tehdit/pazarlık yapma — ödül kuralına göre gelir.
