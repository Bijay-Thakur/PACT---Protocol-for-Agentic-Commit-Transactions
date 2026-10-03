"""Central transition policy for transactions and effects.

This is the *only* place that defines which state changes are legal. Services
call :func:`assert_tx_transition` / :func:`assert_effect_transition` (through the
TransactionManager) and never assign states directly.
"""

from __future__ import annotations

from app.domain.enums import EffectState as E
from app.domain.enums import TransactionState as T
from app.domain.errors import InvalidStateTransition

TX_TRANSITIONS: dict[T, frozenset[T]] = {
    T.CREATED: frozenset({T.SPECIFYING, T.ABORTING}),
    T.SPECIFYING: frozenset({T.PREPARING, T.ABORTING}),
    T.PREPARING: frozenset({T.PREPARED, T.ABORTING}),
    T.PREPARED: frozenset({T.COMMITTING, T.ABORTING}),
    T.COMMITTING: frozenset(
        {T.VERIFYING, T.UNKNOWN, T.COMPENSATING, T.HUMAN_REQUIRED, T.FAILED_TERMINAL}
    ),
    T.VERIFYING: frozenset(
        {T.COMMITTED_VERIFIED, T.UNKNOWN, T.COMPENSATING, T.HUMAN_REQUIRED, T.FAILED_TERMINAL}
    ),
    T.UNKNOWN: frozenset({T.RECONCILING, T.HUMAN_REQUIRED}),
    # RECONCILING -> COMMITTING lets execution continue with the remaining DAG
    # once ambiguity is resolved (ADR-0004).
    T.RECONCILING: frozenset(
        {T.VERIFYING, T.COMMITTING, T.UNKNOWN, T.COMPENSATING, T.HUMAN_REQUIRED}
    ),
    T.COMPENSATING: frozenset({T.COMPENSATED, T.HUMAN_REQUIRED, T.FAILED_TERMINAL}),
    T.HUMAN_REQUIRED: frozenset({T.RECONCILING, T.COMPENSATING, T.FAILED_TERMINAL}),
    T.ABORTING: frozenset({T.ABORTED, T.FAILED_TERMINAL}),
    T.COMMITTED_VERIFIED: frozenset(),
    T.ABORTED: frozenset(),
    T.COMPENSATED: frozenset(),
    T.FAILED_TERMINAL: frozenset(),
}

TERMINAL_TX_STATES: frozenset[T] = frozenset(
    {T.COMMITTED_VERIFIED, T.ABORTED, T.COMPENSATED, T.FAILED_TERMINAL}
)

# States in which a transaction has crossed (or is crossing) the commit barrier
# and may hold real-world effects.
POST_BARRIER_TX_STATES: frozenset[T] = frozenset(
    {T.COMMITTING, T.VERIFYING, T.UNKNOWN, T.RECONCILING, T.COMPENSATING, T.HUMAN_REQUIRED}
)

EFFECT_TRANSITIONS: dict[E, frozenset[E]] = {
    E.PROPOSED: frozenset({E.VALIDATED, E.FAILED, E.ABORTED}),
    E.VALIDATED: frozenset({E.PREPARED, E.FAILED, E.ABORTED}),
    E.PREPARED: frozenset({E.DISPATCHING, E.ABORTED}),
    E.DISPATCHING: frozenset({E.DISPATCHED, E.UNKNOWN, E.FAILED, E.RETRYABLE}),
    E.DISPATCHED: frozenset({E.VERIFYING, E.UNKNOWN}),
    E.VERIFYING: frozenset({E.VERIFIED, E.FAILED, E.UNKNOWN}),
    E.VERIFIED: frozenset({E.COMPENSATING}),
    E.UNKNOWN: frozenset({E.RECONCILING}),
    E.RECONCILING: frozenset({E.VERIFIED, E.RETRYABLE, E.UNKNOWN, E.FAILED, E.HUMAN_REQUIRED}),
    E.RETRYABLE: frozenset({E.DISPATCHING, E.FAILED, E.ABORTED}),
    E.COMPENSATING: frozenset({E.COMPENSATED, E.HUMAN_REQUIRED}),
    E.HUMAN_REQUIRED: frozenset({E.RECONCILING, E.COMPENSATING, E.COMPENSATED, E.FAILED}),
    E.FAILED: frozenset(),
    E.COMPENSATED: frozenset(),
    E.ABORTED: frozenset(),
}

TERMINAL_EFFECT_STATES: frozenset[E] = frozenset({E.FAILED, E.COMPENSATED, E.ABORTED})

# Effects whose outcome is not yet known; any of these blocks commit progress.
UNRESOLVED_EFFECT_STATES: frozenset[E] = frozenset(
    {E.DISPATCHING, E.DISPATCHED, E.VERIFYING, E.UNKNOWN, E.RECONCILING, E.HUMAN_REQUIRED}
)

# Effects that still count against authority/budget exposure.
EXPOSURE_EFFECT_STATES: frozenset[E] = frozenset(set(E) - {E.ABORTED, E.FAILED, E.COMPENSATED})


def assert_tx_transition(current: T | str, target: T | str) -> None:
    cur, tgt = T(current), T(target)
    if tgt not in TX_TRANSITIONS[cur]:
        raise InvalidStateTransition(
            f"Transaction transition {cur} -> {tgt} is not permitted",
            details={"from": cur, "to": tgt, "allowed": sorted(TX_TRANSITIONS[cur])},
        )


def assert_effect_transition(current: E | str, target: E | str) -> None:
    cur, tgt = E(current), E(target)
    if tgt not in EFFECT_TRANSITIONS[cur]:
        raise InvalidStateTransition(
            f"Effect transition {cur} -> {tgt} is not permitted",
            details={"from": cur, "to": tgt, "allowed": sorted(EFFECT_TRANSITIONS[cur])},
        )


def is_terminal_tx(state: T | str) -> bool:
    return T(state) in TERMINAL_TX_STATES
