"""SubscriptionManager tests — primarily leak detection on unsubscription."""

from __future__ import annotations

from agentos.gateway.websocket import SubscriptionManager


def test_subscribe_messages_populates_dict() -> None:
    mgr = SubscriptionManager()
    mgr.subscribe_messages("conn-1", "session-A")
    mgr.subscribe_messages("conn-2", "session-A")
    mgr.subscribe_messages("conn-1", "session-B")

    assert mgr.get_message_subscribers("session-A") == {"conn-1", "conn-2"}
    assert mgr.get_message_subscribers("session-B") == {"conn-1"}
    # Keys exist in internal dict
    assert "session-A" in mgr._message_subs
    assert "session-B" in mgr._message_subs


def test_unsubscribe_messages_removes_conn_and_empties_key() -> None:
    """Issue #609: after discarding the last subscriber, the session key
    must be deleted from _message_subs to prevent leaking empty sets."""
    mgr = SubscriptionManager()
    mgr.subscribe_messages("conn-1", "session-A")
    mgr.subscribe_messages("conn-2", "session-A")

    mgr.unsubscribe_messages("conn-1", "session-A")
    assert mgr.get_message_subscribers("session-A") == {"conn-2"}
    assert "session-A" in mgr._message_subs  # still has conn-2

    mgr.unsubscribe_messages("conn-2", "session-A")
    assert mgr.get_message_subscribers("session-A") == set()
    # Session key must be cleaned up (issue #609)
    assert "session-A" not in mgr._message_subs


def test_unsubscribe_messages_on_single_subscriber_removes_key() -> None:
    """Single subscriber unsubscribing must clean up the empty set."""
    mgr = SubscriptionManager()
    mgr.subscribe_messages("conn-1", "session-A")
    mgr.unsubscribe_messages("conn-1", "session-A")
    assert "session-A" not in mgr._message_subs


def test_remove_connection_cleans_up_empty_keys() -> None:
    """Issue #609: after remove_connection discards the last subscriber for
    a session, the empty set must be deleted from _message_subs."""
    mgr = SubscriptionManager()
    mgr.subscribe_messages("conn-1", "session-A")
    mgr.subscribe_messages("conn-1", "session-B")
    mgr.subscribe_messages("conn-2", "session-A")

    mgr.remove_connection("conn-2")
    assert "session-A" in mgr._message_subs  # conn-1 still subbed

    mgr.remove_connection("conn-1")
    assert "session-A" not in mgr._message_subs
    assert "session-B" not in mgr._message_subs


def test_remove_connection_does_not_affect_other_connections() -> None:
    mgr = SubscriptionManager()
    mgr.subscribe_messages("conn-1", "session-A")
    mgr.subscribe_messages("conn-2", "session-A")

    mgr.remove_connection("conn-1")
    assert "session-A" in mgr._message_subs
    assert mgr.get_message_subscribers("session-A") == {"conn-2"}


def test_topic_subscription_cleanup_existing() -> None:
    """topic subs already had the correct cleanup pattern — verify it works."""
    mgr = SubscriptionManager()
    mgr.subscribe_topic("conn-1", "topic-A")
    mgr.subscribe_topic("conn-2", "topic-A")

    mgr.unsubscribe_topic("conn-2", "topic-A")
    assert "topic-A" in mgr._topic_subs

    mgr.unsubscribe_topic("conn-1", "topic-A")
    assert "topic-A" not in mgr._topic_subs
