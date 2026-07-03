# PoC — Lend cross-chain likidasyonda shortfall kontrolü eksik

Bu klasör, `audit/findings/2025-05-lend-crosschain-liquidation.md` içindeki aday
bulguyu **çalıştırılabilir bir Foundry testiyle** ispatlar.

## Bu ortamda neden çalıştırılamadı

PoC bu oturumda **çalıştırılamadı**: organizasyonun ağ (egress) politikası
Foundry toolchain indirmesini engelliyor —
- `foundry.paradigm.xyz` → 403 (policy denial),
- `foundry-rs/foundry` GitHub release'leri → oturum yalnızca `mhmt23/qwert`
  reposuna kapsamlı olduğu için erişilemiyor.

Bu bir politika kararıdır, atlatılmaya çalışılmamıştır. Kod **yerelde çalışmaya
hazırdır**; aşağıdaki adımlarla Foundry'nin kurulu olduğu bir makinede çalışır.

## Nasıl çalıştırılır

```bash
# 1) Foundry kur (yerel makinende)
curl -L https://foundry.paradigm.xyz | bash && foundryup

# 2) Yarışma reposunu klonla
git clone https://github.com/sherlock-audit/2025-05-lend-audit-contest
cd 2025-05-lend-audit-contest/Lend-V2
forge install   # bağımlılıklar (OpenZeppelin, LayerZero, forge-std)

# 3) Bu PoC dosyasını test klasörüne kopyala
cp /path/to/qwert/audit/poc/PoC_CrossChainLiquidationNoShortfall.t.sol test/

# 4) Çalıştır
forge test --match-contract PoC_CrossChainLiquidationNoShortfall -vvv
```

## Ne göreceksin (beklenen sonuç)

İki test, açığı asimetriyle kanıtlar:

| Test | Senaryo | Beklenen | Anlamı |
|---|---|---|---|
| `test_poc_crosschain_liquidation_of_HEALTHY_position_succeeds` | Sağlıklı cross-chain pozisyon | **PASS** (revert etmez, teminat seize edilir) | AÇIK: batık olmayan pozisyon likidite edildi |
| `test_poc_samechain_liquidation_of_healthy_REVERTS` | Aynı senaryo, aynı-zincir yol | **PASS** (`Insufficient shortfall` ile revert) | Doğru davranış: aynı-zincir yol korur |

Yani aynı sağlıklı borçlu:
- **cross-chain** yolda likidite edilebiliyor (olmamalı),
- **same-chain** yolda korunuyor (olması gereken).

Bu asimetri, cross-chain yolunda shortfall kontrolünün düştüğünün doğrudan
kanıtıdır.

## Harness notu

PoC'nin `setUp()`'ı ve `_supplyA`/`_supplyB` yardımcıları, yarışmanın kendi
`test/TestLiquidations.t.sol` dosyasından **birebir** alınmıştır; böylece resmi
test altyapısıyla %100 uyumludur. Tek fark: PoC senaryosu teminat fiyatını
**düşürmez** — pozisyon kasıtlı olarak sağlıklı tutulur.

## Sonraki adım (gerçek bir bounty olsaydı)

Bu bitmiş bir yarışma; burası öğrenme amaçlı. Gerçek bir programda sıra:
1. PoC'yi çalıştırıp PASS gördüğünü doğrula.
2. Etkiyi ölç (ne kadar teminat, hangi ön koşullar).
3. `findings/*.md` + bu PoC'yi platformun formatında **raporla**.
4. Fonu asla mainnet'te hareket ettirme — kanıt fork/local'de yeterli.
