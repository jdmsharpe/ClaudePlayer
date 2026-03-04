import logging
from typing import List, Dict, Any
from claude_player.config.config_class import ConfigClass
from claude_player.interface.claude_interface import ClaudeInterface
from claude_player.state.game_state import GameState
from claude_player.utils.message_utils import MessageUtils

class SummaryGenerator:
    """Generates game summaries to maintain context over long sessions."""

    def __init__(self, client: ClaudeInterface, game_state: GameState, config: ConfigClass):
        """Initialize the summary generator."""
        self.client = client
        self.game_state = game_state
        self.config = config

        self.summary_count = 0
        
    def generate_summary(self, chat_history: List[Dict[str, Any]]) -> str:
        """
        Generate a summary of the gameplay based on chat history and previous summary.
        
        Args:
            chat_history: Complete chat history to analyze (not truncated)
            
        Returns:
            A comprehensive summary of the gameplay
        """
        self.summary_count += 1
        logging.info(f"Generating gameplay summary #{self.summary_count}")
        
        # Create a system prompt for the summary generation
        system_prompt = """Summarize this gameplay session in plain text (no markdown, no bullets, no headers). Three short paragraphs:
1. SITUATION: Where the player is, current HP/party state, what just happened (2-3 sentences).
2. REVIEW: What worked or failed in the last 20 turns, any patterns to avoid (1-2 sentences).
3. NEXT: The single most important action to take next (1-2 sentences).

Rules: GAME STATE line may be stale — trust visible screenshots over it. Only describe confirmed events; drop unverified claims from the prior summary. Milestone counts MUST match the AUTHORITATIVE STORY PROGRESS line exactly. Under 1200 chars.
"""

        initial_summary_system_prompt = """Summarize the game's starting state in plain text (no markdown, no bullets). Two short paragraphs:
1. SITUATION: Game identified, objective, starting location and party state (2 sentences).
2. NEXT: Immediate first actions to take (1-2 sentences).

Only describe confirmed facts. Under 800 chars.
"""

        # Create a structured message that includes the previous summary and chat history
        messages = []

        # Get the last 60 messages, then drop any leading orphaned
        # tool_result / assistant messages so the API sees valid alternation.
        recent_history = chat_history[-60:] if len(chat_history) > 60 else chat_history[:]
        while recent_history:
            first = recent_history[0]
            if first["role"] == "assistant":
                recent_history.pop(0)
                continue
            if first["role"] == "user" and isinstance(first.get("content"), list):
                if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in first["content"]):
                    recent_history.pop(0)
                    continue
            break
        messages.extend(recent_history)
        
        # Prepare the full chat history in its original structure
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Please analyze the gameplay session and create a comprehensive summary according to the instructions in the system prompt."
                }
            ]
        })

        # Add the previous summary to the last user message
        if self.game_state.summary:
            messages[-1]["content"].append({
                "type": "text",
                "text": f"Here is the previous gameplay summary:\n\n{self.game_state.summary}"
            })

        messages[-1]["content"].append({
            "type": "text",
            "text": f"Here is the current game state:\n\n{self.game_state.get_current_state_summary()}"
        })

        # Inject ground-truth event flag progress so the summary model
        # doesn't hallucinate milestone counts
        if self.game_state.story_progress and self.game_state.story_progress.get("progress_summary"):
            messages[-1]["content"].append({
                "type": "text",
                "text": f"AUTHORITATIVE STORY PROGRESS (from RAM event flags — use these exact counts, do NOT invent different numbers):\n{self.game_state.story_progress['progress_summary']}"
            })

        # Inject real-time party data so the summary doesn't report stale HP
        if self.game_state.party_summary:
            messages[-1]["content"].append({
                "type": "text",
                "text": f"AUTHORITATIVE PARTY STATUS (real-time from RAM — use these exact HP values):\n{self.game_state.party_summary}"
            })

        system_prompt = initial_summary_system_prompt if self.game_state.turn_count == 1 else system_prompt

        try:
            response = self.client.send_request(self.config.SUMMARY, system_prompt, messages, [])

            message_content = MessageUtils.print_and_extract_message_content(response)
            text_blocks = message_content["text_blocks"]

            summary = ""

            # loop through text blocks and add to summary
            for block in text_blocks:
                summary += block.text

            # Cap summary length to prevent context bloat and hallucination surface area
            max_summary_len = 1200
            if len(summary) > max_summary_len:
                logging.warning(f"Summary too long ({len(summary)} chars), truncating to {max_summary_len}")
                # Try to truncate at a paragraph/section boundary
                truncated = summary[:max_summary_len]
                last_newline = truncated.rfind("\n")
                if last_newline > max_summary_len * 0.7:
                    truncated = truncated[:last_newline]
                summary = truncated + "\n[truncated]"

            logging.info(f"Summary generated successfully ({len(summary)} chars)")
            return summary
            
        except Exception as e:
            error_msg = f"ERROR generating summary: {str(e)}"
            logging.error(error_msg)
            return f"[SUMMARY_ERROR] {str(e)}"
