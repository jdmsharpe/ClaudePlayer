import logging
import json

class MessageUtils:
    """Utilities for analyzing and logging message structures."""
    
    @staticmethod
    def print_and_extract_message_content(message):
        """Extract message text and print it."""
        # Extract and process tool use blocks
        content_blocks = message.content

        tool_use_blocks = [block for block in content_blocks if block.type == "tool_use"]
        text_blocks = [block for block in content_blocks if block.type == "text"]
        thinking_blocks = [block for block in content_blocks if block.type == "thinking"]
        
        # Log Claude's thinking: full text at DEBUG, 1-line summary at INFO
        if thinking_blocks:
            total_chars = sum(len(b.thinking) for b in thinking_blocks)
            # First 120 chars as preview, collapsed to single line
            preview = thinking_blocks[0].thinking[:120].replace("\n", " ").strip()
            if total_chars > 120:
                preview += "..."
            logging.info(f"THINKING: {total_chars} chars — {preview}")
            logging.debug("CLAUDE'S THINKING (full):")
            for block in thinking_blocks:
                logging.debug(f"  {block.thinking}")

        # Log Claude's text response
        if text_blocks:
            logging.info("CLAUDE'S RESPONSE:")
            for block in text_blocks:
                logging.info(f"  {block.text}")
        
        # Log tool usage
        if tool_use_blocks:
            logging.info("TOOLS USED:")
            for block in tool_use_blocks:
                tool_input_str = json.dumps(block.input, indent=2)
                logging.info(f"  Tool: {block.name}")
                logging.info(f"  Input: {tool_input_str}")

        return {
            "text_blocks": text_blocks,
            "tool_use_blocks": tool_use_blocks,
            "thinking_blocks": thinking_blocks
        } 