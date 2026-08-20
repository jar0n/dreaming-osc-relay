# GENERATED - do not edit here.
# Copied from dreaming-v3-backend/src/dreaming/server/osc.py by
# sync_from_backend.sh so relay and backend emit identical OSC.

from __future__ import annotations

import queue
import re
import threading

ROOT = "/dreaming"


def _s(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _f(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _i(value) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number)


def _num_or_str(value):
    """complexity / temperature may be numeric or a label ("high")."""
    return _f(value) if _f(value) is not None else _s(value)


def _push(messages: list, suffix: str, args: list) -> None:
    clean = [a for a in args if a is not None]
    if clean:
        messages.append((f"{ROOT}{suffix}", clean))


def _tags(record: dict) -> list[str]:
    raw = record.get("associated_concepts")
    if not raw:
        return []
    if isinstance(raw, list):
        items = [(t.get("name") or t.get("label")) if isinstance(t, dict)
                 else t for t in raw]
        return [str(t).strip() for t in items if t]
    return [t.strip() for t in re.split(r"[,;|]", str(raw)) if t.strip()]


def build_work_messages(record: dict, pid: str,
                        history: dict | None = None) -> list:
    """[(address, args), ...] for one work - the OSCManager.js schema."""
    messages: list = []

    creator = record.get("creator") or {}
    artist = creator.get("name") if isinstance(creator, dict) else creator
    title = record.get("full_title") or record.get("short_title")
    identifier = record.get("identifier") or pid
    date = (record.get("production_date") or record.get("date")
            or record.get("year"))

    _push(messages, "/work", [_s(identifier), _s(title)])
    _push(messages, "/work/id", [_s(identifier)])
    _push(messages, "/work/title", [_s(title)])
    _push(messages, "/work/artist", [_s(artist)])
    _push(messages, "/work/date", [_s(date)])
    _push(messages, "/work/material", [_s(record.get("material"))])
    _push(messages, "/work/location", [_s(record.get("current_location"))])

    c = record.get("composition_analysis") or {}
    if c:
        _push(messages, "/composition/complexity",
              [_num_or_str(c.get("complexity"))])
        _push(messages, "/composition/edge_density",
              [_f(c.get("edge_density"))])
        _push(messages, "/composition/lines", [
            _i(c.get("total_lines_detected")), _i(c.get("horizontal_lines")),
            _i(c.get("vertical_lines")), _i(c.get("diagonal_lines"))])
        if c.get("dominant_angles"):
            _push(messages, "/composition/dominant_angles",
                  [_f(a) for a in c["dominant_angles"]])

    c = record.get("color_analysis") or {}
    if c:
        rgb = c.get("mean_color_rgb")
        if isinstance(rgb, list) and len(rgb) == 3:
            _push(messages, "/colour/mean_rgb", [_f(v) for v in rgb])
        _push(messages, "/colour/mean_hex", [_s(c.get("mean_color_hex"))])
        if c.get("color_palette"):
            _push(messages, "/colour/palette",
                  [_s(h) for h in c["color_palette"]])
        _push(messages, "/colour/temperature",
              [_num_or_str(c.get("color_temperature"))])
        _push(messages, "/colour/diversity", [_f(c.get("color_diversity"))])
        _push(messages, "/colour/unique", [_i(c.get("unique_colors"))])

    t = record.get("tonal_analysis") or {}
    if t:
        _push(messages, "/tonal/brightness", [_f(t.get("mean_brightness"))])
        _push(messages, "/tonal/contrast",
              [_f(t.get("contrast_value")), _s(t.get("contrast_level"))])
        _push(messages, "/tonal/key", [_s(t.get("tonal_key"))])
        _push(messages, "/tonal/dynamic_range", [_f(t.get("dynamic_range"))])
        _push(messages, "/tonal/distribution", [
            _f(t.get("shadows_percentage")), _f(t.get("midtones_percentage")),
            _f(t.get("highlights_percentage"))])

    o = record.get("object_detection") or {}
    if o:
        _push(messages, "/objects/count", [_i(o.get("object_count"))])
        classes = o.get("unique_classes")
        if not isinstance(classes, list):  # some records store a count here
            classes = list(o.get("object_types") or {})
        if classes:
            _push(messages, "/objects/classes", [_s(cls) for cls in classes])
        for index, obj in enumerate(o.get("detected_objects") or []):
            bbox = obj.get("bbox") or []
            _push(messages, "/objects/item", [
                _i(index), _s(obj.get("class")), _f(obj.get("confidence")),
                *(_f(b) for b in bbox[:4])])

    b = record.get("body_parts_detection") or {}
    if b:
        _push(messages, "/faces/count", [_i(b.get("faces_detected"))])
        for index, face in enumerate(b.get("faces") or []):
            bbox = face.get("bbox") or []
            _push(messages, "/faces/item", [
                _i(index), _f(face.get("confidence")),
                *(_f(v) for v in bbox[:4])])
        _push(messages, "/hands/count", [_i(b.get("hands_detected"))])

    f = record.get("figure_detection") or {}
    if f:
        _push(messages, "/figures/count", [_i(f.get("figures_detected"))])
        for index, keypoints in enumerate(f.get("pose_keypoints") or []):
            visible = sum(
                1 for k in (keypoints if isinstance(keypoints, list) else [])
                if (k.get("visibility") or k.get("confidence") or 0) > 0.5)
            _push(messages, "/figures/item", [_i(index), _i(visible)])

    tags = _tags(record)
    if tags:
        _push(messages, "/tags/count", [_i(len(tags))])
        _push(messages, "/tags", [_s(t) for t in tags])

    if history:
        _push(messages, "/history/previous",
              [_s(history.get("previousId")), _s(history.get("previousTitle"))])
        _push(messages, "/history/visited_count",
              [_i(history.get("visitedCount"))])
        _push(messages, "/history/position", [_i(history.get("position"))])
        _push(messages, "/history/via", [_s(history.get("via"))])

    return messages


def event_messages(event: str, data: dict) -> list:
    """Dream-event extras under the same root (not in the web schema)."""
    if event == "voice":
        return [(f"{ROOT}/voice", [data.get("who", ""), data.get("pid", ""),
                                   data.get("text", "")])]
    if event == "pool":
        args: list = []
        for word, weight in data.get("terms", []):
            args += [word, float(weight)]
        return [(f"{ROOT}/pool", args)] if args else []
    if event == "visitor":
        return [(f"{ROOT}/visitor", [line]) for line in data.get("lines", [])]
    if event == "absorbed":
        return [(f"{ROOT}/absorbed",
                 [len(data.get("surfaced", [])),
                  int(data.get("heard_words", 0)),
                  " ".join(data.get("surfaced", []))])]
    if event == "done":
        return [(f"{ROOT}/done", [int(data.get("visited", 0))])]
    return []


class OscTranslator:
    """Dream events -> the frontend's OSC schema, over UDP.

    Shared by the in-process hub forwarder and the venue relay
    (dreaming osc-relay), so both emit byte-identical messages. Focus
    changes (seed / attention) emit full per-work metadata with journey
    history; other events emit the /dreaming extras.
    """

    def __init__(self, records: dict, destinations: list[str]):
        from pythonosc.udp_client import SimpleUDPClient

        self.records = records
        self.clients = []
        for destination in destinations:
            host, _, port = destination.rpartition(":")
            self.clients.append(SimpleUDPClient(host or "127.0.0.1",
                                                int(port)))
        self._current: tuple[str, str] | None = None  # (pid, title)
        self._position = 0

    def _emit(self, messages: list) -> None:
        for address, args in messages:
            for client in self.clients:
                try:
                    client.send_message(address, args)
                except OSError:
                    pass  # UDP best-effort: the dream does not care

    def _focus(self, pid: str, title: str | None, via: str | None) -> None:
        history = None
        if self._current is not None:
            history = {"previousId": self._current[0],
                       "previousTitle": self._current[1],
                       "visitedCount": self._position,
                       "position": self._position, "via": via}
        self._position += 1
        record = self.records.get(pid, {})
        self._current = (pid, title or record.get("full_title") or "")
        self._emit(build_work_messages(record, pid, history))

    def handle(self, event: str, data: dict) -> None:
        try:
            if event in ("seed", "attention"):
                self._focus(data.get("pid", ""), data.get("title"),
                            data.get("via"))
            else:
                self._emit(event_messages(event, data))
        except Exception as exc:  # noqa: BLE001 - one odd record must not
            print(f"osc: skipped {event}: {exc}", flush=True)  # kill the bus


