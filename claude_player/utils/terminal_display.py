import os
import sys
import threading
import time


class TerminalDisplay:
    """Live-updating terminal status display using ANSI escape sequences.

    Clears the screen on each redraw so only the status box is visible.
    Adapts width to the terminal. Falls back to no-op when stdout is not a TTY.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._is_tty = sys.stdout.isatty()
        self._start_time = time.time()

        # Status fields
        self.turn = 0
        self.game = ""
        self.goal = ""
        self.last_action = ""
        self.last_response = ""
        self.last_thinking = ""
        self.status = "Starting..."
        self.analysis_duration = 0.0
        self.error_count = 0
        self.fps = 0.0

    # --- public API ---

    def update(self, **kwargs):
        """Update one or more fields and redraw."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
        self._draw()

    def print_event(self, text: str):
        """Print a one-off event line. Currently a no-op since we clear the screen."""
        pass

    # --- internals ---

    def _elapsed(self) -> str:
        elapsed = int(time.time() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            return f"{hours}h{mins:02d}m"
        return f"{mins}m{secs:02d}s"

    def _draw(self):
        if not self._is_tty:
            return

        with self._lock:
            # Use terminal width, leave 2 chars for box borders
            try:
                term_w = os.get_terminal_size().columns
            except OSError:
                term_w = 80
            w = max(term_w - 2, 40)  # inner width between │ borders
            sep = "─" * w

            def wrap_rows(label: str, value: str, max_lines: int = 0) -> list:
                """Wrap a label+value into multiple rows that fit the box width.

                max_lines=0 means unlimited.
                """
                value = value.replace("\n", " ").strip() if value else "-"
                prefix = f" {label}: "
                continuation = " " * len(prefix)  # indent wrapped lines

                result = []
                remaining = value
                first = True

                while remaining:
                    lead = prefix if first else continuation
                    avail = w - len(lead) - 1  # -1 for trailing space
                    if avail <= 0:
                        avail = 10

                    if len(remaining) <= avail:
                        content = f"{lead}{remaining} "
                        result.append(f"│{content:<{w}}│")
                        break
                    else:
                        # Try to break at a space
                        break_at = remaining.rfind(" ", 0, avail)
                        if break_at <= 0:
                            break_at = avail  # Force break mid-word
                        chunk = remaining[:break_at]
                        remaining = remaining[break_at:].lstrip()
                        content = f"{lead}{chunk} "
                        result.append(f"│{content:<{w}}│")

                    first = False
                    if max_lines and len(result) >= max_lines:
                        # Truncate the last line with ellipsis
                        last = result[-1]
                        result[-1] = last[: -3] + "…│"
                        break

                return result if result else [f"│{prefix}- {'':<{w - len(prefix) - 2}}│"]

            error_str = f"  errors: {self.error_count}" if self.error_count else ""
            fps_str = f"  {self.fps:.0f} FPS" if self.fps else ""
            status_line = f" Turn {self.turn} │ {self.status} │ {self._elapsed()}{fps_str}{error_str} "

            lines = [
                f"┌{sep}┐",
                f"│{status_line:<{w}}│",
                f"├{sep}┤",
            ]
            lines.extend(wrap_rows("Game", self.game or "(detecting...)"))
            lines.extend(wrap_rows("Goal", self.goal or "(none set)"))
            lines.extend(wrap_rows("Action", self.last_action))
            lines.extend(wrap_rows("Response", self.last_response, max_lines=3))
            if self.last_thinking:
                lines.append(f"├{sep}┤")
                lines.extend(wrap_rows("Thinking", self.last_thinking, max_lines=5))
            lines.append(f"└{sep}┘")

            # Clear screen and draw from top
            sys.stdout.write("\033[2J\033[H")
            for line in lines:
                sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
