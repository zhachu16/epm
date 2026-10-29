import numpy as np

from epm import function


@function
def amortization_schedule(principal, annual_pct, is_forecast):
    """Mandatory term-loan repayment: a fixed share of the original principal per forecast year."""
    active = np.asarray(is_forecast, dtype=float) > 0
    return np.where(active, principal * annual_pct, 0.0)
