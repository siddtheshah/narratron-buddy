"""Pure helpers shared by canvas state components and the manager."""



def doodle_snapshot_batches(doodles: list[dict[str, object]]) -> list[dict[str, object]]:
    """Compact adjacent line segments with the same style into stroke paths."""
    batches: list[dict[str, object]] = []
    for action in doodles:
        if action.get("type") != "draw":
            continue
        try:
            x0, y0, x1, y1 = action["x0"], action["y0"], action["x1"], action["y1"]
        except KeyError:
            continue
        color, size = action.get("color"), action.get("size", 3)
        if batches and batches[-1]["color"] == color and batches[-1]["size"] == size and batches[-1]["points"][-2:] == [x0, y0]:
            batches[-1]["points"].extend([x1, y1])
        else:
            batches.append({"color": color, "size": size, "points": [x0, y0, x1, y1]})
    return batches


def clamp_surface_placement(left_pct: float, top_pct: float) -> dict[str, float]:
    return {"left_pct": round(max(2.0, min(98.0, float(left_pct))), 2), "top_pct": round(max(2.0, min(98.0, float(top_pct))), 2)}
