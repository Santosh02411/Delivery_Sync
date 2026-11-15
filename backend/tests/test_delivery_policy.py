"""The central status-transition policy (services/delivery_policy.py), as plain functions."""

import pytest

from app.models.delivery import DeliveryStatus as S
from app.services.delivery_policy import check_transition, check_stale_change, STALE_VERSION, TRANSITION_NOT_ALLOWED


@pytest.mark.parametrize("old,new", [
    (S.pending, S.picked_up), (S.pending, S.out_for_delivery), (S.picked_up, S.delivered),
    (S.out_for_delivery, S.failed_attempt), (S.out_for_delivery, S.picked_up), (S.failed_attempt, S.out_for_delivery),
])
def test_agent_allowed_moves(old, new):
    assert check_transition("agent", old, new).allowed


@pytest.mark.parametrize("old,new", [
    (S.picked_up, S.cancelled), (S.picked_up, S.pending), (S.delivered, S.out_for_delivery),
    (S.delivered, S.failed_attempt), (S.cancelled, S.picked_up), (S.cancelled, S.delivered),
])
def test_agent_refused_moves(old, new):
    verdict = check_transition("agent", old, new)
    assert not verdict.allowed and verdict.code == TRANSITION_NOT_ALLOWED and verdict.message


@pytest.mark.parametrize("role", ["dispatcher", "admin"])
def test_dispatchers_and_admins_can_make_any_deliberate_change(role):
    assert check_transition(role, S.cancelled, S.out_for_delivery).allowed
    assert check_transition(role, S.delivered, S.failed_attempt).allowed


def test_a_non_change_is_always_fine():
    assert check_transition("agent", S.delivered, S.delivered).allowed


def test_customer_can_cancel_only_before_it_leaves():
    assert check_transition("customer", S.pending, S.cancelled).allowed
    assert check_transition("customer", S.picked_up, S.cancelled).allowed
    assert not check_transition("customer", S.out_for_delivery, S.cancelled).allowed
    assert not check_transition("customer", S.pending, S.delivered).allowed


@pytest.mark.parametrize("role", ["agent", "dispatcher", "admin"])
@pytest.mark.parametrize("final", [S.cancelled, S.delivered])
def test_a_stale_change_can_never_move_a_final_delivery(role, final):
    verdict = check_stale_change(role, final, S.out_for_delivery)
    assert not verdict.allowed and verdict.code == STALE_VERSION


def test_a_stale_change_is_fine_if_it_is_still_valid_or_a_repeat():
    assert check_stale_change("agent", S.picked_up, S.out_for_delivery).allowed
    assert check_stale_change("agent", S.delivered, S.delivered).allowed   # re-send of something already applied


def test_a_stale_change_still_obeys_the_role_rules():
    assert not check_stale_change("agent", S.picked_up, S.cancelled).allowed
