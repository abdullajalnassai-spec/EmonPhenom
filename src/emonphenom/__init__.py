"""EmonPhenom -- the Munder Difflin Paper Company operations layer.

The package is split into a deterministic domain core and an agent layer that
sits on top of it:

    catalog / inventory / pricing / quoting / fulfillment / ledger
        -> plain Python, no I/O beyond SQLite, fully testable offline

    agents/
        -> specialists that own one domain area each, plus an orchestrator
           that routes a request to them

    requests_nl
        -> optional Claude-backed parsing of free-text customer enquiries
           into the structured requests the agents consume
"""

from emonphenom.catalog import CATALOG, Product
from emonphenom.pricing import Quote, QuoteLine

__all__ = ["CATALOG", "Product", "Quote", "QuoteLine"]
__version__ = "0.1.0"
