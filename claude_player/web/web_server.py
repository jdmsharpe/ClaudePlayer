"""Flask-based web dashboard for streaming / OBS browser source.

Reads state from TerminalDisplay (shared, thread-safe) and serves it
as JSON + JPEG frame over HTTP.  Runs in a daemon thread alongside the
emulator — no extra process needed.
"""

import logging
import threading
import time

from flask import Flask, Response, jsonify

from claude_player.utils.terminal_display import _encode_jpeg

logger = logging.getLogger(__name__)


class WebStreamer:
    """Lightweight web dashboard that mirrors the terminal display."""

    def __init__(self, display, port: int = 5555, config=None):
        self.display = display
        self.port = port
        self.config = config
        self._app = Flask(__name__)
        self._setup_routes()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _setup_routes(self):
        app = self._app

        @app.route("/")
        def index():
            return Response(DASHBOARD_HTML, mimetype="text/html")

        @app.route("/api/state")
        def api_state():
            d = self.display
            with d._lock:
                data = {
                    "turn": d.turn,
                    "game": d.game,
                    "goal": d.goal,
                    "status": d.status,
                    "fps": d.fps,
                    "elapsed": d._elapsed(),
                    "last_action": d.last_action,
                    "last_response": d.last_response,
                    "last_thinking": d.last_thinking,
                    "spatial_grid": d.spatial_grid,
                    "location": d.location,
                    "party_summary": d.party_summary,
                    "party_mons": d.party_mons,
                    "bag_summary": d.bag_summary,
                    "bag_items": d.bag_items,
                    "menu_summary": d.menu_summary,
                    "error_count": d.error_count,
                    "dex_caught": d.dex_caught,
                    "dex_seen": d.dex_seen,
                    "trainer_name": d.trainer_name,
                    "trainer_id": d.trainer_id,
                    "play_time": d.play_time,
                    "badges": d.badges,
                }
            return jsonify(data)

        @app.route("/api/config")
        def api_config():
            cfg = self.config
            if cfg is None:
                return jsonify({})
            action = getattr(cfg, "ACTION", {}) or {}
            summary = getattr(cfg, "SUMMARY", {}) or {}
            return jsonify({
                "model": action.get("MODEL", ""),
                "max_tokens": action.get("MAX_TOKENS", ""),
                "thinking_budget": action.get("THINKING_BUDGET", ""),
                "summary_interval": summary.get("SUMMARY_INTERVAL", ""),
                "summary_model": summary.get("MODEL", ""),
            })

        @app.route("/api/frame")
        def api_frame():
            jpeg = self.display.get_frame_jpeg()
            if jpeg is None:
                return Response(b"", status=204)
            return Response(
                jpeg,
                mimetype="image/jpeg",
                headers={"Cache-Control": "no-cache"},
            )

        @app.route("/stream")
        def stream():
            return Response(
                self._mjpeg_generator(),
                mimetype="multipart/x-mixed-replace; boundary=frame",
            )

    # ------------------------------------------------------------------
    # MJPEG streaming
    # ------------------------------------------------------------------

    def _mjpeg_generator(self):
        """Yield MJPEG frames as a multipart stream, pushing each new frame once.

        Encoding happens here (Flask worker thread) so the main emulator loop
        is never blocked by PIL resize / JPEG compression.
        """
        last_seq = -1
        while True:
            raw, seq = self.display.get_raw_frame()
            if seq != last_seq and raw is not None:
                jpeg = _encode_jpeg(raw)
                last_seq = seq
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                )
            else:
                time.sleep(0.016)  # ~60 Hz check rate when no new frame

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Launch Flask in a daemon thread."""
        t = threading.Thread(target=self._run, daemon=True, name="web-streamer")
        t.start()
        logger.info(f"Web streamer started on http://localhost:{self.port}")

    def _run(self):
        # Suppress Werkzeug/Flask logs (startup banner + per-request noise)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        import click as _click
        _click.echo = lambda *a, **kw: None  # silence Flask's "Serving" banner
        self._app.run(
            host="0.0.0.0",
            port=self.port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )


# ======================================================================
# Inline HTML dashboard (single-page, no build tools)
# ======================================================================

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1280">
<title>ClaudePlayer</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: #0d1117;
    color: #e6edf3;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px;
    overflow: hidden;
    height: 100vh;
    width: 100vw;
    display: flex;
    flex-direction: column;
  }

  /* ---------- Unified section label ---------- */
  /* All section headers (panel-label, info-label, ai-label) share one style */
  .panel-label, .info-label, .ai-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #6e7681;
    margin-bottom: 5px;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .panel-label::before, .info-label::before, .ai-label::before {
    content: '';
    display: inline-block;
    width: 2px;
    height: 9px;
    border-radius: 2px;
    flex-shrink: 0;
  }

  /* ---------- Status bar ---------- */
  .status-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    background: linear-gradient(180deg, #1c2128 0%, #161b22 100%);
    border-bottom: 1px solid #30363d;
    font-size: 13px;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 10px;
    background: #21262d;
    border: 1px solid #30363d;
    white-space: nowrap;
  }
  .pill-label { color: #6e7681; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
  .pill-value { color: #e6edf3; font-weight: 600; }
  .pill.game { background: #0d419d22; border-color: #1f6feb40; }
  .pill.game .pill-value { color: #58a6ff; }
  .pill.status .pill-value { color: #7ee787; }
  .pill.status.analyzing .pill-value { animation: pulse 1.5s ease-in-out infinite; color: #d2a8ff; }
  .pill.error { background: #f8514918; border-color: #f8514940; }
  .pill.error .pill-value { color: #f85149; }
  .spacer { flex: 1; }
  .title-text {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.2px;
    color: #e6edf3;
    white-space: nowrap;
    margin-right: 2px;
  }
  .title-text em {
    font-style: normal;
    background: linear-gradient(90deg, #58a6ff, #bc8cff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .cfg-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 7px;
    border-radius: 6px;
    background: #161b22;
    border: 1px solid #21262d;
    white-space: nowrap;
    font-size: 11px;
  }
  .cfg-badge .cfg-key { color: #6e7681; text-transform: uppercase; letter-spacing: 0.5px; font-size: 10px; }
  .cfg-badge .cfg-val { color: #c9d1d9; font-weight: 600; font-family: "Cascadia Mono", "Fira Code", monospace; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }

  /* ---------- Main grid ---------- */
  .main {
    display: grid;
    grid-template-columns: 1fr 540px 720px;
    grid-template-rows: auto 1fr auto;
    gap: 0;
    flex: 1;
    min-height: 0;
  }

  /* ---------- Game frame ---------- */
  .frame-panel {
    grid-row: 2;
    grid-column: 1;
    background: #010409;
    display: flex;
    align-items: center;
    justify-content: center;
    border-right: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
    padding: 6px;
    overflow: hidden;
  }
  .frame-panel img {
    height: 100%;
    width: auto;
    image-rendering: pixelated;
    border: 2px solid #30363d;
    border-radius: 4px;
    box-shadow: 0 0 16px #00000060, 0 0 4px #30363d40;
  }

  /* ---------- Left bottom (action + bag) ---------- */
  .left-bottom {
    grid-row: 3;
    grid-column: 1;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 12px;
    border-top: 1px solid #30363d;
    border-right: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
    background: #0d1117;
    overflow: hidden;
  }
  .info-section {
    padding: 6px 10px;
    background: #161b22;
    border-radius: 6px;
    border: 1px solid #21262d;
    overflow: hidden;
  }
  .action-section .info-label::before { background: #ffa657; }
  .action-section { text-align: center; flex-shrink: 0; }
  .bag-section .info-label::before { background: #e3b341; }
  .bag-section { flex-shrink: 0; }

  /* ---------- Right panels ---------- */
  .right-panels {
    grid-row: 2;
    grid-column: 2;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
    overflow: hidden;
    border-bottom: 1px solid #30363d;
  }

  .panel {
    padding: 8px 10px;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    flex-shrink: 0;
  }

  .grid-panel .panel-label::before { background: #7ee787; }
  .goal-bar {
    grid-row: 1;
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 12px;
    background: #161b22;
    border-bottom: 1px solid #30363d;
  }
  .goal-bar .panel-label { margin-bottom: 0; }
  .goal-bar .panel-label::before { background: #58a6ff; }
  .goal-bar .ai-goal-text { font-size: 12px; }
  .party-panel .panel-label::before { background: #f85149; }
  .bag-panel .panel-label::before { background: #e3b341; }
  .menu-panel .panel-label::before { background: #d2a8ff; }
  .location-panel .panel-label::before { background: #79c0ff; }
  .location-panel { flex-shrink: 0; }
  #location-info { color: #e6edf3; white-space: pre; line-height: 1.5; }

  /* Spatial grid */
  .grid-panel { flex: 1; overflow: hidden; min-width: 0; }
  #spatial-grid {
    font-family: "Cascadia Mono", "Fira Code", "Consolas", monospace;
    overflow: hidden;
  }
  #spatial-grid.empty { color: #484f58; font-style: italic; }

  /* Tile table */
  #tile-table {
    border-collapse: collapse;
    margin: 8px 0;
    font-family: "Cascadia Mono", "Fira Code", "Consolas", monospace;
  }
  #tile-table td, #tile-table th {
    width: 38px;
    height: 38px;
    text-align: center;
    vertical-align: middle;
    font-size: 18px;
    padding: 0;
    border: 1px solid #080b10;
    line-height: 1;
  }
  #tile-table .row-num {
    color: #484f58;
    font-size: 11px;
    padding-right: 6px;
    text-align: right;
    border: none;
    background: transparent;
    white-space: nowrap;
    min-width: 24px;
  }
  #tile-table .col-num {
    color: #484f58;
    font-size: 10px;
    border: none;
    background: transparent;
    height: 14px;
  }
  /* Tile cell backgrounds */
  .tc-w  { background: #161b22; color: #21262d; }
  .tc-f  { background: #0d1117; color: #3a4048; }
  .tc-g  { background: #071b05; color: #3fb950; }
  .tc-a  { background: #020d1a; color: #58a6ff; }
  .tc-l  { background: #191100; color: #d29922; font-weight: bold; }
  .tc-t  { background: #07160a; color: #56d364; font-weight: bold; }
  .tc-b  { background: #190f00; color: #d29922; font-weight: bold; }
  .tc-e  { background: #150a28; color: #bc8cff; font-weight: bold; box-shadow: inset 0 0 4px #bc8cff30; }
  .tc-p  { background: #2a1500; color: #ffa657; font-weight: bold; box-shadow: inset 0 0 6px #ffa65740; }
  .tc-n  { background: #280a0a; color: #f85149; font-weight: bold; }
  .tc-i  { background: #281e00; color: #e3b341; font-weight: bold; }
  .tc-o  { background: #0e141a; color: #6e7681; }
  .tc-gh { background: #110a28; color: #8957e5; font-weight: bold; }

  /* Tile colors (used in legend symbols) */
  .tw  { color: #6e7681; }
  .tf  { color: #8b949e; }
  .tg  { color: #3fb950; }
  .ta  { color: #58a6ff; }
  .tl  { color: #d29922; }
  .tt  { color: #3fb950; font-weight: bold; }
  .tb  { color: #d29922; font-weight: bold; }
  .te  { color: #bc8cff; font-weight: bold; text-shadow: 0 0 4px #bc8cff40; }
  .tp  { color: #ffa657; font-weight: bold; text-shadow: 0 0 8px #ffa65780; }
  .tn  { color: #f85149; font-weight: bold; }
  .ti  { color: #e3b341; font-weight: bold; text-shadow: 0 0 4px #e3b34150; }
  .to  { color: #8b949e; }
  .tgh { color: #6e40c9; }
  .grid-num    { color: #6e7681; }
  .grid-header { color: #8b949e; line-height: 1.5; }
  .grid-legend { display: flex; flex-wrap: wrap; gap: 3px 12px; margin-top: 8px; margin-bottom: 10px; }
  .legend-entry { display: inline-flex; align-items: center; gap: 3px; }
  .legend-sym { font-weight: bold; font-size: 13px; }
  .legend-desc { color: #6e7681; font-size: 11px; }
  .npc-line { line-height: 1.5; }
  .npc-id   { color: #f85149; font-weight: bold; }
  .npc-name { color: #e6edf3; font-weight: 600; }
  .npc-dir  { color: #79c0ff; }
  .npc-tag  { color: #f85149; font-size: 10px; }
  .npc-tag.dim { color: #484f58; }
  .progress-bar {
    display: inline-block;
    width: 120px;
    height: 8px;
    background: #21262d;
    border-radius: 4px;
    vertical-align: middle;
    margin: 0 6px;
    overflow: hidden;
    border: 1px solid #30363d;
  }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #238636, #3fb950);
    border-radius: 4px;
    transition: width 0.5s ease;
  }
  .next-goal { color: #58a6ff; }

  /* Battle context */
  #battle-view { display: none; overflow-y: auto; padding: 4px 0; }
  .battle-header {
    color: #8b949e;
    padding-bottom: 6px;
    margin-bottom: 6px;
  }
  .battle-section {
    margin-bottom: 8px;
    padding: 8px 10px;
    background: #161b22;
    border-radius: 6px;
    border: 1px solid #21262d;
  }
  .battle-section.your { border-left: 3px solid #3fb950; }
  .battle-section.enemy { border-left: 3px solid #f85149; }
  .battle-section-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: #6e7681; margin-bottom: 5px;
  }
  .battle-info-row { display: flex; align-items: center; gap: 8px; }
  .battle-hp-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .battle-hp-row .hp-bar { flex: 1; max-width: 200px; }
  .battle-mon-name { font-weight: 700; color: #e6edf3; }
  .battle-mon-level { color: #8b949e; }
  .battle-mon-status { font-size: 11px; font-weight: 600; }
  .battle-mon-status.ok { color: #3fb950; }
  .battle-mon-status.fnt { color: #f85149; }
  .battle-mon-status.bad { color: #d29922; }
  .battle-moves-divider { border: none; border-top: 1px solid #21262d; margin: 8px 0; }
  .type-badge {
    display: inline-block; padding: 1px 5px; border-radius: 3px;
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.3px; color: #fff;
  }
  .type-normal   { background: #a8a878; color: #333; }
  .type-fire     { background: #f08030; }
  .type-water    { background: #6890f0; }
  .type-electric { background: #f8d030; color: #333; }
  .type-grass    { background: #78c850; }
  .type-ice      { background: #98d8d8; color: #333; }
  .type-fighting { background: #c03028; }
  .type-poison   { background: #a040a0; }
  .type-ground   { background: #e0c068; color: #333; }
  .type-flying   { background: #a890f0; }
  .type-psychic  { background: #f85888; }
  .type-bug      { background: #a8b820; color: #333; }
  .type-rock     { background: #b8a038; }
  .type-ghost    { background: #705898; }
  .type-dragon   { background: #7038f8; }
  .battle-stats { display: flex; gap: 10px; margin-top: 6px; }
  .battle-stat-col { min-width: 44px; }
  .battle-stat-label { color: #6e7681; font-size: 10px; text-transform: uppercase; letter-spacing: 0.3px; }
  .battle-stat-value { color: #c9d1d9; font-weight: 600; font-size: 12px; }
  .move-row {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 6px; border-radius: 3px; margin-bottom: 3px;
  }
  .move-row.active { background: #1f6feb20; border: 1px solid #1f6feb40; }
  .move-cursor { color: #58a6ff; font-weight: bold; width: 10px; text-align: center; font-size: 11px; }
  .move-name { font-weight: 600; color: #e6edf3; min-width: 110px; }
  .move-power { color: #8b949e; min-width: 52px; }
  .move-pp { color: #8b949e; min-width: 52px; }
  .move-hm { color: #d2a8ff; font-size: 10px; font-weight: 600; }
  .battle-menu {
    color: #d2a8ff;
    padding: 3px 8px; margin-bottom: 4px;
  }
  .battle-tip {
    padding: 4px 8px; background: #e3b34110;
    border-left: 3px solid #e3b341;
    border-radius: 4px; color: #e3b341; font-weight: 600;
  }

  /* Markdown in AI panels */
  .ai-response code, .ai-thinking code {
    font-family: "Cascadia Mono", "Fira Code", monospace;
    font-size: 12px; background: #21262d; padding: 1px 4px;
    border-radius: 3px; border: 1px solid #30363d; color: #e6edf3;
  }
  .ai-response strong, .ai-thinking strong { color: #e6edf3; }

  /* Party */
  .party-bar > .panel { overflow-y: auto; }
  .party-panel .mon {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }
  .mon-name { font-weight: 600; width: 110px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mon-level { color: #8b949e; width: 34px; flex-shrink: 0; font-size: 11px; }
  .mon-types { display: flex; align-items: center; gap: 3px; width: 100px; flex-shrink: 0; }
  .mon-status-col { width: 32px; flex-shrink: 0; font-size: 11px; font-weight: 600; color: #f85149; }
  .hp-bar {
    width: 100%;
    height: 5px;
    background: #21262d;
    border-radius: 3px;
    overflow: hidden;
    border: 1px solid #30363d;
  }
  .hp-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
  }
  .hp-text { color: #8b949e; font-size: 11px; width: 46px; flex-shrink: 0; text-align: right; font-variant-numeric: tabular-nums; }
  .mon-status { color: #f85149; font-size: 11px; font-weight: 600; }
  .bars-col { display: flex; flex-direction: column; flex: 1; gap: 2px; }
  .exp-bar {
    width: 100%;
    height: 3px;
    background: #21262d;
    border-radius: 3px;
    overflow: hidden;
    border: 1px solid #30363d;
  }
  .exp-fill {
    height: 100%;
    border-radius: 3px;
    background: #58a6ff;
    transition: width 0.4s ease;
  }

  /* Bag */
  .bag-items { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 5px; }
  .bag-item {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 10px; border-radius: 6px;
    background: #21262d; border: 1px solid #30363d;
  }
  .bag-item-label { color: #6e7681; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
  .bag-item-value { color: #e6edf3; font-weight: 700; font-size: 13px; }
  .bag-inv { font-size: 11px; color: #8b949e; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bag-inv-name { color: #c9d1d9; }
  .bag-inv-qty { color: #6e7681; font-size: 10px; }
  .bag-item.badges { border-color: #e3b34140; }
  .bag-item.badges .bag-item-value { color: #e3b341; }
  .bag-item.money { border-color: #3fb95040; }
  .bag-item.money .bag-item-value { color: #3fb950; }
  .bag-item.balls { border-color: #f8514940; }
  .bag-item.balls .bag-item-value { color: #f85149; }
  .bag-item.medicine { border-color: #bc8cff40; }
  .bag-item.medicine .bag-item-value { color: #d2a8ff; }
  .bag-item.key { border-color: #58a6ff40; }
  .bag-item.key .bag-item-value { color: #58a6ff; }
  .bag-item.dex { border-color: #d2a8ff40; }
  .bag-item.dex .bag-item-value { color: #d2a8ff; }
  .bag-item.trainer { border-color: #ffa65740; }
  .bag-item.trainer .bag-item-value { color: #ffa657; }
  .bag-item.trainer-id { border-color: #6e768140; }
  .bag-item.trainer-id .bag-item-value { color: #8b949e; }
  .bag-item.time { border-color: #79c0ff40; }
  .bag-item.time .bag-item-value { color: #79c0ff; }

  /* Menu */
  .menu-panel { color: #d2a8ff; }

  /* ---------- Left info (action + bag, under game frame) ---------- */
  .left-info {
    grid-row: 3;
    grid-column: 1;
    display: flex;
    border-top: 1px solid #30363d;
    border-right: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
    background: #0d1117;
  }
  .left-info > .panel {
    border-bottom: 1px solid #21262d;
    border-right: none;
    padding: 8px 10px;
    flex: 1;
    overflow: hidden;
    margin: 8px;
    border-radius: 6px;
    background: #161b22;
  }
  .left-info > .panel:last-child { margin-left: 0; }
  .left-info .ai-action-section {
    padding: 8px 12px;
    background: #161b22;
    display: flex;
    flex-direction: column;
    align-items: center;
  }

  /* ---------- Party bar (under battle/map view) ---------- */
  .party-bar {
    grid-row: 3;
    grid-column: 2;
    border-top: 1px solid #30363d;
    border-right: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
    background: #0d1117;
  }
  .party-bar > .panel {
    border-bottom: 1px solid #21262d;
    padding: 8px 10px;
    margin: 8px;
    border-radius: 6px;
    background: #161b22;
  }

  /* ---------- AI panel (right column) ---------- */
  .ai-panel {
    grid-row: 2 / span 2;
    grid-column: 3;
    padding: 8px 12px;
    overflow: hidden;
    display: grid;
    grid-template-columns: 1fr;
    grid-template-rows: 2fr 1fr;
    gap: 8px;
    min-height: 0;
    border-left: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
  }
  .ai-section {
    overflow: hidden;
    padding: 8px 10px;
    background: #161b22;
    border-radius: 6px;
    border: 1px solid #21262d;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .ai-goal-text { color: #58a6ff; font-weight: 600; }
  .ai-action-section {
    padding: 4px 8px;
  }
  .ai-action-section .ai-label {
    margin-bottom: 2px;
    justify-content: flex-start;
  }
  .ai-action-section .ai-label::before { background: #ffa657; }
  .ai-response-section .ai-label::before { background: #7ee787; }
  .ai-thinking-section .ai-label::before { background: #6e7681; }
  .ai-thinking {
    color: #c9d1d9;
    overflow-y: auto;
    line-height: 1.5;
    flex: 1;
    min-height: 0;
  }
  .ai-action {
    color: #ffa657;
    font-family: "Cascadia Mono", "Fira Code", monospace;
    font-size: 30px;
    font-weight: 700;
    letter-spacing: 3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: center;
    width: 100%;
    height: 40px;
    line-height: 40px;
  }
  .ai-response {
    color: #c9d1d9;
    overflow-y: auto;
    line-height: 1.5;
    flex: 1;
    min-height: 0;
  }

  .empty { color: #484f58; font-style: italic; }
</style>
</head>
<body>

<div class="status-bar">
  <span class="title-text">Claude Plays <em>Pokémon!</em></span>
  <div class="pill game">
    <span class="pill-value" id="game-name">-</span>
  </div>
  <div class="pill">
    <span class="pill-label">Turn</span>
    <span class="pill-value" id="turn">0</span>
  </div>
  <div class="pill status" id="status-pill">
    <span class="pill-value" id="status">-</span>
  </div>
  <div class="pill">
    <span class="pill-label">Time</span>
    <span class="pill-value" id="elapsed">0m00s</span>
  </div>
  <div class="pill">
    <span class="pill-value"><span id="fps">0</span> FPS</span>
  </div>
  <span class="spacer"></span>
  <div id="cfg-badges" style="display:none;align-items:center;gap:6px;"></div>
  <div class="pill error" id="error-pill" style="display:none">
    <span class="pill-value" id="error-count"></span>
  </div>
</div>

<div class="main">
  <div class="goal-bar">
    <div class="panel-label">Goal</div>
    <div class="ai-goal-text" id="ai-goal">-</div>
  </div>

  <div class="frame-panel">
    <img id="game-frame" src="/stream" alt="Game">
  </div>

  <div class="right-panels">
    <div class="panel location-panel" id="location-panel" style="display:none">
      <div class="panel-label">Location</div>
      <div id="location-info"></div>
    </div>
    <div class="panel grid-panel">
      <div class="panel-label" id="grid-label">Map</div>
      <div id="spatial-grid" class="empty">Waiting for data...</div>
      <div id="battle-view"></div>
    </div>
    <div class="panel menu-panel" id="menu-panel" style="display:none">
      <div class="panel-label">Menu</div>
      <div id="menu-info"></div>
    </div>
  </div>

  <div class="left-bottom">
    <div class="info-section action-section">
      <div class="info-label">Last Action</div>
      <div class="ai-action" id="ai-action">-</div>
    </div>
    <div class="info-section bag-section">
      <div class="info-label">Trainer</div>
      <div id="bag-info">-</div>
    </div>
  </div>

  <div class="party-bar">
    <div class="panel party-panel">
      <div class="panel-label">Party</div>
      <div id="party-list"><span class="empty">-</span></div>
    </div>
  </div>

  <div class="ai-panel">
    <div class="ai-section ai-thinking-section">
      <div class="ai-label">Thinking</div>
      <div class="ai-thinking" id="ai-thinking">-</div>
    </div>
    <div class="ai-section ai-response-section">
      <div class="ai-label">Response</div>
      <div class="ai-response" id="ai-response">-</div>
    </div>
  </div>
</div>

<script>
const STATE_MS = 500;

/* Claude Code spinner — same character sequence */
const SPINNER_FRAMES = ['\u00b7', '\u273b', '\u273d', '\u2736', '\u2733', '\u2722'];
let _spinnerIdx = 0, _spinnerTimer = null, _lastAction = '-';
function startSpinner() {
  if (_spinnerTimer) return;
  const el = document.getElementById('ai-action');
  el.textContent = SPINNER_FRAMES[0];
  _spinnerTimer = setInterval(function() {
    _spinnerIdx = (_spinnerIdx + 1) % SPINNER_FRAMES.length;
    el.textContent = SPINNER_FRAMES[_spinnerIdx];
  }, 120);
}
function stopSpinner() {
  if (_spinnerTimer) { clearInterval(_spinnerTimer); _spinnerTimer = null; }
  document.getElementById('ai-action').textContent = _lastAction;
}

/* Tile char -> text color class (used in legend) */
const TC = {
  '#':'tw','.':'tf',',':'tg','=':'ta','v':'tl','>':'tl','<':'tl',
  'T':'tt','B':'tb','W':'te','@':'tp','i':'ti','o':'to','g':'tgh'
};
/* Tile char -> table cell background class */
const TCB = {
  '#':'tc-w','.':'tc-f',',':'tc-g','=':'tc-a','v':'tc-l','>':'tc-l','<':'tc-l','^':'tc-l',
  'T':'tc-t','B':'tc-b','W':'tc-e','@':'tc-p','i':'tc-i','o':'tc-o','g':'tc-gh'
};

async function pollState() {
  try {
    const res = await fetch('/api/state');
    if (!res.ok) return;
    const d = await res.json();

    setText('game-name', d.game || '-');
    setText('turn', d.turn || 0);
    setText('status', d.status || '-');
    setText('elapsed', d.elapsed || '-');
    setText('fps', d.fps ? d.fps.toFixed(0) : '0');
    setText('ai-goal', d.goal || '-');
    _lastAction = d.last_action || '-';
    renderMarkdown('ai-response', d.last_response || '-');
    renderMarkdown('ai-thinking', d.last_thinking || '-');

    /* Analyzing pulse */
    const sp = document.getElementById('status-pill');
    const st = (d.status || '').toLowerCase();
    const isThinking = st.includes('analyz') || st.includes('thinking');
    sp.classList.toggle('analyzing', isThinking);
    isThinking ? startSpinner() : stopSpinner();

    renderGrid(d.spatial_grid);
    const locPanel = document.getElementById('location-panel');
    if (d.location) { locPanel.style.display = ''; setText('location-info', d.location); }
    else { locPanel.style.display = 'none'; }
    renderBag(d.bag_summary, d.bag_items, d.dex_caught, d.trainer_name, d.trainer_id, d.play_time);

    const menuPanel = document.getElementById('menu-panel');
    if (d.menu_summary) { menuPanel.style.display = ''; setText('menu-info', d.menu_summary); }
    else { menuPanel.style.display = 'none'; }

    const errPill = document.getElementById('error-pill');
    if (d.error_count > 0) { setText('error-count', 'Errors: ' + d.error_count); errPill.style.display = ''; }
    else { errPill.style.display = 'none'; }

    renderParty(d.party_summary, d.party_mons);
  } catch (e) { /* retry next interval */ }
}

function setText(id, v) { document.getElementById(id).textContent = v; }
function mkSpan(parent, text, cls) {
  const s = document.createElement('span');
  s.className = cls;
  s.textContent = text;
  parent.appendChild(s);
  return s;
}

/* ---- Colorized grid renderer ---- */
function renderGrid(text) {
  const gridEl = document.getElementById('spatial-grid');
  const battleEl = document.getElementById('battle-view');
  const labelEl = document.getElementById('grid-label');

  if (!text) {
    gridEl.textContent = 'Waiting for data...'; gridEl.className = 'empty';
    gridEl.style.display = ''; battleEl.style.display = 'none';
    labelEl.textContent = 'Map';
    return;
  }

  /* Detect battle context */
  if (text.includes('BATTLE CONTEXT')) {
    gridEl.style.display = 'none';
    battleEl.style.display = 'block';
    labelEl.textContent = 'Battle';
    renderBattle(battleEl, text);
    return;
  }

  /* Map mode */
  gridEl.style.display = ''; battleEl.style.display = 'none';
  labelEl.textContent = 'Map';
  gridEl.className = '';
  gridEl.textContent = '';

  const lines = text.split('\n');
  let table = null, tbody = null;

  function flushTable() {
    if (table) { gridEl.appendChild(table); table = null; tbody = null; }
  }
  function mkHeaderDiv(line) {
    flushTable();
    const d = document.createElement('div');
    d.className = 'grid-header';
    d.textContent = line;
    gridEl.appendChild(d);
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trimStart();
    if (!line.trim()) continue;

    if (trimmed.startsWith('GAME STATE')) {
      mkHeaderDiv(line);
    } else if (trimmed.startsWith('PROGRESS')) {
      flushTable();
      const d = document.createElement('div');
      renderProgress(d, line);
      gridEl.appendChild(d);
    } else if (/^\s+[\d ]+$/.test(line) && !table) {
      /* Column-number header row — start the tile table */
      table = document.createElement('table');
      table.id = 'tile-table';
      tbody = document.createElement('tbody');
      table.appendChild(tbody);
      const tr = document.createElement('tr');
      const th0 = document.createElement('th');
      th0.className = 'row-num';
      tr.appendChild(th0);
      for (const ch of line.trim().replace(/\s/g, '')) {
        const th = document.createElement('th');
        th.className = 'col-num';
        th.textContent = ch;
        tr.appendChild(th);
      }
      tbody.appendChild(tr);
    } else if (/^\s*\d{1,2} [^:]/.test(line) && table) {
      renderTileTableRow(tbody, line);
    } else if (line.includes('= walkable') || line.includes('= blocked')) {
      flushTable();
      renderLegend(gridEl, line);
    } else if (/^\s*\d+:/.test(trimmed)) {
      flushTable();
      const d = document.createElement('div');
      renderNpc(d, line);
      gridEl.appendChild(d);
    } else {
      mkHeaderDiv(line);
    }
  }
  flushTable();
}

function renderTileTableRow(tbody, line) {
  const m = line.match(/^(\s*\d+)\s(.*)/);
  if (!m) return;
  const tr = document.createElement('tr');
  const th = document.createElement('th');
  th.className = 'row-num';
  th.textContent = m[1].trim();
  tr.appendChild(th);
  for (const ch of m[2]) {
    const td = document.createElement('td');
    td.className = (ch >= '1' && ch <= '9') ? 'tc-n' : (TCB[ch] || 'tc-f');
    td.textContent = ch;
    tr.appendChild(td);
  }
  tbody.appendChild(tr);
}

function renderProgress(parent, line) {
  const m = line.match(/PROGRESS:\s*(\d+)\/(\d+)\s*milestones\s*\(([^)]*)\)/);
  if (!m) { mkSpan(parent, line, 'grid-header'); return; }

  const [full, cur, total, lastInfo] = m;
  const pct = Math.round((parseInt(cur) / parseInt(total)) * 100);
  const before = line.substring(0, line.indexOf('PROGRESS'));
  if (before) parent.appendChild(document.createTextNode(before));
  mkSpan(parent, 'PROGRESS: ', 'grid-header');

  const bar = document.createElement('span');
  bar.className = 'progress-bar';
  const fill = document.createElement('span');
  fill.className = 'progress-fill';
  fill.style.width = pct + '%';
  bar.appendChild(fill);
  parent.appendChild(bar);

  mkSpan(parent, ' ' + cur + '/' + total + ' ', 'pill-value');
  mkSpan(parent, '(' + lastInfo + ')', 'grid-header');
}

function renderLegend(parent, line) {
  const wrap = document.createElement('div');
  wrap.className = 'grid-legend';
  /* Legend map: symbol → { css class, label } */
  const entries = [
    ['.', 'tf', 'walkable'],
    ['#', 'tw', 'blocked'],
    [',', 'tg', 'grass'],
    ['=', 'ta', 'water'],
    ['v', 'tl', 'ledge'],
    ['T', 'tt', 'cut tree'],
    ['B', 'tb', 'boulder'],
    ['W', 'te', 'exit'],
    ['@', 'tp', 'player'],
    ['1', 'tn', 'NPC'],
    ['i', 'ti', 'item'],
    ['o', 'to', 'object'],
    ['g', 'tgh', 'ghost'],
  ];
  entries.forEach(function(e) {
    const ent = document.createElement('span');
    ent.className = 'legend-entry';
    mkSpan(ent, e[0], 'legend-sym ' + e[1]);
    mkSpan(ent, e[2], 'legend-desc');
    wrap.appendChild(ent);
  });
  parent.appendChild(wrap);
}

function renderNpc(parent, line) {
  const m = line.match(/^(\s*)(\d+):\s*/);
  if (!m) { mkSpan(parent, line, 'grid-header'); return; }
  const wrap = document.createElement('span');
  wrap.className = 'npc-line';
  wrap.appendChild(document.createTextNode(m[1]));
  mkSpan(wrap, m[2], 'npc-id');
  wrap.appendChild(document.createTextNode(': '));

  const rest = line.substring(m[0].length);
  const dash = rest.indexOf(' - ');
  if (dash < 0) { mkSpan(wrap, rest, 'npc-name'); parent.appendChild(wrap); return; }

  mkSpan(wrap, rest.substring(0, dash), 'npc-name');
  wrap.appendChild(document.createTextNode(' - '));

  let info = rest.substring(dash + 3);
  const bIdx = info.indexOf('[');
  if (bIdx >= 0) {
    mkSpan(wrap, info.substring(0, bIdx).trimEnd(), 'npc-dir');
    wrap.appendChild(document.createTextNode('  '));
    const tag = info.substring(bIdx);
    mkSpan(wrap, tag, tag.includes('UNREACHABLE') || tag.includes('do NOT') ? 'npc-tag' : 'npc-tag dim');
  } else {
    mkSpan(wrap, info, 'npc-dir');
  }
  parent.appendChild(wrap);
}

/* ---- Battle context renderer ---- */
function renderBattle(el, text) {
  el.textContent = '';
  const lines = text.split('\n');

  /* Pre-scan for structured data */
  let header='', yourLine='', yourStats='', yourMoves='', enemyLine='', enemyStats='';
  let menuLine='', tipLine='', cursorMoveIdx=-1;
  let parsingYour=false, parsingEnemy=false;

  for (const line of lines) {
    const t = line.trim();
    if (/^(Wild|Trainer) battle/.test(t)) header = t;
    else if (t.startsWith('YOUR:')) { yourLine=t; parsingYour=true; parsingEnemy=false; }
    else if (t.startsWith('ENEMY:')) { enemyLine=t; parsingEnemy=true; parsingYour=false; }
    else if (t.startsWith('TIP:')) { tipLine=t.substring(4).trim(); parsingYour=false; parsingEnemy=false; }
    else if (t.startsWith('Stats:')) { if (parsingYour) yourStats=t; else if (parsingEnemy) enemyStats=t; }
    else if (t.startsWith('Moves:')) yourMoves=t.substring(6).trim();
    else if (t.charAt(0) === '\u2192' || t.startsWith('→')) menuLine=t;
    const cm = t.match(/Fight menu: cursor on move (\d+)/);
    if (cm) cursorMoveIdx = parseInt(cm[1]) - 1;
  }

  /* Header */
  if (header) { const h=document.createElement('div'); h.className='battle-header'; h.textContent=header; el.appendChild(h); }

  /* YOUR pokemon + moves (single card) */
  if (yourLine) {
    const sec = document.createElement('div'); sec.className='battle-section your';
    mkSpan(sec, 'YOUR POKEMON', 'battle-section-label');
    const m = yourLine.match(/YOUR:\s*(.+?)\s+Lv(\d+)\s*\[([^\]]+)\]\s*HP:(\d+)\/(\d+)\s*(.*)/);
    if (m) { renderBattlePokemon(sec,m[1],m[2],m[3],m[4],m[5],m[6]); }
    if (yourStats) renderBattleStats(sec, yourStats);
    /* Moves inside YOUR card */
    if (yourMoves) {
      const hr = document.createElement('hr'); hr.className='battle-moves-divider'; sec.appendChild(hr);
      const moveParts = yourMoves.split('|').map(function(s){return s.trim();}).filter(Boolean);
      moveParts.forEach(function(ms,idx) {
        const mm = ms.match(/(.+?)\s*\((\w+),\s*(\d+|status)(?:pwr)?,\s*(\d+\/\d+)pp\)(\s*\[HM\])?/);
        const row = document.createElement('div');
        row.className = 'move-row' + (idx===cursorMoveIdx ? ' active' : '');
        mkSpan(row, idx===cursorMoveIdx ? '\u25BA' : ' ', 'move-cursor');
        if (mm) {
          mkSpan(row, mm[1].trim(), 'move-name');
          mkSpan(row, mm[2], 'type-badge type-'+mm[2].toLowerCase());
          mkSpan(row, mm[3] === 'status' ? 'status' : mm[3]+'pwr', 'move-power');
          mkSpan(row, mm[4]+'pp', 'move-pp');
          if (mm[5]) mkSpan(row, 'HM', 'move-hm');
        } else { mkSpan(row, ms, 'move-name'); }
        sec.appendChild(row);
      });
    }
    el.appendChild(sec);
  }

  /* ENEMY pokemon */
  if (enemyLine) {
    const sec = document.createElement('div'); sec.className='battle-section enemy';
    mkSpan(sec, 'ENEMY', 'battle-section-label');
    const m = enemyLine.match(/ENEMY:\s*(.+?)\s+Lv(\d+)\s*\[([^\]]+)\]\s*HP:(\d+)\/(\d+)\s*(.*)/);
    if (m) { renderBattlePokemon(sec,m[1],m[2],m[3],m[4],m[5],m[6]); }
    if (enemyStats) renderBattleStats(sec, enemyStats);
    el.appendChild(sec);
  }

  /* Menu cursor */
  if (menuLine) { const md=document.createElement('div'); md.className='battle-menu'; md.textContent=menuLine; el.appendChild(md); }

  /* TIP */
  if (tipLine) { const tip=document.createElement('div'); tip.className='battle-tip'; tip.textContent=tipLine; el.appendChild(tip); }
}

function renderBattlePokemon(parent, name, level, types, hp, maxHp, status) {
  /* Row 1: Name, Level, Types, Status */
  const ir = document.createElement('div'); ir.className='battle-info-row';
  mkSpan(ir, name, 'battle-mon-name');
  mkSpan(ir, 'Lv'+level, 'battle-mon-level');
  types.split('/').forEach(function(t) { mkSpan(ir, t.trim(), 'type-badge type-'+t.trim().toLowerCase()); });
  if (status && status.trim()) {
    const st=status.trim();
    mkSpan(ir, st, 'battle-mon-status '+(st==='OK'?'ok':st==='FNT'?'fnt':'bad'));
  }
  parent.appendChild(ir);
  /* Row 2: HP bar + text */
  const hr = document.createElement('div'); hr.className='battle-hp-row';
  const pct = parseInt(maxHp)>0 ? Math.round((parseInt(hp)/parseInt(maxHp))*100) : 0;
  const color = pct>50?'#3fb950':pct>20?'#d29922':'#f85149';
  const bo = document.createElement('div'); bo.className='hp-bar';
  const bf = document.createElement('div'); bf.className='hp-fill'; bf.style.width=pct+'%'; bf.style.background=color;
  bo.appendChild(bf); hr.appendChild(bo);
  mkSpan(hr, hp+'/'+maxHp, 'hp-text');
  parent.appendChild(hr);
}

function renderBattleStats(parent, statsLine) {
  const sd = document.createElement('div'); sd.className='battle-stats';
  const sm = statsLine.match(/Atk:(\d+)\s*Def:(\d+)\s*Spd:(\d+)\s*Spc:(\d+)/);
  if (sm) {
    ['Atk','Def','Spd','Spc'].forEach(function(n,j) {
      const col = document.createElement('div'); col.className='battle-stat-col';
      const lbl = document.createElement('div'); lbl.className='battle-stat-label'; lbl.textContent=n;
      const val = document.createElement('div'); val.className='battle-stat-value'; val.textContent=sm[j+1];
      col.appendChild(lbl); col.appendChild(val); sd.appendChild(col);
    });
  }
  parent.appendChild(sd);
}

/* ---- Markdown renderer (safe DOM-based, no innerHTML) ---- */
function renderMarkdown(id, text) {
  const el = document.getElementById(id);
  el.textContent = '';
  if (!text || text === '-') { el.textContent = text || '-'; return; }
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (i > 0) el.appendChild(document.createElement('br'));
    renderInlineMd(el, lines[i]);
  }
}

function renderInlineMd(parent, text) {
  /* Match **bold** and `code` only — skip *italic* to avoid false positives */
  const re = /(\*\*(.+?)\*\*|`([^`]+)`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parent.appendChild(document.createTextNode(text.substring(last, m.index)));
    if (m[2]) {
      const b = document.createElement('strong'); b.textContent = m[2]; parent.appendChild(b);
    } else if (m[3]) {
      const c = document.createElement('code'); c.textContent = m[3]; parent.appendChild(c);
    }
    last = re.lastIndex;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.substring(last)));
}

/* ---- Bag renderer ---- */
function mkBagItem(wrap, cls, value, label) {
  const item = document.createElement('div');
  item.className = 'bag-item ' + cls;
  mkSpan(item, value, 'bag-item-value');
  mkSpan(item, label, 'bag-item-label');
  wrap.appendChild(item);
}

function renderBag(summary, items, dexCaught, trainerName, trainerId, playTime) {
  const el = document.getElementById('bag-info');
  el.textContent = '';
  if (!summary && dexCaught === undefined) { mkSpan(el, '-', 'empty'); return; }
  const wrap = document.createElement('div');
  wrap.className = 'bag-items';
  if (trainerName)              mkBagItem(wrap, 'trainer', trainerName, 'trainer');
  if (trainerId)                mkBagItem(wrap, 'trainer-id', '#' + String(trainerId).padStart(5, '0'), 'ID no.');
  if (dexCaught !== undefined)  mkBagItem(wrap, 'dex', dexCaught + '/151', 'dex');
  if (playTime)                 mkBagItem(wrap, 'time', playTime, 'time');
  if (!summary) { el.appendChild(wrap); return; }
  const parts = summary.split('|').map(s => s.trim()).filter(Boolean);
  parts.forEach(part => {
    const item = document.createElement('div');
    item.className = 'bag-item';
    const bm = part.match(/^(\d+)\s*badges?$/i);
    const mm = part.match(/^\$(\d+)$/);
    const pm = part.match(/^Balls?:(\d+)$/i);
    const hm = part.match(/^Medicine:(\d+)$/i);
    const km = part.match(/^Key:\s*(.+)$/i);
    if (bm) {
      item.classList.add('badges');
      mkSpan(item, bm[1] + '/8', 'bag-item-value');
      mkSpan(item, 'badges', 'bag-item-label');
    } else if (mm) {
      item.classList.add('money');
      mkSpan(item, '$'+mm[1], 'bag-item-value');
      mkSpan(item, 'money', 'bag-item-label');
    } else if (pm) {
      item.classList.add('balls');
      mkSpan(item, pm[1], 'bag-item-value');
      mkSpan(item, 'balls', 'bag-item-label');
    } else if (hm) {
      item.classList.add('medicine');
      mkSpan(item, hm[1], 'bag-item-value');
      mkSpan(item, 'medicine', 'bag-item-label');
    } else if (km) {
      item.classList.add('key');
      mkSpan(item, km[1], 'bag-item-value');
    } else {
      item.textContent = part;
    }
    wrap.appendChild(item);
  });
  el.appendChild(wrap);
  /* Item list */
  if (items && items.length) {
    const inv = document.createElement('div');
    inv.className = 'bag-inv';
    const names = items.map(it => it.name + (it.qty > 1 ? '\u00d7' + it.qty : ''));
    inv.textContent = names.join(', ');
    el.appendChild(inv);
  }
}

/* ---- Party renderer ---- */
function renderParty(summary, mons) {
  const el = document.getElementById('party-list');
  el.textContent = '';

  /* Prefer structured data if available */
  if (mons && mons.length) {
    mons.forEach(function(m) {
      const row = document.createElement('div');
      row.className = 'mon';
      mkSpan(row, m.name, 'mon-name');
      mkSpan(row, 'Lv' + m.level, 'mon-level');
      const tc = document.createElement('span');
      tc.className = 'mon-types';
      (m.types || []).forEach(function(t) {
        mkSpan(tc, t, 'type-badge type-' + t.toLowerCase());
      });
      row.appendChild(tc);
      const sc = document.createElement('span'); sc.className = 'mon-status-col';
      if (m.status && m.status !== 'OK') sc.textContent = m.status;
      row.appendChild(sc);
      /* Stacked HP + EXP bars */
      const bars = document.createElement('div'); bars.className = 'bars-col';
      const pct = m.max_hp > 0 ? Math.round((m.hp / m.max_hp) * 100) : 0;
      const color = pct > 50 ? '#3fb950' : pct > 20 ? '#d29922' : '#f85149';
      const bo = document.createElement('div'); bo.className = 'hp-bar';
      const bf = document.createElement('div'); bf.className = 'hp-fill';
      bf.style.width = pct + '%'; bf.style.background = color;
      bo.appendChild(bf); bars.appendChild(bo);
      if (m.exp !== undefined && m.level < 100) {
        var curLvExp = Math.pow(m.level, 3);
        var nxtLvExp = Math.pow(m.level + 1, 3);
        var expPct = nxtLvExp > curLvExp ? Math.min(100, Math.round(((m.exp - curLvExp) / (nxtLvExp - curLvExp)) * 100)) : 0;
        if (expPct < 0) expPct = 0;
        const ebo = document.createElement('div'); ebo.className = 'exp-bar';
        const ebf = document.createElement('div'); ebf.className = 'exp-fill';
        ebf.style.width = expPct + '%';
        ebo.appendChild(ebf); bars.appendChild(ebo);
      }
      row.appendChild(bars);
      mkSpan(row, m.hp + '/' + m.max_hp, 'hp-text');
      el.appendChild(row);
    });
    return;
  }

  /* Fallback to summary string */
  if (!summary) { mkSpan(el, '-', 'empty'); return; }
  const clean = summary.replace(/\[HP:.*$/, '').trim();
  const parts = clean.split('|').map(s => s.trim()).filter(Boolean);
  parts.forEach(function(mon) {
    const pm = mon.match(/^(.+?)\s+Lv(\d+)\s+(\d+)\/(\d+)(.*)$/);
    const row = document.createElement('div');
    row.className = 'mon';
    if (!pm) { row.textContent = mon; el.appendChild(row); return; }
    const pct = Math.round((parseInt(pm[3]) / parseInt(pm[4])) * 100);
    const color = pct > 50 ? '#3fb950' : pct > 20 ? '#d29922' : '#f85149';
    mkSpan(row, pm[1], 'mon-name');
    mkSpan(row, 'Lv' + pm[2], 'mon-level');
    const tc = document.createElement('span'); tc.className = 'mon-types'; row.appendChild(tc);
    const bo = document.createElement('div'); bo.className = 'hp-bar';
    const bf = document.createElement('div'); bf.className = 'hp-fill';
    bf.style.width = pct + '%'; bf.style.background = color;
    bo.appendChild(bf); row.appendChild(bo);
    const sc2 = document.createElement('span'); sc2.className = 'mon-status-col';
    const st = pm[5].trim(); if (st) sc2.textContent = st;
    row.appendChild(sc2);
    mkSpan(row, pm[3] + '/' + pm[4], 'hp-text');
    el.appendChild(row);
  });
}

setInterval(pollState, STATE_MS);
pollState();

/* Fetch config once and populate badges */
(async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) return;
    const c = await res.json();
    const wrap = document.getElementById('cfg-badges');
    if (!wrap) return;
    const defs = [
      ['model',        c.model],
      ['max_tokens',   c.max_tokens],
      ['thinking',     c.thinking_budget],
      ['summary interval', c.summary_interval],
      ['summary model',    c.summary_model],
    ];
    defs.forEach(function([key, val]) {
      if (val === '' || val === null || val === undefined) return;
      const display = val;
      const b = document.createElement('div');
      b.className = 'cfg-badge';
      const k = document.createElement('span'); k.className = 'cfg-key'; k.textContent = key;
      const v = document.createElement('span'); v.className = 'cfg-val'; v.textContent = display;
      b.appendChild(k); b.appendChild(v);
      wrap.appendChild(b);
    });
    wrap.style.display = 'flex';
  } catch(e) {}
})();
</script>
</body>
</html>
"""
