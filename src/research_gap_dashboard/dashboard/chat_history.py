"""
The shared turn model for Conversational Analytics threads.

One exchange in a grounded conversation: a question the researcher asked or a
grounded answer the model gave. Answers keep the citation keys behind them so a
reloaded conversation still shows what every claim was grounded in
(CODING_STANDARDS.md > Research integrity).

Persistence of whole threads lives in `conversations` (issue #50); this module is
only the value model both the store and the layout layer share, so neither
imports a pipeline stage (ADR 0002).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["user", "assistant"]


class ChatTurn(BaseModel):
  """One turn of the conversation: a question asked or a grounded answer given."""

  role: Role
  content: str
  citations: list[str] = []
  at: datetime
