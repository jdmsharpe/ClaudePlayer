"""A* pathfinding on a 2D metatile grid.

Operates on the 10x9 character grid produced by spatial_context.py.
Each cell is one map tile (16 frames of directional input).

Grid coordinate convention: (x, y) where x = column, y = row.
Grid indexing: grid[y][x].
"""

import heapq
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# Characters treated as impassable by default.
# The goal tile is ALWAYS passable regardless of this set.
DEFAULT_BLOCKED: FrozenSet[str] = frozenset(
    {'#', 'W', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'n', 'i', '=', 'T', 'B'}
)

# Ledge tiles are one-way: passable only when entering from the correct direction.
# Maps ledge char → the (dx, dy) movement that is ALLOWED to enter it.
LEDGE_ALLOWED_DIR: Dict[str, Tuple[int, int]] = {
    'v': (0, 1),    # south ledge: can enter moving DOWN (from north)
    '>': (1, 0),    # east ledge: can enter moving RIGHT (from west)
    '<': (-1, 0),   # west ledge: can enter moving LEFT (from east)
}

# Direction deltas → button letters (shared with world_map.py A*)
DIR_BUTTONS: Dict[Tuple[int, int], str] = {
    (0, -1): 'U',
    (0, 1): 'D',
    (-1, 0): 'L',
    (1, 0): 'R',
}

# 4-connected neighbors (no diagonal movement in Pokemon)
NEIGHBORS = ((0, -1), (0, 1), (-1, 0), (1, 0))


def find_path(
    grid: List[List[str]],
    start: Tuple[int, int],
    goal: Tuple[int, int],
    blocked_chars: FrozenSet[str] = DEFAULT_BLOCKED,
    extra_passable: Optional[Set[Tuple[int, int]]] = None,
) -> Optional[List[Tuple[int, int]]]:
    """A* pathfinding from start to goal on a character grid.

    Args:
        grid: grid[y][x] character grid (e.g. 10 wide x 9 tall).
        start: (x, y) player position.
        goal: (x, y) target position. Always treated as passable.
        blocked_chars: Characters that are impassable (except at goal).
        extra_passable: Positions treated as passable regardless of tile
            (e.g. a warp tile we need to walk through to reach an overshoot).

    Returns:
        List of (x, y) from start to goal inclusive, or None.
    """
    height = len(grid)
    width = len(grid[0]) if grid else 0
    sx, sy = start
    gx, gy = goal
    _extra = extra_passable or set()

    if not (0 <= sx < width and 0 <= sy < height):
        return None
    if not (0 <= gx < width and 0 <= gy < height):
        return None
    if start == goal:
        return [start]

    def _passable(x: int, y: int, dx: int = 0, dy: int = 0) -> bool:
        if (x, y) == goal or (x, y) in _extra:
            return True
        cell = grid[y][x]
        if cell in LEDGE_ALLOWED_DIR:
            return (dx, dy) == LEDGE_ALLOWED_DIR[cell]
        return cell not in blocked_chars

    if not _passable(sx, sy):
        return None

    def _h(x: int, y: int) -> int:
        return abs(gx - x) + abs(gy - y)

    # (f_score, tiebreaker, (x, y))
    counter = 0
    open_heap: list = [(0 + _h(sx, sy), counter, start)]
    counter += 1
    g_score: Dict[Tuple[int, int], int] = {start: 0}
    came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
    closed: Set[Tuple[int, int]] = set()

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path
        closed.add(current)
        cx, cy = current
        g_cur = g_score[current]

        for dx, dy in NEIGHBORS:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) in closed:
                continue
            if not _passable(nx, ny, dx, dy):
                continue
            tentative_g = g_cur + 1
            if tentative_g < g_score.get((nx, ny), float('inf')):
                g_score[(nx, ny)] = tentative_g
                came_from[(nx, ny)] = current
                heapq.heappush(open_heap, (tentative_g + _h(nx, ny), counter, (nx, ny)))
                counter += 1

    return None


