"""
Base Agent definition for the Inter-Module Communication Mesh.
Every module (Arena AI, Copilot, Claude, GPT, Custom) inherits from BaseAgent.

User-centered: bounded memory, better error handling, status tracking.

Provenance is part of the contract: every message carries ``metadata`` saying
which provider produced it and whether the text is simulated. A provider that
fails is never turned into a normal-looking answer - either the simulator
answers *and says so*, or the error is raised for the caller to handle.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..protocol.bus import MessageBus
from ..protocol.message import Message, MessageType, clamp_content
from ..run_control import check_cancelled
from .providers import FALLBACK_NOTICE_TEMPLATE, BaseLLMProvider, MockLLMProvider, ProviderError

logger = logging.getLogger("BaseAgent")

#: Hex colours only - the value ends up in a ``style`` attribute in the browser.
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
#: Emoji-ish avatars: a few characters, no markup, no quotes.
MAX_AVATAR_CHARS = 8

#: How much of a peer's last answer a single agent may be handed as context.
#: Topologies compose their prompts out of previous messages, so with a real
#: provider those grow far past what the simulator ever produced; the budget has
#: to be enforced by *shrinking the input*, never by refusing the turn.
MAX_INJECTED_PROMPT = 10_000

#: Marker kept at both ends of a clamped context so the transcript says what
#: happened instead of quietly reading like a complete message.
_CONTEXT_CUT = "\n… [… trimmed to fit the context budget] …\n"


def fit_context(prompt: str, limit: int = MAX_INJECTED_PROMPT) -> Tuple[str, int]:
    """
    Fit a composed prompt into ``limit`` characters, keeping the head and the tail.

    The first lines carry the instruction and the last lines carry the thing the
    peer was actually asked to react to, so a middle-out cut preserves far more
    of the meaning than a plain tail truncation.

    Returns ``(text, dropped_characters)``.
    """
    if len(prompt) <= limit:
        return prompt, 0
    budget = limit - len(_CONTEXT_CUT)
    head = budget - (budget // 3)
    tail = budget // 3
    return prompt[:head].rstrip() + _CONTEXT_CUT + prompt[-tail:].lstrip(), len(prompt) - limit



def safe_color(value: Any, default: str = "#6366f1") -> str:
    """
    Return ``value`` when it is a plain hex colour, else ``default``.

    Colours reach the browser through inline styles, and saved sessions are
    user-editable JSON files, so the value is validated here as well as in the
    API models - defence in depth against markup sneaking into an attribute.
    """
    if isinstance(value, str):
        candidate = value.strip()
        if COLOR_PATTERN.match(candidate):
            return candidate
    return default


#: Characters that have no business in an avatar: markup, quoting, or anything
#: that could break out of an attribute when the value is rendered.
_AVATAR_FORBIDDEN = set('<>"\'&;=()[]{}%`\\/|')


def safe_avatar(value: Any, default: str = "🤖") -> str:
    """
    Keep avatars to a short run of plain printable characters (emoji, letters).

    Anything containing markup-ish characters is discarded outright rather than
    filtered - a mangled emoji is cosmetic, a silently accepted payload is not.
    """
    if not isinstance(value, str):
        return default
    cleaned = value.strip()[:MAX_AVATAR_CHARS]
    if not cleaned:
        return default
    if any(ch in _AVATAR_FORBIDDEN or ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        return default
    return cleaned


class BaseAgent:
    MAX_MEMORY = 100  # Prevent unbounded growth

    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        color: str = "#6366f1",
        avatar: str = "🤖",
        provider: Optional[BaseLLMProvider] = None,
        bus: Optional[MessageBus] = None,
    ):
        if not agent_id or not agent_id.strip():
            raise ValueError("Agent ID cannot be empty")
        if not name or not name.strip():
            raise ValueError("Agent name cannot be empty")
        if not role or not role.strip():
            raise ValueError("Agent role cannot be empty")

        self.agent_id = agent_id.strip()
        self.name = name.strip()
        self.role = role.strip()
        self.system_prompt = system_prompt
        self.color = safe_color(color, "#6366f1")
        self.avatar = safe_avatar(avatar)
        self.provider = provider or MockLLMProvider()
        self.bus = bus
        self.memory: List[Message] = []
        self.status: str = "idle"  # idle, thinking, degraded, error

        if self.bus:
            self.attach_bus(self.bus)

    def attach_bus(self, bus: MessageBus) -> None:
        """Connect agent to message bus."""
        self.bus = bus
        self.bus.register_agent(self.agent_id, self.receive)

    def detach_bus(self) -> None:
        """Disconnect from message bus."""
        if self.bus:
            self.bus.unregister_agent(self.agent_id)
            self.bus = None

    async def receive(self, message: Message) -> None:
        """Handle incoming message delivered by the bus with bounded memory."""
        self.memory.append(message)
        # Prune old memory to prevent unbounded growth
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        logger.debug(f"Agent {self.name} received message from {message.sender_name}")

    async def generate_response(
        self,
        prompt: Optional[str] = None,
        recipient_id: str = "*",
        recipient_name: Optional[str] = None,
        message_type: MessageType = MessageType.PROPOSAL,
        topic: str = "collaboration",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """
        Synthesize response using the LLM provider and broadcast/send it over the bus.

        Provenance and failure policy:
        - the produced message records which provider answered and whether the
          text is simulated (``metadata["simulated"]``),
        - a :class:`ProviderError` is never turned into a plausible-looking
          answer. Either the simulator answers *and says which provider failed*,
          or the error propagates so the run can be reported as failed.
        """
        check_cancelled()
        self.status = "thinking"

        # The incoming context is a *budget*, not a precondition. A real provider
        # answer is routinely longer than the 10k this code used to reject, and the
        # run is nobody's better for dying on turn 3 of a four-turn dialogue.
        context_dropped = 0
        if prompt is not None:
            prompt, context_dropped = fit_context(prompt)

        # Build prompt context from memory - limit for performance
        recent_messages = []
        for m in self.memory[-6:]:
            role = "assistant" if m.sender_id == self.agent_id else "user"
            # Truncate very long messages for context window
            content = m.content[:2000] + "..." if len(m.content) > 2000 else m.content
            recent_messages.append({"role": role, "content": f"[{m.sender_name}]: {content}"})

        if prompt:
            recent_messages.append({"role": "user", "content": prompt})

        provider_label = self.provider.__class__.__name__
        simulated = bool(getattr(self.provider, "is_simulated", False))
        metadata: Dict[str, Any] = {"provider": provider_label, "simulated": simulated}
        if context_dropped:
            # Visible in the transcript and in the export, not just in a log line.
            metadata["context_truncated"] = context_dropped

        try:
            content = await self.provider.generate(
                system_prompt=self.system_prompt,
                messages=recent_messages,
                agent_role=self.role,
                agent_name=self.name,
                task_context=prompt,
            )
            if not content or not content.strip():
                content = f"[{self.name} processed the request but generated empty response - using fallback]"
                metadata["empty_response"] = True
        except ProviderError as e:
            # A real provider was configured and failed. Falling back is allowed
            # only when it is visible: the message carries the reason, the agent
            # goes to "degraded", and the text says the simulator is talking.
            logger.error(f"{self.name}: provider {e.provider} failed: {e.reason} {e.detail}".strip())
            if not getattr(self.provider, "fallback_to_mock", True):
                self.status = "error"
                raise
            content = FALLBACK_NOTICE_TEMPLATE.format(provider=e.provider, reason=e.reason) + (
                await MockLLMProvider().generate(
                    system_prompt=self.system_prompt,
                    messages=recent_messages,
                    agent_role=self.role,
                    agent_name=self.name,
                    task_context=prompt,
                )
            )
            metadata.update(
                {
                    "provider": f"{e.provider} -> simulator",
                    "simulated": True,
                    "provider_error": e.reason,
                    "provider_status_code": e.status_code,
                    # Honest about how hard the provider was tried before giving up.
                    "provider_attempts": e.attempts,
                }
            )
            self.status = "degraded"
        except Exception as e:  # unexpected bug inside a provider, not its own error
            logger.error(f"Error in {self.name} generation: {e}", exc_info=True)
            if not getattr(self.provider, "fallback_to_mock", True):
                self.status = "error"
                raise
            content = (
                f"> ⚠️ **{provider_label} failed** ({e.__class__.__name__}). The reply below is "
                f"the built-in simulator talking, **not** an answer from {provider_label}.\n\n"
                f"### [{self.name} - Error Recovery]\n\n"
                f"Task received: {prompt[:200] if prompt else 'No prompt'}...\n\n"
                f"_Continuing with a simulated response so the dialogue does not stall._"
            )
            metadata.update(
                {
                    "provider": f"{provider_label} -> simulator",
                    "simulated": True,
                    "provider_error": f"{e.__class__.__name__}: {e}"[:300],
                }
            )
            self.status = "degraded"
            # Brief pause before returning to idle
            await asyncio.sleep(0.1)

        # A provider is free to answer with more text than the protocol can carry.
        # Clamping here (instead of letting Message reject it) is what keeps a long
        # code answer from turning into a failed run and a lost transcript.
        content, content_dropped = clamp_content(content)
        if content_dropped:
            metadata["content_truncated"] = content_dropped

        msg = Message(
            sender_id=self.agent_id,
            sender_name=self.name,
            recipient_id=recipient_id,
            recipient_name=recipient_name,
            topic=topic,
            message_type=message_type,
            content=content,
            artifacts=artifacts or {},
            metadata=metadata,
        )

        self.memory.append(msg)
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        # "degraded" survives the turn so the UI can show that the last answer
        # came from the simulator after a provider failure, not from the model.
        self.status = "degraded" if metadata.get("provider_error") else "idle"

        if self.bus:
            try:
                await self.bus.dispatch(msg)
            except Exception as e:
                logger.error(f"Failed to dispatch message from {self.name}: {e}")

        return msg

    async def send_to(
        self,
        recipient: "BaseAgent",
        content: str,
        message_type: MessageType = MessageType.PROPOSAL,
        topic: str = "direct",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Direct message to another agent with validation."""
        if not content or not content.strip():
            raise ValueError("Message content cannot be empty")
        if len(content) > 50000:
            raise ValueError("Message content too long (max 50000 chars)")

        msg = Message(
            sender_id=self.agent_id,
            sender_name=self.name,
            recipient_id=recipient.agent_id,
            recipient_name=recipient.name,
            topic=topic,
            message_type=message_type,
            content=content,
            artifacts=artifacts or {},
        )
        self.memory.append(msg)
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        if self.bus:
            await self.bus.dispatch(msg)
        return msg

    async def broadcast(
        self,
        content: str,
        message_type: MessageType = MessageType.PROPOSAL,
        topic: str = "general",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Broadcast message to all connected agents with validation."""
        if not content or not content.strip():
            raise ValueError("Broadcast content cannot be empty")
        if len(content) > 50000:
            raise ValueError("Broadcast content too long (max 50000 chars)")

        msg = Message(
            sender_id=self.agent_id,
            sender_name=self.name,
            recipient_id="*",
            topic=topic,
            message_type=message_type,
            content=content,
            artifacts=artifacts or {},
        )
        self.memory.append(msg)
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        if self.bus:
            await self.bus.dispatch(msg)
        return msg

    def clear_memory(self) -> None:
        self.memory.clear()
        self.status = "idle"

    def to_dict(self) -> Dict[str, Any]:
        """Public agent description.

        ``provider_kind`` is what the dashboard shows: ``simulated`` answers
        must never look like ``live`` ones.
        """
        simulated = bool(getattr(self.provider, "is_simulated", False))
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "system_prompt": self.system_prompt,
            "color": self.color,
            "avatar": self.avatar,
            "status": self.status,
            "provider": self.provider.__class__.__name__,
            "provider_kind": "simulated" if simulated else "live",
            "model": getattr(self.provider, "model", None),
            "memory_count": len(self.memory),
        }
