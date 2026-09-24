"""finam_connector — единый коннектор к Finam Trade API + расчёты."""
from .client import FinamConnector
from .analytics import Analytics
from .viz import FinamViz

__version__ = "0.1.0"
__all__ = ["FinamConnector", "Analytics", "FinamViz"]
