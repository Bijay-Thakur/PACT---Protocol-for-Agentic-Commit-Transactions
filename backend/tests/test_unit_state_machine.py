import pytest

from app.core.state_machine import (
    EFFECT_TRANSITIONS,
    TERMINAL_TX_STATES,
    TX_TRANSITIONS,
    assert_effect_transition,
    assert_tx_transition,
)
from app.domain.enums import EffectState as E
from app.domain.enums import TransactionState as T
from app.domain.errors import InvalidStateTransition


def test_every_state_has_a_policy_entry():
    assert set(TX_TRANSITIONS) == set(T)
    assert set(EFFECT_TRANSITIONS) == set(E)


@pytest.mark.parametrize("path", [
    [T.CREATED, T.SPECIFYING, T.PREPARING, T.PREPARED, T.COMMITTING, T.VERIFYING, T.COMMITTED_VERIFIED],
    [T.SPECIFYING, T.ABORTING, T.ABORTED],
    [T.COMMITTING, T.UNKNOWN, T.RECONCILING, T.COMMITTING],
    [T.VERIFYING, T.UNKNOWN, T.RECONCILING, T.VERIFYING],
    [T.COMMITTING, T.COMPENSATING, T.COMPENSATED],
    [T.COMPENSATING, T.HUMAN_REQUIRED, T.FAILED_TERMINAL],
])
def test_legal_transaction_paths(path):
    for a, b in zip(path, path[1:]):
        assert_tx_transition(a, b)


@pytest.mark.parametrize("a,b", [
    (T.CREATED, T.COMMITTING),          # cannot skip prepare + barrier
    (T.PREPARING, T.COMMITTING),
    (T.UNKNOWN, T.COMMITTED_VERIFIED),  # UNKNOWN cannot be collapsed into success
    (T.UNKNOWN, T.ABORTED),             # ...or into failure
    (T.COMPENSATING, T.ABORTED),        # compensation is not "rolled back"
    (T.HUMAN_REQUIRED, T.COMMITTING),
])
def test_illegal_transaction_transitions_fail_loudly(a, b):
    with pytest.raises(InvalidStateTransition) as exc:
        assert_tx_transition(a, b)
    assert exc.value.code == "INVALID_STATE_TRANSITION"


def test_terminal_states_have_no_exits_and_unknown_is_not_terminal():
    for s in TERMINAL_TX_STATES:
        assert TX_TRANSITIONS[s] == frozenset()
    assert T.UNKNOWN not in TERMINAL_TX_STATES
    assert T.COMMITTED_VERIFIED in TERMINAL_TX_STATES


@pytest.mark.parametrize("path", [
    [E.PROPOSED, E.VALIDATED, E.PREPARED, E.DISPATCHING, E.DISPATCHED, E.VERIFYING, E.VERIFIED],
    [E.DISPATCHING, E.UNKNOWN, E.RECONCILING, E.VERIFIED],
    [E.DISPATCHING, E.UNKNOWN, E.RECONCILING, E.RETRYABLE, E.DISPATCHING],
    [E.VERIFIED, E.COMPENSATING, E.COMPENSATED],
    [E.COMPENSATING, E.HUMAN_REQUIRED, E.COMPENSATING],
])
def test_legal_effect_paths(path):
    for a, b in zip(path, path[1:]):
        assert_effect_transition(a, b)


@pytest.mark.parametrize("a,b", [
    (E.UNKNOWN, E.DISPATCHING),   # never blind-retry an UNKNOWN effect
    (E.UNKNOWN, E.FAILED),        # never convert UNKNOWN to FAILED for convenience
    (E.PROPOSED, E.DISPATCHING),  # cannot dispatch without validation + prepare
    (E.DISPATCHED, E.VERIFIED),   # cannot be VERIFIED without a verifier pass
    (E.FAILED, E.COMPENSATING),
])
def test_illegal_effect_transitions(a, b):
    with pytest.raises(InvalidStateTransition):
        assert_effect_transition(a, b)
