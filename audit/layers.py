"""Katman dedektörleri.

Her katman, o kategoriye ait bir dizi heuristik dedektör içerir. Dedektörler
yorum/string'i temizlenmiş kaynak üzerinde regex ile çalışır ve satır numarası,
mesaj ve giderme önerisiyle birlikte Finding üretir.

Heuristik olduğu için bulgular KANIT değil, İNCELEME NOKTASIDIR. Her bulguyu
elle doğrula; asıl açık genelde iş mantığındadır, aracın işaretlediği yerden
başlayarak oku.
"""

from __future__ import annotations

import re
from typing import Iterable

from .scanner import Finding, Severity, SourceFile


def _findall(src: SourceFile, pattern: str, flags: int = 0):
    """Temizlenmiş metinde deseni bulur; (satır, eşleşen_satır_metni) döndürür."""
    clean = src.strip_comments()
    for m in re.finditer(pattern, clean, flags):
        line = clean.count("\n", 0, m.start()) + 1
        snippet = src.lines[line - 1].strip() if line - 1 < len(src.lines) else m.group().strip()
        yield line, snippet


def _has(src: SourceFile, pattern: str, flags: int = 0) -> bool:
    return re.search(pattern, src.strip_comments(), flags) is not None


# --------------------------------------------------------------------------
# Katman 1 — Erişim kontrolü
# --------------------------------------------------------------------------

_PRIVILEGED = r"(selfdestruct|mint|burn|withdraw|setOwner|transferOwnership|upgrade|pause|unpause|setPrice|setOracle|rescue|sweep|initialize)"


def access_missing_modifier(src: SourceFile) -> Iterable[Finding]:
    # Ayrıcalıklı isimli public/external fonksiyonlarda erişim modifier'ı yok
    pat = rf"function\s+\w*{_PRIVILEGED}\w*\s*\([^)]*\)\s*(public|external)([^{{;]*)"
    for line, snippet in _findall(src, pat, re.IGNORECASE):
        tail = snippet.lower()
        if not re.search(r"only\w+|onlyrole|_checkrole|require\s*\(\s*msg\.sender", tail):
            yield Finding(
                layer="1-access", detector="missing-access-modifier",
                severity=Severity.HIGH, line=line, snippet=snippet,
                message="Ayrıcalıklı fonksiyon görünürlüğü public/external ama erişim kontrolü (onlyOwner/onlyRole) görünmüyor.",
                remediation="Fonksiyona uygun erişim modifier'ı ekle veya msg.sender kontrolü yap. Yanlış pozitifse modifier satır dışı tanımlı olabilir, elle doğrula.",
            )


def access_tx_origin(src: SourceFile) -> Iterable[Finding]:
    for line, snippet in _findall(src, r"tx\.origin"):
        yield Finding(
            layer="1-access", detector="tx-origin-auth",
            severity=Severity.MEDIUM, line=line, snippet=snippet,
            message="tx.origin ile yetkilendirme phishing kontratına karşı kırılgandır.",
            remediation="Yetkilendirmede tx.origin yerine msg.sender kullan.",
        )


def access_unprotected_init(src: SourceFile) -> Iterable[Finding]:
    pat = r"function\s+initialize\s*\([^)]*\)\s*(public|external)([^{;]*)"
    for line, snippet in _findall(src, pat):
        if not re.search(r"initializer|onlyowner|require", snippet, re.IGNORECASE):
            yield Finding(
                layer="1-access", detector="unprotected-initializer",
                severity=Severity.HIGH, line=line, snippet=snippet,
                message="initialize() 'initializer' modifier'ı olmadan çağrılabilir görünüyor; tekrar başlatma / ele geçirme riski.",
                remediation="OpenZeppelin 'initializer' modifier'ı kullan ve implementasyon kontratını devre dışı bırak (_disableInitializers).",
            )


# --------------------------------------------------------------------------
# Katman 2 — Aritmetik / muhasebe
# --------------------------------------------------------------------------

