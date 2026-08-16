from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommunityHolding:
    symbol: str
    portfolio_percent: float


@dataclass(frozen=True)
class CommunityMember:
    handle: str
    name: str
    manager: str
    reporting_period: str
    report_date: str
    holdings: tuple[CommunityHolding, ...]
    relationship_note: str


# Default members derived from famous-investor-holdings.json. These are reported
# manager portfolios, not personal or current holdings.
DEFAULT_MEMBERS = (
    CommunityMember(
        "warrenbuffett", "Warren Buffett", "Berkshire Hathaway", "2026-Q2", "2026-06-30",
        tuple(CommunityHolding(*item) for item in (("AAPL", 22.04), ("AXP", 17.14), ("KO", 10.86), ("GOOGL", 9.41), ("BAC", 9.20), ("CVX", 4.67), ("OXY", 4.30), ("CB", 3.90), ("MCO", 3.73), ("GOOG", 3.21))),
        "Berkshire Hathaway portfolio, not Warren Buffett's personal account.",
    ),
    CommunityMember(
        "billackman", "Bill Ackman", "Pershing Square Capital Management", "2026-Q2", "2026-06-30",
        tuple(CommunityHolding(*item) for item in (("UBER", 12.72), ("BN", 12.58), ("MSFT", 11.89), ("AMZN", 10.49), ("HHH", 10.23), ("QSR", 9.62), ("META", 9.25), ("V", 5.76), ("MA", 5.61), ("SPGI", 5.43))),
        "Pershing Square portfolio, not Bill Ackman's personal account.",
    ),
    CommunityMember(
        "michaelburry", "Michael Burry", "Scion Asset Management", "2025-Q3", "2025-09-30",
        tuple(CommunityHolding(*item) for item in (("MOH", 43.49), ("LULU", 32.35), ("SLM", 24.16))),
        "Scion portfolio, not Michael Burry's personal account.",
    ),
    CommunityMember(
        "davidtepper", "David Tepper", "Appaloosa Management", "2026-Q2", "2026-06-30",
        tuple(CommunityHolding(*item) for item in (("AMZN", 15.95), ("MU", 15.06), ("TSM", 10.55), ("GOOG", 8.75), ("UBER", 7.43), ("EWY", 6.55), ("META", 5.09), ("VST", 4.70), ("NVDA", 4.08), ("NRG", 3.44))),
        "Appaloosa portfolio, not David Tepper's personal account.",
    ),
    CommunityMember(
        "carlicahn", "Carl Icahn", "Icahn Capital Management", "2026-Q2", "2026-06-30",
        tuple(CommunityHolding(*item) for item in (("IEP", 53.95), ("CVI", 23.73), ("UAN", 5.62), ("CTRI", 5.25), ("IFF", 4.10), ("ECHO", 1.73), ("JBLU", 1.43), ("MNRO", 1.05), ("CZR", 0.89), ("SD", 0.84))),
        "Icahn Capital portfolio, not Carl Icahn's personal account.",
    ),
    CommunityMember(
        "raydalio", "Ray Dalio", "Bridgewater Associates", "2026-Q1", "2026-03-31",
        tuple(CommunityHolding(*item) for item in (("SPY", 12.67), ("IVV", 7.81), ("AMZN", 4.08), ("NVDA", 3.65), ("GOOGL", 2.56), ("AVGO", 2.54), ("MU", 2.23), ("MSFT", 1.79), ("GEV", 1.69), ("TSM", 1.62))),
        "Bridgewater's institutional portfolio, not Ray Dalio's personal account.",
    ),
)
