"""
Standardized Inter-Module Message Protocol for MachineLearningMachine.
Defines structured message types, payloads, and serialization.
"""

from enum import Enum
import time
import uuid
from typing import Dict, Any, Optional, List
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


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    sender_id: str
    sender_name: str
    recipient_id: str = "*"     # Specific agent ID or '*' for broadcast
    recipient_name: Optional[str] = None
    topic: str = "general"
    message_type: MessageType = MessageType.PROPOSAL
    content: str
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