def arith_unchecked_block(src: SourceFile) -> Iterable[Finding]:
    for line, snippet in _findall(src, r"unchecked\s*\{"):
        yield Finding(
            layer="2-arithmetic", detector="unchecked-block",
            severity=Severity.LOW, line=line, snippet=snippet,
            message="unchecked bloğu var; içindeki toplama/çıkarmanın taşamayacağını doğrula.",
            remediation="unchecked içindeki her işlemin sınırlarını kanıtla; kullanıcı girdisiyle beslenen işlemleri unchecked dışına al.",
        )


def arith_division_before_mul(src: SourceFile) -> Iterable[Finding]:
    # a / b * c kalıbı → precision loss
    for line, snippet in _findall(src, r"/\s*[\w\.\(\)]+\s*\*"):
        yield Finding(
            layer="2-arithmetic", detector="div-before-mul",
            severity=Severity.MEDIUM, line=line, snippet=snippet,
            message="Çarpmadan önce bölme (a / b * c) precision loss'a yol açar; küçük hesaplar sıfıra yuvarlanabilir.",
            remediation="Önce çarp sonra böl (a * c / b). Faiz/pay hesabında yuvarlama yönünü protokol lehine seç.",
        )


def arith_solc_version(src: SourceFile) -> Iterable[Finding]:
    m = re.search(r"pragma\s+solidity\s+([^;]+);", src.strip_comments())
    if not m:
        return
    ver = m.group(1)
    line = src.text.count("\n", 0, m.start()) + 1
    if re.search(r"0\.[0-7]\.", ver) and "0.8" not in ver:
        yield Finding(
            layer="2-arithmetic", detector="pre-0.8-overflow",
            severity=Severity.MEDIUM, line=line, snippet=m.group().strip(),
            message="Solidity <0.8: aritmetik varsayılan olarak taşmaya karşı KORUMASIZ.",
            remediation="0.8+ kullan veya SafeMath uygula.",
        )


# --------------------------------------------------------------------------
# Katman 3 — Dış çağrı / reentrancy
# --------------------------------------------------------------------------

_STATE_WRITE = r"(balances?|deposits?|shares?|debt|collateral|totalSupply)\s*\[[^\]]*\]\s*(=|-=|\+=)"
_EXTERNAL_CALL = r"\.call\{|\.call\(|\.transfer\(|\.send\(|safeTransfer|IERC20\("


def reentrancy_state_after_call(src: SourceFile) -> Iterable[Finding]:
    """Aynı fonksiyon gövdesinde dış çağrıdan SONRA durum yazımı (CEI ihlali)."""
    clean = src.strip_comments()
    for fm in re.finditer(r"function\s+\w+[^{]*\{", clean):
        body, end = _extract_block(clean, fm.end() - 1)
        if body is None:
            continue
        call_m = re.search(_EXTERNAL_CALL, body)
        if not call_m:
            continue
        write_m = re.search(_STATE_WRITE, body[call_m.end():])
        if write_m and not re.search(r"nonReentrant|reentrancyGuard", body, re.IGNORECASE):
            offset = fm.end() - 1 + call_m.end() + write_m.start()
            line = clean.count("\n", 0, offset) + 1
            snippet = src.lines[line - 1].strip() if line - 1 < len(src.lines) else write_m.group()
            yield Finding(
                layer="3-reentrancy", detector="state-write-after-external-call",
                severity=Severity.CRITICAL, line=line, snippet=snippet,
                message="Dış çağrıdan sonra bakiye/borç durumu güncelleniyor (Checks-Effects-Interactions ihlali) ve nonReentrant koruması yok.",
                remediation="Önce durumu güncelle, sonra dış çağrıyı yap. Ayrıca nonReentrant guard ekle.",
            )


