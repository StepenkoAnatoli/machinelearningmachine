"""
Standardized Inter-Module Message Protocol for MachineLearningMachine.
Defines structured message types, payloads, and serialization.
"""

import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    TASK_SPEC = "task_spec"     # Initial specification / prompt
    PROPOSAL = "proposal"       # Draft solution / code implementation
    CRITIQUE = "critique"       # Architectural or code review / feedback
    REVISION = "revision"       # Improved solution addressing critique
    QUERY = "query"             # Direct question from one agent to another
    ANSWER = "answer"           # Reply to a query
    HANDOFF = "handoff"         # Delegation / passing state to next module
    CONSENSUS = "consensus"     # Final agreement or synthesis
    SYSTEM = "system"           # Orchestrator / environment notification


MAX_CONTENT_LENGTH = 50_000
MAX_TOPIC_LENGTH = 100

def clamp_content(content: str, limit: int = MAX_CONTENT_LENGTH) -> Tuple[str, int]:
    """
    Fit ``content`` into ``limit`` characters, saying so when it did not fit.

    Producers must never build a :class:`Message` whose content exceeds
    :data:`MAX_CONTENT_LENGTH`: the model would reject it and the whole run would
    die on an internal bound. A real provider answer routinely runs past that size
    (a few hundred lines of code is enough), so clamping is the producer's job and
    it must be visible: the returned text carries the notice and the second return
    value is the number of characters that were dropped (0 when nothing was cut).

    Returns ``(text, dropped_characters)``.
    """
    if len(content) <= limit:
        return content, 0
    notice = (
        f"\n\n> ✂️ **Truncated:** the full text is {len(content)} characters, which exceeds "
        f"this mesh's {limit}-character message limit. The beginning is shown. Nothing was "
        "saved elsewhere - re-run with a shorter request if you need the rest."
    )
    keep = limit - len(notice)
    return content[:keep].rstrip() + notice, len(content) - keep


class Message(BaseModel):
    # 12 hex characters (~10^14). The browser keys messages by this id, so a
    # collision inside one transcript would silently drop a message; 8 characters
    # was already within birthday-paradox reach of a full 1000-message history.
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    sender_id: str = Field(..., min_length=1, max_length=100)
    sender_name: str = Field(..., min_length=1, max_length=200)
    recipient_id: str = Field(default="*", max_length=100)  # Specific agent ID or '*' for broadcast
    recipient_name: Optional[str] = Field(default=None, max_length=200)
    topic: str = Field(default="general", max_length=MAX_TOPIC_LENGTH)
    message_type: MessageType = MessageType.PROPOSAL
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "recipient_id": self.recipient_id,
            "recipient_name": self.recipient_name,
            "topic": self.topic,
            "message_type": self.message_type.value,
            "content": self.content,
            "artifacts": self.artifacts,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "formatted_time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
        }

    def to_formatted_log(self) -> str:
        t_str = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        target = f" -> @{self.recipient_name or self.recipient_id}" if self.recipient_id != "*" else " (broadcast)"
        return f"[{t_str}] [{self.message_type.value.upper()}] {self.sender_name}{target}:\n{self.content}\n"