def find_path_to_edge(
    grid: List[List[str]],
    start: Tuple[int, int],
    edge: str,
    blocked_chars: FrozenSet[str] = DEFAULT_BLOCKED,
) -> Optional[List[Tuple[int, int]]]:
    """Find shortest A* path to any reachable cell on a grid edge.

    Uses multi-goal A*: the goal test checks membership in a set of
    all edge cells, finding the nearest one in a single expansion.

    Args:
        grid: grid[y][x] character grid.
        start: (x, y) player position.
        edge: "NORTH", "SOUTH", "EAST", or "WEST".
        blocked_chars: Characters that are impassable (except at goal).

    Returns:
        Path to the nearest reachable edge cell, or None.
    """
    height = len(grid)
    width = len(grid[0]) if grid else 0
    sx, sy = start

    if not (0 <= sx < width and 0 <= sy < height):
        return None

    # Collect candidate cells on the target edge
    if edge == "NORTH":
        goals = {(x, 0) for x in range(width)}
    elif edge == "SOUTH":
        goals = {(x, height - 1) for x in range(width)}
    elif edge == "WEST":
        goals = {(0, y) for y in range(height)}
    elif edge == "EAST":
        goals = {(width - 1, y) for y in range(height)}
    else:
        return None

    if not goals:
        return None

    def _passable(x: int, y: int, dx: int = 0, dy: int = 0) -> bool:
        if (x, y) in goals:
            return True
        cell = grid[y][x]
        if cell in LEDGE_ALLOWED_DIR:
            return (dx, dy) == LEDGE_ALLOWED_DIR[cell]
        return cell not in blocked_chars

    if not _passable(sx, sy):
        return None

    # Heuristic: minimum Manhattan distance to any goal cell.
    # For edge goals this simplifies to distance to the edge line.
    if edge == "NORTH":
        def _h(x: int, y: int) -> int:
            return y
    elif edge == "SOUTH":
        def _h(x: int, y: int) -> int:
            return (height - 1) - y
    elif edge == "WEST":
        def _h(x: int, y: int) -> int:
            return x
    elif edge == "EAST":
        def _h(x: int, y: int) -> int:
            return (width - 1) - x

    counter = 0
    open_heap: list = [(_h(sx, sy), counter, start)]
    counter += 1
    g_score: Dict[Tuple[int, int], int] = {start: 0}
    came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
    closed: Set[Tuple[int, int]] = set()

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current in goals:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path
        closed.add(current)
        cx, cy = current
        g_cur = g_score[current]

        for dx, dy in NEIGHBORS:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) in closed:
                continue
            if not _passable(nx, ny, dx, dy):
                continue
            tentative_g = g_cur + 1
            if tentative_g < g_score.get((nx, ny), float('inf')):
                g_score[(nx, ny)] = tentative_g
                came_from[(nx, ny)] = current
                heapq.heappush(open_heap, (tentative_g + _h(nx, ny), counter, (nx, ny)))
                counter += 1

    return None


def path_to_buttons(path: List[Tuple[int, int]], frames_per_tile: int = 16) -> str:
    """Convert a sequence of (x, y) positions into a button input string.

    Merges consecutive same-direction steps:
    [(4,4), (4,3), (4,2), (5,2)] → "U32 R16"

    Args:
        path: List of (x, y) grid positions from start to goal.
        frames_per_tile: Frames per tile of movement (16 for Pokemon).

    Returns:
        Button string like "U32 R16 D16", or "" if path has < 2 points.
    """
    if not path or len(path) < 2:
        return ""

    commands: List[str] = []
    current_dir: Optional[str] = None
    current_count = 0

    for i in range(1, len(path)):
        px, py = path[i - 1]
        nx, ny = path[i]
        dx = nx - px
        dy = ny - py
        direction = DIR_BUTTONS.get((dx, dy))
        if direction is None:
            continue

        if direction == current_dir:
            current_count += 1
        else:
            if current_dir is not None:
                commands.append(f"{current_dir}{current_count * frames_per_tile}")
            current_dir = direction
            current_count = 1

    if current_dir is not None:
        commands.append(f"{current_dir}{current_count * frames_per_tile}")

    return " ".join(commands)