def reentrancy_low_level_call(src: SourceFile) -> Iterable[Finding]:
    for line, snippet in _findall(src, r"\.call\{[^}]*\}\("):
        yield Finding(
            layer="3-reentrancy", detector="low-level-call",
            severity=Severity.LOW, line=line, snippet=snippet,
            message="Düşük seviye .call{value:...} kullanımı; dönüş değeri kontrolünü ve reentrancy'yi doğrula.",
            remediation="Dönüş değerini require ile kontrol et; CEI ve nonReentrant uygula.",
        )


# --------------------------------------------------------------------------
# Katman 4 — Oracle / fiyat
# --------------------------------------------------------------------------

def oracle_spot_price(src: SourceFile) -> Iterable[Finding]:
    # DEX rezervinden anlık fiyat = manipülasyona açık
    for line, snippet in _findall(src, r"getReserves\s*\(|\.balanceOf\([^)]*\)\s*[*/]|price0Cumulative"):
        if "Cumulative" in snippet:
            continue
        yield Finding(
            layer="4-oracle", detector="spot-price-source",
            severity=Severity.HIGH, line=line, snippet=snippet,
            message="Anlık rezerv/bakiye üzerinden fiyat türetimi flash-loan ile manipüle edilebilir.",
            remediation="TWAP veya Chainlink gibi manipülasyona dirençli oracle kullan; tek blokta değişebilen spot fiyata dayanma.",
        )


def oracle_no_staleness_check(src: SourceFile) -> Iterable[Finding]:
    if _has(src, r"latestAnswer\s*\("):
        line, snippet = next(_findall(src, r"latestAnswer\s*\("), (0, "latestAnswer()"))
        yield Finding(
            layer="4-oracle", detector="chainlink-latestAnswer",
            severity=Severity.MEDIUM, line=line, snippet=snippet,
            message="Chainlink latestAnswer() eskimiş/geçersiz veriyi yakalamaz.",
            remediation="latestRoundData() kullan ve updatedAt (staleness) ile answeredInRound kontrolü yap; fiyat > 0 doğrula.",
        )


# --------------------------------------------------------------------------
# Katman 5 — Ekonomik / protokol mantığı (lending odaklı)
# --------------------------------------------------------------------------

def logic_first_depositor(src: SourceFile) -> Iterable[Finding]:
    # totalSupply == 0 dalı yoksa vault share inflation riski
    if _has(src, r"(shares?|totalShares|totalSupply)") and _has(src, r"deposit|mint"):
        if not _has(src, r"totalSupply\s*==\s*0|totalShares\s*==\s*0|_decimalsOffset|1e3|MINIMUM_LIQUIDITY"):
            line, snippet = next(_findall(src, r"function\s+(deposit|mint)\b"), (0, "deposit"))
            yield Finding(
                layer="5-logic", detector="first-depositor-inflation",
                severity=Severity.HIGH, line=line, snippet=snippet,
                message="Vault/share mantığı var ama ilk-depozitör (share inflation) korumasına dair iz yok.",
                remediation="İlk depozitte minimum likidite kilitle veya decimals-offset (virtual shares) uygula; ERC4626 saldırısını gözden geçir.",
            )


def logic_liquidation_no_health_check(src: SourceFile) -> Iterable[Finding]:
    if _has(src, r"function\s+liquidat", re.IGNORECASE):
        line, snippet = next(_findall(src, r"function\s+liquidat\w*", re.IGNORECASE), (0, "liquidate"))
        # sağlık faktörü / collateral kontrolü var mı?
        clean = src.strip_comments()
        fm = re.search(r"function\s+liquidat\w*[^{]*\{", clean, re.IGNORECASE)
        healthy = False
        if fm:
            body, _ = _extract_block(clean, fm.end() - 1)
            if body and re.search(r"healthFactor|isHealthy|collateralValue|require\s*\(", body, re.IGNORECASE):
                healthy = True
        if not healthy:
            yield Finding(
                layer="5-logic", detector="liquidation-missing-health-check",
                severity=Severity.HIGH, line=line, snippet=snippet,
                message="Likidasyon fonksiyonunda sağlık faktörü / borç uygunluk kontrolü görünmüyor.",
                remediation="Likidasyondan önce pozisyonun gerçekten likidite edilebilir olduğunu (health factor < 1) doğrula.",
            )


