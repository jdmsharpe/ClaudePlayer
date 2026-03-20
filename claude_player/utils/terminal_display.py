import io
import os
import sys
import threading
import time


def _encode_jpeg(pil_image):
    """Encode a PIL image to JPEG bytes (640×576, pixel-art nearest scaling)."""
    buf = io.BytesIO()
    rgb = pil_image.convert("RGB") if pil_image.mode != "RGB" else pil_image
    scaled = rgb.resize((640, 576), resample=0)  # NEAREST for pixel art
    scaled.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


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
        self.tactical_goal = ""
        self.side_objectives = ""
        self.last_action = ""
        self.last_response = ""
        self.last_thinking = ""
        self.spatial_grid = ""
        self.party_summary = ""
        self.bag_summary = ""
        self.bag_items = []
        self.party_mons = []
        self.menu_summary = ""
        self.world_map_text = ""
        self.status = "Starting..."
        self.analysis_duration = 0.0
        self.error_count = 0
        self.fps = 0.0
        self.dex_caught = 0
        self.dex_seen = 0
        self.trainer_name = ""
        self.trainer_id = 0
        self.play_time = ""
        self.badges = []
        self.session_cost = 0.0
        self._raw_frame = None   # latest raw PIL image (stored cheaply; encoding is off-thread)
        self._frame_seq = 0     # increments on each new frame, for MJPEG change detection

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

    def set_frame(self, pil_image):
        """Store latest raw PIL frame. Cheap: no encoding; JPEG encoding is off-thread."""
        if pil_image is None:
            return
        # Copy so the emulator can reuse its buffer without affecting our stored frame.
        frame_copy = pil_image.copy()
        with self._lock:
            self._raw_frame = frame_copy
            self._frame_seq += 1

    def get_raw_frame(self):
        """Return (pil_image, seq) for off-thread encoding (e.g. MJPEG generator)."""
        with self._lock:
            return self._raw_frame, self._frame_seq

    def get_frame_jpeg(self):
        """Encode and return the latest frame as JPEG bytes (for /api/frame snapshot)."""
        with self._lock:
            img = self._raw_frame
        if img is None:
            return None
        return _encode_jpeg(img)

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
            cost_str = f"  ${self.session_cost:.2f}" if self.session_cost >= 0.01 else ""
            status_line = f" Turn {self.turn} │ {self.status} │ {self._elapsed()}{fps_str}{cost_str}{error_str} "

            lines = [
                f"┌{sep}┐",
                f"│{status_line:<{w}}│",
                f"├{sep}┤",
            ]
            lines.extend(wrap_rows("Game", self.game or "(detecting...)"))
            lines.extend(wrap_rows("Goal", self.goal or "(none set)"))
            if self.tactical_goal:
                lines.extend(wrap_rows("  Sub", self.tactical_goal))
            if self.side_objectives:
                lines.extend(wrap_rows(" Side", self.side_objectives))
            lines.extend(wrap_rows("Action", self.last_action))
            lines.extend(wrap_rows("Response", self.last_response, max_lines=3))
            if self.last_thinking:
                lines.append(f"├{sep}┤")
                lines.extend(wrap_rows("Thinking", self.last_thinking, max_lines=5))
            if self.spatial_grid:
                lines.append(f"├{sep}┤")
                after_legend = False
                npc_sep_added = False
                for grid_line in self.spatial_grid.split("\n"):
                    if after_legend and not npc_sep_added and grid_line.strip():
                        lines.append(f"├{sep}┤")
                        npc_sep_added = True
                    padded = f" {grid_line} "
                    lines.append(f"│{padded:<{w}}│")
                    if grid_line.startswith(". = walkable"):
                        after_legend = True
            if self.world_map_text:
                lines.append(f"├{sep}┤")
                for map_line in self.world_map_text.split("\n"):
                    padded = f" {map_line} "
                    lines.append(f"│{padded:<{w}}│")
            if self.party_summary or self.bag_summary or self.menu_summary:
                lines.append(f"├{sep}┤")
                if self.menu_summary:
                    lines.extend(wrap_rows("Menu", self.menu_summary))
                if self.party_summary:
                    lines.extend(wrap_rows("Party", self.party_summary))
                if self.bag_summary:
                    lines.extend(wrap_rows("Bag", self.bag_summary))
            lines.append(f"└{sep}┘")

            # Clear screen and draw from top
            sys.stdout.write("\033[2J\033[H")
            for line in lines:
                sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
