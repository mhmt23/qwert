# Aday bulgu — Cross-chain likidasyonda shortfall (batıklık) kontrolü eksik

**Hedef:** Sherlock `2025-05-lend-audit-contest` — Lend (Compound V2 + LayerZero
cross-chain lending). *Yarışma sona ermiştir; bu bir ÖĞRENME/METODOLOJI
çalışmasıdır, canlı submission değil.*

**Kontratlar:** `CrossChainRouter.sol`, `CoreRouter.sol`, `LendStorage.sol`
**Önem (tahmini):** High / Critical
**Durum:** Aday — Foundry fork PoC ile doğrulanması gerekir.

---

## İddia

Cross-chain likidasyon yolu (`CrossChainRouter.liquidateCrossChain`),
borçlunun gerçekten likidite edilebilir (teminat < borç, yani shortfall)
olduğunu **hiçbir noktada doğrulamıyor**. Aynı-zincir yolunda bu kontrol açıkça
var; cross-chain yolunda düşmüş. Sonuç: **sağlıklı bir borçlu haksız yere
likidite edilebilir**, teminatı ele geçirilebilir.

## Kanıt izi (kod okuması)

### Aynı-zincir yolunda kontrol VAR
`CoreRouter.liquidateBorrowInternal` → `liquidateBorrowAllowedInternal`:

```solidity
// CoreRouter.sol
require(borrowedAmount > collateral, "Insufficient shortfall");
```

Yani borçlu ancak batıksa (borç > teminat) likidite edilebilir.

### Cross-chain yolunda kontrol YOK
`CrossChainRouter.liquidateCrossChain`
→ `_validateAndPrepareLiquidation` → `_executeLiquidation`
→ `_prepareLiquidationValues` → `_executeLiquidationCore` → `_send(... CrossChainLiquidationExecute)`
→ (Chain A) `_handleLiquidationExecute`

Bu zincirde yapılan tüm kontroller:
- `_validateAndPrepareLiquidation`: `borrower != msg.sender`, `repayAmount > 0`,
  pozisyon var mı, `repayAmount <= getMaxLiquidationRepayAmount(...)`.
- `_prepareLiquidationValues`: `maxLiquidation = currentBorrow * closeFactor`.
- `_executeLiquidationCore`: seize token hesabı + mesaj gönderimi.
- `_handleLiquidationExecute` (Chain A): mesaja güvenip teminatı ele geçirir.

`getMaxLiquidationRepayAmount` (LendStorage.sol) yalnızca close-factor uygular:

```solidity
uint256 maxRepay = (currentBorrow * closeFactorMantissa) / 1e18;
return maxRepay; // shortfall / health-factor kontrolü YOK
```

Hiçbir adımda `borrowed > collateral` (veya
`getHypotheticalAccountLiquidityCollateral` ile shortfall) doğrulaması yok.

## Etki

- Batık olmayan (sağlıklı) bir cross-chain borç pozisyonu likidite edilebilir.
- Likidatör, close-factor kadar borcu "geri ödeyip" karşılığında likidasyon
  bonusuyla teminatı ele geçirir — borçlu zarara uğrar.
- Lending protokolünde temel invariant ("yalnızca batık pozisyon likidite
  edilir") ihlal edilir → doğrudan fon kaybı.

## Ön koşullar / varsayımlar (PoC'de doğrulanacak)

1. Borçlunun bir cross-chain borç pozisyonu var ve sağlıklı (teminat > borç).
2. `liquidateCrossChain` shortfall olmadan çağrılabiliyor.
3. Chain A `_handleLiquidationExecute` mesajı ek bir sağlık kontrolü yapmadan
   uyguluyor (okumadan görünüşe göre yapmıyor).

## Doğrulama planı (Foundry, mainnet fork YOK gerekmez — local deploy)

1. Compound V2 + bu router'ları local iki "chain" (iki LayerZero endpoint mock)
   ile kur.
2. Sağlıklı bir cross-chain borç aç (teminat > borç).
3. `liquidateCrossChain`'i çağır; işlemin **revert etmeden** geçtiğini ve
   teminatın seize edildiğini göster.
4. Aynı senaryoyu aynı-zincir `liquidateBorrow` ile dene; "Insufficient
   shortfall" ile revert ettiğini göster → asimetriyi kanıtla.

## Öneri (giderme)

Cross-chain yolunda da, seize mesajı gönderilmeden önce borçlunun toplam
(cross-chain dahil) likiditesini hesaplayıp shortfall şartını zorunlu kıl:

```solidity
(uint256 borrowed, uint256 collateral) =
    lendStorage.getHypotheticalAccountLiquidityCollateral(borrower, LToken(borrowedlToken), 0, 0);
require(borrowed > collateral, "Insufficient shortfall");
```

## Metodoloji notu

Tarayıcı (`5-logic/liquidation-missing-health-check`) hem CoreRouter'ı hem
CrossChainRouter'ı işaretledi. Elle doğrulamada:
- CoreRouter → **yanlış pozitif** (kontrol yardımcı fonksiyonda var).
- CrossChainRouter → **gerçek aday** (kontrol hiçbir yerde yok).

Bu, "heuristik = inceleme noktası, kanıt değil" ilkesinin somut örneği:
araç iki yeri de gösterdi, karar elle okumayla verildi.
