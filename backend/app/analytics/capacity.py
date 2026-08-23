"""Wired headroom: what a switch is being asked to carry and to power.

Both answers are arithmetic over what the Integration API already reports per
port — negotiated speed, PoE standard and class — against one number per model
that the API does not report at all. A model missing from the budget table
produces no answer rather than a guessed one.
"""

from __future__ import annotations

from dataclasses import dataclass

# Maximum a port can draw at each PoE class, per IEEE. The console reports the
# standard and the type, not the draw, so this is the worst case a port is
# entitled to rather than what it is using — which is the right number for a
# budget question and the wrong one for a bill.
POE_CLASS_WATTS: dict[tuple[str, int | None], float] = {
    ("802.3AF", None): 15.4,
    ("802.3AT", None): 30.0,
    ("802.3BT", 3): 60.0,
    ("802.3BT", 4): 90.0,
}
# Fallback by standard alone, for a console that reports no type.
POE_STANDARD_WATTS: dict[str, float] = {
    "802.3AF": 15.4,
    "802.3AT": 30.0,
    "802.3BT": 60.0,
}

# Total PoE the model can deliver, from Ubiquiti's published tech specs. Only
# models whose figure is confirmed appear here: an unlisted model yields None,
# and the rules over it stay quiet rather than compare against a guess. Adding
# a model is a one-line change once its datasheet has been checked.
MODEL_POE_BUDGET_W: dict[str, float] = {
    "USW-24-POE": 95.0,
    "USW-48-POE": 195.0,
    "USW-PRO-24-POE": 400.0,
    "USW-PRO-48-POE": 600.0,
    "USW-LITE-8-POE": 52.0,
    "USW-LITE-16-POE": 45.0,
    "USW-ENTERPRISE-24-POE": 400.0,
}


def port_poe_watts(standard: str | None, poe_type: int | None, powering: bool) -> float:
    """Worst-case draw this port is entitled to, 0 when it powers nothing.

    `powering` is about delivery, not capability: a PoE port with nothing
    attached is entitled to nothing, however it is configured.
    """
    if not powering or not standard:
        return 0.0
    key = standard.upper().replace(" ", "").replace("-", "")
    key = key if key.startswith("802.3") else f"802.3{key}"
    return POE_CLASS_WATTS.get((key, poe_type)) or POE_STANDARD_WATTS.get(key, 0.0)


def model_poe_budget_w(model: str | None) -> float | None:
    """Published PoE budget for this model, or None if it is not in the table."""
    if not model:
        return None
    return MODEL_POE_BUDGET_W.get(model.upper())


@dataclass(frozen=True)
class PoeLoad:
    powered_ports: int
    demand_w: float
    budget_w: float | None

    @property
    def utilization(self) -> float | None:
        if not self.budget_w:
            return None
        return self.demand_w / self.budget_w


def poe_load(
    ports: list[tuple[str | None, int | None, bool]], model: str | None
) -> PoeLoad:
    """Committed PoE against the model's budget, from (standard, type, powering)."""
    draws = [port_poe_watts(*port) for port in ports]
    return PoeLoad(
        powered_ports=sum(1 for w in draws if w > 0),
        demand_w=round(sum(draws), 1),
        budget_w=model_poe_budget_w(model),
    )


@dataclass(frozen=True)
class Oversubscription:
    uplink_mbps: int
    downstream_mbps: int
    ratio: float | None


def oversubscription(uplink_mbps: int | None, downstream_mbps: int) -> Oversubscription:
    """How much link speed sits behind the uplink, per unit of uplink.

    A ratio above 1 is normal and not by itself a fault -- switches are built on
    the assumption that not everything transmits at once. It is the size of the
    ratio that says whether that assumption is still safe.
    """
    return Oversubscription(
        uplink_mbps=uplink_mbps or 0,
        downstream_mbps=downstream_mbps,
        ratio=(downstream_mbps / uplink_mbps) if uplink_mbps else None,
    )
