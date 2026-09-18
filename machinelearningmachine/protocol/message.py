"""
Standardized Inter-Module Message Protocol for MachineLearningMachine.
Defines structured message types, payloads, and serialization.
"""

import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

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

class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
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
