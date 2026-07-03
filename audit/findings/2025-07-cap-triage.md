# İkinci hedef triyajı — CAP (2025-07-cap)

**Hedef:** Sherlock `2025-07-cap` — lending + staking + fractional-reserve
vault (stcUSD, Symbiotic delegation). *Öğrenme çalışması; canlı submission değil.*

**Taranan:** LiquidationLogic, BorrowLogic, Lender, MinterLogic, Minter,
FractionalReserve, PriceOracle, RateOracle, ChainlinkAdapter, ScaledToken.

**Sonuç:** Tarayıcı 13 nokta işaretledi. Elle triyajda scanner'ın iki High
bulgusu da **temizlendi** (yanlış pozitif). Doğrulanmış yeni açık çıkmadı;
derinlemesine bakılacak iki aday alan not edildi. Bu, gerçekçi bir denetim
turudur — bulguların çoğu yanlış pozitiftir ve doğru elemek işin özüdür.

---

## Temizlenen scanner bulguları (yanlış pozitif)

### 1. `LiquidationLogic.liquidate` — "sağlık kontrolü yok" → YANLIŞ
Kontrol var, yardımcı fonksiyonda:
```solidity
ValidationLogic.validateLiquidation(health, ...);   // satır 68
```
Ayrıca `openLiquidation` da `validateOpenLiquidation(health, ...)` çağırıyor.
Sağlık faktörü akış boyunca doğrulanıyor. (İlk hedefteki CoreRouter'la aynı
tür yanlış pozitif — kontrol gövdede değil helper'da.)

### 2. `ChainlinkAdapter` / `PriceOracle` — staleness/sıfır kontrolü
Adapter kendi içinde staleness kontrol etmiyor ama `lastUpdated`'ı döndürüyor
ve `PriceOracle.getPrice` çağıran tarafta kontrol ediyor:
```solidity
if (price == 0 || _isStale(_asset, lastUpdated)) {
    // yedek oracle'a düş
    if (price == 0 || _isStale(...)) revert PriceError(_asset);
}
```
Negatif fiyat adapter'da `0`'a çekiliyor → `price == 0` ile yakalanıyor.
Staticcall başarısızlığı → (0,0) → yine `price == 0`. Sağlam. **Temiz.**

---

## Derinlemesine bakılacak aday alanlar (DOĞRULANMADI)

Bunlar açık DEĞİL — tek oturumda kanıtlanamayan, daha çok bağlam (DebtToken
index mekaniği, ViewLogic, Vault) gerektiren şüpheli noktalar. Dürüstlük için
"aday" olarak işaretlendi.

### A. `BorrowLogic.repay` — minBorrow ayarında olası underflow DoS (düşük güven)
```solidity
uint256 remainingDebt = agentDebt - repaid;
if (remainingDebt > 0 && remainingDebt < reserve.minBorrow) {
    repaid = agentDebt - reserve.minBorrow;   // agentDebt < minBorrow ise underflow → revert
}
```
Eğer `minBorrow`, mevcut `agentDebt`'ten büyük olacak şekilde ayarlanırsa
(admin sonradan yükseltirse), kısmi repay bu satırda underflow ile revert
edebilir → borçlu repay edemez (likidasyona zorlanır). Ön koşul olağandışı bir
admin config; etki DoS. **Doğrulanması için:** minBorrow > agentDebt senaryosu
kurup repay'in revert ettiğini göster.

### B. Chainlink `minAnswer`/`maxAnswer` circuit-breaker kontrolü yok (tartışmalı)
`ChainlinkAdapter.price` yalnızca `latestRoundData` kullanıyor; feed'in
min/max sınırına (circuit breaker) çarpıp çarpmadığını kontrol etmiyor. Aşırı
fiyat hareketinde Chainlink gerçek fiyatı değil clamp'lenmiş min/max değeri
döndürebilir → teminat yanlış değerlenebilir. **Tartışmalı** çünkü çoğu güncel
feed min/maxAnswer'ı kaldırdı; judge'lar sık sık geçersiz sayıyor. Feed
konfigürasyonuna bağlı. **Doğrulanması için:** kapsamdaki feed'lerin gerçekten
min/max bound'u olup olmadığını kontrol et.

---

## Metodoloji dersi

İki hedefte de tarayıcının "liquidation-missing-health-check" bulgusu yanlış
pozitif çıktı çünkü heuristik yalnızca fonksiyon GÖVDESİNE bakıyor, çağrılan
`ValidationLogic`/helper'lara bakmıyor. Bu, aracın bilinen bir sınırı:
**işaretlediği yer inceleme başlangıcıdır, sonuç değil.** Gerçek karar her
zaman çağrı zincirini elle takip etmekle veriliyor.
