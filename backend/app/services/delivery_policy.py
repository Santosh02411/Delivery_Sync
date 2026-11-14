"""
The ONE place that decides which delivery status changes are allowed — used by the offline sync
resolver (services/conflict_resolver.py), the online PATCH /deliveries/{id}, and the customer
self-service cancel, so every path obeys the same rules.

WHO MAY DO WHAT
  agent        from any NON-final status to picked_up / out_for_delivery / delivered / failed_attempt.
               Never cancel, never move a delivery back to `pending`, never touch one that is
               already delivered or cancelled (a dispatcher can).
  dispatcher / admin
               any status to any other (they can reinstate a cancelled delivery on purpose).
  customer     cancel only while the delivery hasn't left yet (pending / picked_up).

STALE WRITERS (a change based on an older `version` than the server now has)
  Additionally may NOT move a delivery out of a FINAL status (delivered / cancelled). A device that was
  offline while a dispatcher cancelled the delivery must never bring it back to life — no matter how
  recent its timestamp looks. A stale change that is still valid against the CURRENT state (e.g. the
  agent marks it out for delivery after the dispatcher only edited a note) is "reconciled".

Timestamps are never consulted here: they describe when things happened, versions decide what wins.
"""

from dataclasses import dataclass

from app.models.delivery import DeliveryStatus

FINAL_STATUSES = {DeliveryStatus.delivered, DeliveryStatus.cancelled}
AGENT_TARGET_STATUSES = {
    DeliveryStatus.picked_up,
    DeliveryStatus.out_for_delivery,
    DeliveryStatus.delivered,
    DeliveryStatus.failed_attempt,
}
CUSTOMER_CANCELLABLE_STATUSES = {DeliveryStatus.pending, DeliveryStatus.picked_up}

# Codes returned to clients / written to the audit log.
STALE_VERSION = "stale_version"
TRANSITION_NOT_ALLOWED = "transition_not_allowed"


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    code: str | None = None
    message: str | None = None


ALLOWED = Verdict(True)


def _label(status) -> str:
    return getattr(status, "value", status)


def check_transition(actor_kind: str, old_status, new_status) -> Verdict:
    """actor_kind: "agent" | "dispatcher" | "admin" | "customer"."""
    if old_status == new_status:
        return ALLOWED  # not a status change (only notes/location/proof changed)

    if actor_kind in ("dispatcher", "admin"):
        return ALLOWED

    if actor_kind == "agent":
        if old_status in FINAL_STATUSES:
            return Verdict(False, TRANSITION_NOT_ALLOWED,
                           f"A delivery that is '{_label(old_status)}' can only be changed by a dispatcher.")
        if new_status not in AGENT_TARGET_STATUSES:
            return Verdict(False, TRANSITION_NOT_ALLOWED,
                           f"Agents can't set a delivery to '{_label(new_status)}'.")
        return ALLOWED

    if actor_kind == "customer":
        if new_status != DeliveryStatus.cancelled or old_status not in CUSTOMER_CANCELLABLE_STATUSES:
            return Verdict(False, TRANSITION_NOT_ALLOWED,
                           "This order can no longer be cancelled — it's already out for delivery or further along.")
        return ALLOWED

    return Verdict(False, TRANSITION_NOT_ALLOWED, "Unknown actor.")


def check_stale_change(actor_kind: str, current_status, incoming_status) -> Verdict:
    """For a change whose base_version is behind the server's: may it still be applied to the CURRENT state?"""
    if incoming_status == current_status:
        return ALLOWED
    if current_status in FINAL_STATUSES:
        what = "cancelled" if current_status == DeliveryStatus.cancelled else "completed"
        return Verdict(
            False, STALE_VERSION,
            f"This delivery was {what} after you last synced, so your change to "
            f"'{_label(incoming_status)}' was not applied.",
        )
    return check_transition(actor_kind, current_status, incoming_status)
