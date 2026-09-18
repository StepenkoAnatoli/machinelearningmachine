"""
Message Bus and Dispatcher for inter-agent communication.
Supports point-to-point routing, topic-based pub/sub, event hooks, and transcript logging.
"""

import asyncio
import json
import logging
from typing import Dict, List, Callable, Optional, Awaitable, Set
from .message import Message, MessageType

logger = logging.getLogger("MessageBus")


class MessageBus:
    # Prevent unbounded memory growth - keep last N messages
    MAX_HISTORY = 1000

    def __init__(self, max_history: int = MAX_HISTORY):
        # agent_id -> message handler callback: async def (message: Message) -> None
        self._agents: Dict[str, Callable[[Message], Awaitable[None]]] = {}
        # topic -> set of agent_ids
        self._topics: Dict[str, Set[str]] = {}
        # Global listeners (e.g. WebSockets, logging, metrics)
        self._global_listeners: List[Callable[[Message], Awaitable[None]]] = []
        # Message history log (bounded)
        self._history: List[Message] = []
        self._max_history = max_history
        self._lock = asyncio.Lock()

    def register_agent(self, agent_id: str, handler: Callable[[Message], Awaitable[None]]) -> None:
        """Register an agent's inbox handler."""
        self._agents[agent_id] = handler

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent."""
        self._agents.pop(agent_id, None)
        for subscribers in self._topics.values():
            subscribers.discard(agent_id)

    def subscribe_topic(self, topic: str, agent_id: str) -> None:
        """Subscribe an agent to a topic channel."""
        if topic not in self._topics:
            self._topics[topic] = set()
        self._topics[topic].add(agent_id)

    def unsubscribe_topic(self, topic: str, agent_id: str) -> None:
        """Unsubscribe an agent from a topic channel."""
        if topic in self._topics:
            self._topics[topic].discard(agent_id)

    def add_global_listener(self, listener: Callable[[Message], Awaitable[None]]) -> None:
        """Attach a passive listener (e.g. UI WebSocket broadcaster)."""
        self._global_listeners.append(listener)

    def remove_global_listener(self, listener: Callable[[Message], Awaitable[None]]) -> None:
        """Remove a passive listener."""
        if listener in self._global_listeners:
            self._global_listeners.remove(listener)

    async def dispatch(self, message: Message) -> None:
        """
        Deliver message according to its recipient_id or topic.
        Also records in history and alerts all global listeners.
        History is bounded to prevent memory exhaustion.
        """
        async with self._lock:
            self._history.append(message)
            # Prune oldest messages if we exceed max history
            if len(self._history) > self._max_history:
                # Keep most recent messages
                self._history = self._history[-self._max_history:]

        # Notify global listeners (UI, logger, etc.)
        for listener in self._global_listeners:
            try:
                res = listener(message)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error in global listener: {e}")

        # If recipient is specific
        if message.recipient_id and message.recipient_id not in ("*", "broadcast", "all"):
            handler = self._agents.get(message.recipient_id)
            if handler:
                try:
                    await handler(message)
                except Exception as e:
                    logger.error(f"Error dispatching to {message.recipient_id}: {e}")
            else:
                logger.warning(f"Target agent '{message.recipient_id}' not found in registry")
            return

        # Broadcast or Topic publish
        targets = set()
        if message.topic and message.topic in self._topics:
            targets.update(self._topics[message.topic])
        elif message.recipient_id in ("*", "broadcast", "all"):
            targets.update(self._agents.keys())

        # Never deliver back to sender unless explicitly configured
        targets.discard(message.sender_id)

        for agent_id in targets:
            handler = self._agents.get(agent_id)
            if handler:
                try:
                    await handler(message)
                except Exception as e:
                    logger.error(f"Error dispatching to subscriber {agent_id}: {e}")

    def get_history(self) -> List[Message]:
        """Return a copy of the message history."""
        return list(self._history)

    def clear_history(self) -> None:
        """Clear message history."""
        self._history.clear()

    def export_json(self) -> str:
        """Export history to JSON string."""
        return json.dumps([m.to_dict() for m in self._history], indent=2)

    def export_markdown(self) -> str:
        """Export history to human-readable Markdown transcript."""
        lines = ["# Multi-Agent Dialogue Transcript\n"]
        for m in self._history:
            badge = m.message_type.value.upper()
            target_str = f" to **{m.recipient_name or m.recipient_id}**" if m.recipient_id != "*" else " (broadcast)"
            lines.append(f"### [{badge}] {m.sender_name}{target_str}")
            lines.append(f"*Time: {m.to_dict()['formatted_time']} | Topic: `{m.topic}`*\n")
            lines.append(m.content)
            lines.append("\n---\n")
        return "\n".join(lines)
