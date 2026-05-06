"""Chain providers — pluggable data-source adapters.

The simulator's ``simulate_fills`` convenience wrapper queries data through a
``ChainProvider``. Implementations:

- ``InMemoryChainProvider`` — backing list of Quote objects; for tests and
  small offline runs. Always available, zero dependencies.
- ``CSVChainProvider`` — loads from a tidy CSV file. Always available.
- (community) ``QuestDBChainProvider``, ``PolygonChainProvider``, etc. —
  shipped as optional extras or as separate user packages.

For per-bar primitive use (``simulate_fill``), no provider is needed: the
caller passes the chain dict directly.
"""

from fillsim.providers.base import ChainProvider, Quote
from fillsim.providers.csv import CSVChainProvider
from fillsim.providers.memory import InMemoryChainProvider

__all__ = ["ChainProvider", "Quote", "CSVChainProvider", "InMemoryChainProvider"]