def logic_fee_on_transfer(src: SourceFile) -> Iterable[Finding]:
    # transferFrom sonrası bakiye farkı ölçülmüyorsa fee-on-transfer token uyumsuzluğu
    if _has(src, r"transferFrom\s*\(") and not _has(src, r"balanceOf\([^)]*\)\s*[-]"):
        line, snippet = next(_findall(src, r"transferFrom\s*\("), (0, "transferFrom"))
        yield Finding(
            layer="5-logic", detector="fee-on-transfer-assumption",
            severity=Severity.MEDIUM, line=line, snippet=snippet,
            message="transferFrom sonrası gerçekte gelen miktar ölçülmüyor; fee-on-transfer/rebasing token muhasebeyi bozabilir.",
            remediation="Transferden önce/sonra balanceOf farkını ölç ve gerçek gelen miktarı kullan.",
        )


# --------------------------------------------------------------------------
# Katman 6 — Başlatma / upgrade
# --------------------------------------------------------------------------

def upgrade_delegatecall(src: SourceFile) -> Iterable[Finding]:
    for line, snippet in _findall(src, r"\.delegatecall\s*\("):
        yield Finding(
            layer="6-upgrade", detector="delegatecall",
            severity=Severity.HIGH, line=line, snippet=snippet,
            message="delegatecall hedefi güvenilir değilse storage/erişim ele geçirilebilir.",
            remediation="delegatecall hedefinin sabit/güvenilir olduğunu doğrula; kullanıcı kontrollü adrese delegatecall yapma.",
        )


def upgrade_selfdestruct(src: SourceFile) -> Iterable[Finding]:
    for line, snippet in _findall(src, r"selfdestruct\s*\("):
        yield Finding(
            layer="6-upgrade", detector="selfdestruct",
            severity=Severity.MEDIUM, line=line, snippet=snippet,
            message="selfdestruct kontratı yok edebilir; proxy implementasyonunda fon kilitlenmesine yol açabilir.",
            remediation="selfdestruct'a gerçekten ihtiyaç olduğunu ve erişim kontrolünü doğrula.",
        )


# --------------------------------------------------------------------------
# Yardımcı: dengeli süslü parantez bloğu çıkar
# --------------------------------------------------------------------------

def _extract_block(text: str, open_brace_index: int):
    """open_brace_index '{' konumundan itibaren dengeli bloğu döndürür."""
    if open_brace_index >= len(text) or text[open_brace_index] != "{":
        return None, open_brace_index
    depth = 0
    for i in range(open_brace_index, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1:i], i
    return text[open_brace_index + 1:], len(text)


# --------------------------------------------------------------------------
# Katman kayıt tablosu
# --------------------------------------------------------------------------

LAYERS: dict[str, list] = {
    "1-access": [access_missing_modifier, access_tx_origin, access_unprotected_init],
    "2-arithmetic": [arith_unchecked_block, arith_division_before_mul, arith_solc_version],
    "3-reentrancy": [reentrancy_state_after_call, reentrancy_low_level_call],
    "4-oracle": [oracle_spot_price, oracle_no_staleness_check],
    "5-logic": [logic_first_depositor, logic_liquidation_no_health_check, logic_fee_on_transfer],
    "6-upgrade": [upgrade_delegatecall, upgrade_selfdestruct],
}

LAYER_TITLES = {
    "1-access": "Erişim kontrolü",
    "2-arithmetic": "Aritmetik / muhasebe",
    "3-reentrancy": "Dış çağrı / reentrancy",
    "4-oracle": "Oracle / fiyat",
    "5-logic": "Ekonomik / protokol mantığı",
    "6-upgrade": "Başlatma / upgrade",
}
