"""Katman bazlı Solidity denetim aracı.

Bir akıllı kontrat kaynağını (.sol) alır, güvenlik açıklarını altı katmana
ayrılmış sistematik dedektörlerle tarar ve bulguları önem sırasına göre
raporlar. Amaç: bug bounty / audit yarışması öncesi hızlı, tekrarlanabilir
bir ilk geçiş sağlamak. Fon hareketi yapmaz; yalnızca kaynak kodu okur.
"""

from .scanner import Scanner, Finding, Severity
from .layers import LAYERS

__all__ = ["Scanner", "Finding", "Severity", "LAYERS"]
