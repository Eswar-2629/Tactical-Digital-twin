"""Local GIS digital twin demo server; standard-library only."""
from __future__ import annotations

import heapq
import json
import math
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
GRID_W, GRID_H = 30, 20


def build_scenario() -> dict:
    """Create deterministic fictional terrain and threat layers."""
    elevation, threat, blocked = [], [], []
    for y in range(GRID_H):
        erow, trow, brow = [], [], []
        for x in range(GRID_W):
            # Gentle hills plus a ridge; units are arbitrary meters in this demo.
            h = 95 + 9 * math.sin(x / 3.7) + 7 * math.cos(y / 3.1) + 11 * math.exp(-((x - 17) ** 2 + (y - 8) ** 2) / 38)
            erow.append(round(h, 1))
            # A fictional sensor/emission field with a few high-risk cells.
            value = 0.13 + 0.18 * ((math.sin((x + 2) / 4) + math.cos((y - 1) / 3)) / 2 + 0.5)
            value += 0.47 * math.exp(-((x - 19) ** 2 + (y - 10) ** 2) / 18)
            value += 0.32 * math.exp(-((x - 9) ** 2 + (y - 5) ** 2) / 12)
            trow.append(round(min(1, max(0, value)), 3))
            brow.append(1 if (x, y) in {(13, 8), (13, 9), (14, 9), (22, 14), (23, 14), (7, 12)} else 0)
        elevation.append(erow); threat.append(trow); blocked.append(brow)
    return {
        "id": "training-range-01", "name": "North Ridge Training Area", "crs": "LOCAL:GRID-10M",
        "width": GRID_W, "height": GRID_H, "cellSizeMeters": 10,
        "layers": {"elevation": elevation, "threat": threat, "blocked": blocked},
        "start": {"x": 2, "y": 16}, "goal": {"x": 27, "y": 3},
        "units": [{"id": "scout-01", "type": "scout", "x": 2, "y": 16, "status": "ready"}],
    }


def _validate_point(p, name: str) -> tuple[int, int]:
    if not isinstance(p, dict) or type(p.get("x")) is not int or type(p.get("y")) is not int:
        raise ValueError(f"{name} must include integer x and y coordinates")
    x, y = p["x"], p["y"]
    if not (0 <= x < GRID_W and 0 <= y < GRID_H):
        raise ValueError(f"{name} is outside the {GRID_W}×{GRID_H} map")
    return x, y


def find_route(payload: dict, scenario: dict | None = None) -> dict:
    """8-connected A* with terrain, slope, and threat exposure costs."""
    scenario = scenario or build_scenario()
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    start = _validate_point(payload.get("start"), "start")
    goal = _validate_point(payload.get("goal"), "goal")
    try:
        risk_weight = float(payload.get("riskWeight", 4.0))
        slope_weight = float(payload.get("slopeWeight", 0.22))
    except (TypeError, ValueError):
        raise ValueError("riskWeight and slopeWeight must be numbers") from None
    if not math.isfinite(risk_weight) or not 0 <= risk_weight <= 20:
        raise ValueError("riskWeight must be between 0 and 20")
    if not math.isfinite(slope_weight) or not 0 <= slope_weight <= 5:
        raise ValueError("slopeWeight must be between 0 and 5")
    elev = scenario["layers"]["elevation"]
    threats = scenario["layers"]["threat"]
    blocked = scenario["layers"]["blocked"]
    if blocked[start[1]][start[0]] or blocked[goal[1]][goal[0]]:
        raise ValueError("Start and destination must be traversable cells")
    if start == goal:
        return {"path": [list(start)], "distanceMeters": 0, "exposure": 0, "cost": 0, "expanded": 0}

    def heuristic(a):
        dx, dy = abs(goal[0]-a[0]), abs(goal[1]-a[1])
        return (max(dx, dy) + (math.sqrt(2)-1) * min(dx, dy)) * 10

    frontier = [(heuristic(start), 0.0, start)]
    costs = {start: 0.0}; previous = {}; expanded = 0
    while frontier:
        _, cost, current = heapq.heappop(frontier)
        if cost != costs.get(current):
            continue
        expanded += 1
        if current == goal:
            break
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            nx, ny = current[0]+dx, current[1]+dy
            if not (0 <= nx < GRID_W and 0 <= ny < GRID_H) or blocked[ny][nx]:
                continue
            step = 10 * (math.sqrt(2) if dx and dy else 1)
            slope = abs(elev[ny][nx] - elev[current[1]][current[0]]) / 10
            edge = step * (1 + risk_weight * threats[ny][nx] + slope_weight * slope)
            candidate = cost + edge
            nxt = (nx, ny)
            if candidate < costs.get(nxt, float("inf")):
                costs[nxt] = candidate; previous[nxt] = current
                heapq.heappush(frontier, (candidate + heuristic(nxt), candidate, nxt))
    if goal not in costs:
        raise ValueError("No traversable route connects the selected points")
    points = [goal]
    while points[-1] != start:
        points.append(previous[points[-1]])
    points.reverse()
    distance = sum(10 * (math.sqrt(2) if a[0] != b[0] and a[1] != b[1] else 1) for a,b in zip(points, points[1:]))
    exposure = sum(threats[y][x] for x,y in points) / len(points)
    return {"path": [list(p) for p in points], "distanceMeters": round(distance, 1), "exposure": round(exposure, 3), "cost": round(costs[goal], 1), "expanded": expanded}


class Handler(BaseHTTPRequestHandler):
    server_version = "TacticalTwin/1.0"

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status); self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body))); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers(); self.wfile.write(body)

    def _json(self, status: int, data: dict):
        self._send(status, json.dumps(data).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json(200, {"status": "ok"})
        if path == "/api/scenario":
            return self._json(200, build_scenario())
        if path == "/api/scenario/geojson":
            scenario = build_scenario()
            features = []
            for y in range(GRID_H):
                for x in range(GRID_W):
                    features.append({"type":"Feature","geometry":{"type":"Point","coordinates":[x*10,y*10]},"properties":{"elevation":scenario["layers"]["elevation"][y][x],"threat":scenario["layers"]["threat"][y][x],"blocked":bool(scenario["layers"]["blocked"][y][x])}})
            return self._json(200, {"type":"FeatureCollection","name":scenario["name"],"crs":{"type":"name","properties":{"name":scenario["crs"]}},"features":features})
        if path == "/" or path.startswith("/static/"):
            relative = "index.html" if path == "/" else path.removeprefix("/static/")
            target = (ROOT / "static" / relative).resolve()
            if not target.is_relative_to(ROOT / "static") or not target.is_file():
                return self._json(404, {"error":"Resource not found"})
            mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return self._send(200, target.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or "javascript" in mime else ""))
        return self._json(404, {"error":"Endpoint not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/route":
            return self._json(404, {"error":"Endpoint not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                return self._json(400, {"error":"Request body must be between 1 and 16384 bytes"})
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._json(400, {"error":"Request body must contain valid JSON"})
            return self._json(200, find_route(payload))
        except ValueError as exc:
            return self._json(400, {"error":str(exc)})

    def log_message(self, fmt, *args):
        if os.environ.get("APP_LOG", "1") != "0":
            super().log_message(fmt, *args)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Tactical Digital Twin running at http://{host}:{port}")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nShutting down")
    finally: server.server_close()


if __name__ == "__main__":
    main()
