"""Fantasy NBA GM — historical data pipeline, decision engine, and recommendation log.

The single hardest correctness property in this milestone is **no lookahead bias**:
any read can be constrained to "known as of date D," and results/availability/roster
moves are never visible before the day they became known. See ``fantasy_gm.data.store``.
"""

__version__ = "0.1.0"
