# GENERATED - do not edit here.
# Copied from dreaming-v3-backend/src/dreaming/server/osc.py by
# sync_from_backend.sh so relay and backend emit identical OSC.

from __future__ import annotations

import queue
import re
import threading

ROOT = "/dreaming"
#: seconds after a dream ends until the wall has reset to the atlas and its
#: depth field has built, when /dreaming/sssh is sent; 0 = with `done`
SSSH_DELAY_S = 0.0
AFFECT_AXES = ("valence", "arousal", "dominance", "approach")


def affect_messages(axes: dict | None) -> list:
    """The four affect params: bundled in fixed order, and one address each
    so a patch can wire any of them without unpacking. The dream keeps its
    axes at -1..1 (0 = rest); the wire carries 0..1 (0.5 = rest), the range
    the patch's controls read."""
    axes = axes or {}
    values = [round(min(1.0, max(0.0, (float(axes.get(a, 0.0)) + 1.0) / 2.0)), 4)
              for a in AFFECT_AXES]
    return ([(f"{ROOT}/affect", values)]
            + [(f"{ROOT}/affect/{a}", [v]) for a, v in zip(AFFECT_AXES, values)])


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


PCT_METRICS = {
    "brightness": ("tonal_analysis", "mean_brightness"),
    "contrast": ("tonal_analysis", "contrast_value"),
    "dynamic_range": ("tonal_analysis", "dynamic_range"),
    "edge_density": ("composition_analysis", "edge_density"),
    "lines": ("composition_analysis", "total_lines_detected"),
    "colour_diversity": ("color_analysis", "color_diversity"),
    "faces": ("body_parts_detection", "faces_detected"),
    "figures": ("figure_detection", "figures_detected"),
}

#: Emitter profiles. "web" is the faithful OSCManager.js schema. "supercollider"
#: rewrites the addresses the venue patch (dreamingworkmidi_oscarpcontrol.scd)
#: reads on the wrong scale, so that what reaches the synth varies across the
#: collection: the patch stores values raw and at the point of use divides
#: brightness by 255, clips contrast / dynamic range / diversity / complexity
#: / edge density to 0..1, clips line counts at 96, compares the key with
#: "low-key" and looks for mood words in the tags. Measured against the raw
#: schema, five of those were constant for every work. See adapt_for_patch.
PROFILES = ("web", "supercollider")
#: the patch's own threshold for calling contrast "high" (its mock generator)
PATCH_CONTRAST_HIGH = 0.58
#: The profile's corrections, each switchable on its own from the admin:
#: key -> (addresses it rewrites, what it does). The profile applies the
#: enabled ones; all are enabled unless the admin says otherwise.
CORRECTIONS = {
    "brightness": (("/tonal/brightness",),
                   "percentile rank x 255 (the patch divides by 255)"),
    "contrast": (("/tonal/contrast",),
                 "percentile rank 0..1, \"high\" above 0.58"),
    "dynamic_range": (("/tonal/dynamic_range",),
                      "percentile rank 0..1 (raw 0..255 pinned at the patch's clip)"),
    "diversity": (("/colour/diversity",),
                  "percentile rank 0..1 (raw values sit in the bottom 4%)"),
    "complexity": (("/composition/complexity",),
                   "percentile rank of line count, a number (raw is a word the patch reads as 0)"),
    "edge_density": (("/composition/edge_density",),
                     "percentile rank 0..1 (raw values sit in the bottom 11%)"),
    "lines": (("/composition/lines",),
              "percentile rank x 96 in proportion (raw counts pin at the patch's 96)"),
    "key": (("/tonal/key",),
            "suffix dropped: low-key / mid-key / high-key (the patch compares for equality)"),
    "tags": (("/tags", "/tags/count"),
             "the patch's own mood, style and subject words (raw is the school name)"),
    "angles": (("/composition/dominant_angles",),
               "distinct directions 0..180 (the patch reads only the count)"),
}
ADAPTED_ADDRESSES = tuple(a for addrs, _ in CORRECTIONS.values() for a in addrs)


def corrections_enabled(setting) -> set[str]:
    """The enabled correction keys from a setting: None or missing means all;
    a dict key -> bool; a list/set of keys; a callable returning any of those."""
    if callable(setting):
        setting = setting()
    if setting is None:
        return set(CORRECTIONS)
    if isinstance(setting, dict):
        return {k for k in CORRECTIONS if setting.get(k, True)}
    return {k for k in CORRECTIONS if k in set(setting)}
#: words the patch listens for in /tags (its ~featuresForTags lists), by the
#: mood, style or subject that earns them
PATCH_MOOD_WORDS = {
    "melancholic": "melancholy", "joyful": "joy", "romantic": "romantic",
    "intimate": "intimate", "dramatic": "dramatic", "turbulent": "storm",
    "serene": "clear", "mysterious": "shadow", "ominous": "dark",
    "solemn": "sacred", "austere": "minimal", "majestic": "radiant",
    "contemplative": "floating", "playful": "bright",
}
PATCH_STYLE_WORDS = {"gothic": "gothic", "impressionist": "impressionist",
                     "post-impressionist": "impressionist",
                     "baroque": "baroque minor", "romantic": "romantic"}
_SUBJECT_WORDS = (
    (("saint", "virgin", "christ", "madonna", "angel", "adoration", "crucifixion",
      "annunciation", "holy", "altarpiece"), "sacred"),
    (("landscape", "river", "sea", "forest", "field", "hill", "garden", "coast",
      "harbour", "view of"), "landscape"),
    (("night", "nocturne", "moonlight", "evening"), "night"),
    (("storm", "battle", "shipwreck", "tempest"), "storm"),
    (("still life", "flowers", "fruit"), "simple"),
)


def _fold_angles(angles: list[float], within: float = 10.0, cap: int = 12) -> list[float]:
    """Signed angles (-90..90) folded to 0..180 and reduced to the distinct
    directions the lines run in, so the count the patch reads means
    something: one direction for a plain horizon, many for a crowd."""
    folded = sorted(((float(a) + 180.0) % 180.0) for a in angles)
    out: list[float] = []
    for a in folded:
        if not out or a - out[-1] > within:
            out.append(round(a, 1))
    return out[:cap]


def adapt_for_patch(messages: list, record: dict,
                    percentiles: "Percentiles | None",
                    sidecars: dict | None = None,
                    corrections=None) -> list:
    """Rewrite the web-schema messages into what the SuperCollider patch
    reads, applying the enabled CORRECTIONS (all, unless a set, dict or
    callable narrows them). Continuous values become the work's percentile
    rank across the collection, on the scale the patch divides or clips them
    by, so every parameter spans its range evenly; the key loses its suffix;
    the tags become words the patch listens for; the dominant angles become
    distinct directions. Everything else passes through unchanged."""
    if percentiles is None:
        return messages
    on = corrections_enabled(corrections)
    pct = percentiles.of(record)
    out = []
    tags_done = "tags" not in on
    for address, args in messages:
        suffix = address.removeprefix(ROOT)
        if suffix == "/tonal/brightness" and "brightness" in on and "brightness" in pct:
            args = [round(pct["brightness"] * 255.0, 2)]
        elif suffix == "/tonal/contrast" and "contrast" in on and "contrast" in pct:
            args = [round(pct["contrast"], 4),
                    "high" if pct["contrast"] > PATCH_CONTRAST_HIGH else "low"]
        elif suffix == "/tonal/dynamic_range" and "dynamic_range" in on and "dynamic_range" in pct:
            args = [round(pct["dynamic_range"], 4)]
        elif suffix == "/colour/diversity" and "diversity" in on and "colour_diversity" in pct:
            args = [round(pct["colour_diversity"], 4)]
        elif suffix == "/composition/complexity" and "complexity" in on and "lines" in pct:
            args = [round(pct["lines"], 4)]
        elif suffix == "/composition/edge_density" and "edge_density" in on and "edge_density" in pct:
            args = [round(pct["edge_density"], 4)]
        elif suffix == "/composition/lines" and "lines" in on and "lines" in pct and len(args) == 4:
            total = max(1, round(pct["lines"] * 96))
            raw_total = max(1, float(args[0]))
            parts = [round(total * float(a) / raw_total) for a in args[1:]]
            args = [total, *parts]
        elif suffix == "/composition/dominant_angles" and "angles" in on:
            args = _fold_angles(args)
        elif suffix == "/tonal/key" and "key" in on and args:
            args = [str(args[0]).split(" (")[0]]
        elif suffix in ("/tags", "/tags/count") and "tags" in on:
            if tags_done:
                continue
            tags = patch_tags(record, sidecars or {})
            out.append((f"{ROOT}/tags/count", [len(tags)]))
            if tags:
                out.append((f"{ROOT}/tags", tags))
            tags_done = True
            continue
        out.append((address, args))
    if not tags_done:
        # the web schema sends no tags when the record has no concepts; the
        # patch then invents some, so always send ours
        tags = patch_tags(record, sidecars or {})
        out.append((f"{ROOT}/tags/count", [len(tags)]))
        if tags:
            out.append((f"{ROOT}/tags", tags))
    return out


def patch_tags(record: dict, sidecars: dict) -> list[str]:
    """Tags in the patch's own vocabulary: its mood words for the work's
    moods, a style word, and a subject word read off the title."""
    tags: list[str] = []
    mood = (sidecars or {}).get("mood") or {}
    moods = [m.get("mood") for m in (mood.get("moods") or [])[:3]
             if isinstance(m, dict)]
    if mood.get("primary") and mood["primary"] not in moods:
        moods.insert(0, mood["primary"])
    for m in moods:
        word = PATCH_MOOD_WORDS.get(str(m).lower())
        if word and word not in tags:
            tags.append(word)
    style = str(record.get("style") or "").lower()
    for key, word in PATCH_STYLE_WORDS.items():
        if key in style and word not in tags:
            tags.append(word)
            break
    title = str(record.get("full_title") or record.get("short_title") or "").lower()
    for needles, word in _SUBJECT_WORDS:
        if any(n in title for n in needles) and word not in tags:
            tags.append(word)
            break
    faces = ((record.get("body_parts_detection") or {}).get("faces_detected") or 0)
    if faces and "portrait" not in tags and "sacred" not in tags:
        tags.append("portrait")
    return tags[:6]


class Percentiles:
    """Where a work's metrics sit in the whole collection, 0..1.

    Audited across 2,260 works: the raw metrics are unevenly spread (dynamic
    range is 220 +- 23 for nearly every work, contrast_level is "low" or
    "medium" for all but three), so a patch mapping raw values gets a
    near-constant signal. Percentile rank makes every metric span the full
    range evenly across the collection."""

    def __init__(self, records: dict):
        import bisect

        self._bisect = bisect
        self.sorted: dict[str, list[float]] = {}
        for name, (block, key) in PCT_METRICS.items():
            values = []
            for record in records.values():
                v = _f((record.get(block) or {}).get(key))
                if v is not None:
                    values.append(v)
            self.sorted[name] = sorted(values)

    def rank(self, name: str, value) -> float | None:
        v = _f(value)
        values = self.sorted.get(name)
        if v is None or not values:
            return None
        lo = self._bisect.bisect_left(values, v)
        hi = self._bisect.bisect_right(values, v)
        return round(((lo + hi) / 2) / len(values), 4)

    def of(self, record: dict) -> dict[str, float]:
        out = {}
        for name, (block, key) in PCT_METRICS.items():
            r = self.rank(name, (record.get(block) or {}).get(key))
            if r is not None:
                out[name] = r
        return out


def _school(record: dict) -> str | None:
    raw = record.get("associated_concepts")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return str(raw).split(" (")[0].strip() if raw else None


def _year(date) -> int | None:
    m = re.search(r"1[0-9]{3}", str(date or ""))
    return int(m.group(0)) if m else None


def build_work_messages(record: dict, pid: str,
                        history: dict | None = None,
                        percentiles: "Percentiles | None" = None,
                        sidecars: dict | None = None) -> list:
    """[(address, args), ...] for one work - the OSCManager.js schema, plus
    the dream's additions (style, school, year, percentiles, mood, faces)."""
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
    _push(messages, "/work/style", [_s(record.get("style"))])
    _push(messages, "/work/school", [_s(_school(record))])
    _push(messages, "/work/year", [_i(_year(date))])
    if percentiles is not None:
        for name, rank in percentiles.of(record).items():
            _push(messages, f"/pct/{name}", [_f(rank)])
    sidecars = sidecars or {}
    mood = sidecars.get("mood") or {}
    if mood:
        _push(messages, "/mood/primary", [_s(mood.get("primary"))])
        top: list = []
        for m in (mood.get("moods") or [])[:3]:
            if isinstance(m, dict) and m.get("mood"):
                top += [_s(m["mood"]), _f(m.get("score"))]
        _push(messages, "/mood/top", top)
    faces = sidecars.get("face") or {}
    if faces:
        labels = [_s(f.get("expression")) for f in (faces.get("faces") or [])
                  if f.get("expression")]
        _push(messages, "/faces/expressions", labels)

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
    if event == "attention":
        # the hop itself, for sound and light: what the walked edge was
        # made of, and how far the dream leapt
        extras = []
        dims = data.get("dimensions") or {}
        if dims:
            extras.append((f"{ROOT}/hop/dimensions", [
                float(dims.get(k, 0.0)) for k in
                ("colour", "composition", "pose", "objects", "clip")]))
        leap = data.get("leap") or {}
        if leap and leap.get("years") is not None:
            extras.append((f"{ROOT}/hop/leap", [
                int(leap["years"]), 1 if leap.get("changed_style") else 0,
                str(leap.get("style_from") or ""),
                str(leap.get("style_to") or "")]))
        if data.get("link"):
            extras.append((f"{ROOT}/link", [str(data["link"])]))
        return extras
    if event == "affect":
        return affect_messages(data.get("axes"))
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

    def __init__(self, records: dict, destinations: list[str],
                 profile="web", corrections=None, sssh_delay=SSSH_DELAY_S):
        self.records = records
        #: seconds from a dream's end to /dreaming/sssh; a callable is read
        #: each time so the admin's setting applies live
        self.sssh_delay = sssh_delay
        self._affect: dict | None = None  # the last feeling, for focus bursts
        self._sssh_timer: threading.Timer | None = None
        self.percentiles = Percentiles(records)
        #: "web" or "supercollider", or a callable returning one - read per
        #: focus change, so the admin's toggle takes effect on the next work
        self.profile = profile
        #: which of the profile's CORRECTIONS apply (see corrections_enabled);
        #: a callable is read per focus change too
        self.corrections = corrections
        self.clients = []
        if destinations:  # none = a dry run, for tracing what would be sent
            from pythonosc.udp_client import SimpleUDPClient

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

    def _focus(self, pid: str, title: str | None, via: str | None) -> list:
        history = None
        if self._current is not None:
            history = {"previousId": self._current[0],
                       "previousTitle": self._current[1],
                       "visitedCount": self._position,
                       "position": self._position, "via": via}
        self._position += 1
        record = self.records.get(pid, {})
        self._current = (pid, title or record.get("full_title") or "")
        sidecars = self._sidecars(pid)
        messages = build_work_messages(record, pid, history,
                                       self.percentiles, sidecars)
        if self.current_profile() == "supercollider":
            messages = adapt_for_patch(messages, record, self.percentiles,
                                       sidecars, self.corrections)
        return messages

    def current_profile(self) -> str:
        profile = self.profile() if callable(self.profile) else self.profile
        return profile if profile in PROFILES else "web"

    def _sidecars(self, pid: str) -> dict:
        """The mood and face readings OSC did not carry before: two small
        JSON reads per focus change, or nothing if unavailable."""
        out: dict = {}
        try:
            from dreaming import store
            from dreaming.config import load_config

            root = load_config().analysis_data_dir
        except Exception:  # noqa: BLE001
            return out
        for key, name, version in (("mood", "mood", "clip-zeroshot-v1"),
                                   ("face", "face", "yunet-clipexpr-v1")):
            try:
                out[key] = store.read(root, pid, name, version).result
            except Exception:  # noqa: BLE001 - an enrichment, never a fault
                pass
        return out

    def current_sssh_delay(self) -> float:
        delay = self.sssh_delay() if callable(self.sssh_delay) else self.sssh_delay
        try:
            return max(0.0, min(float(delay), 30.0))
        except (TypeError, ValueError):
            return SSSH_DELAY_S

    def messages(self, event: str, data: dict) -> list:
        """What this event sends at once, as (address, args) pairs, advancing
        the journey history as sending would - so a dry run over a recording
        reproduces the live stream message for message. Focus changes carry
        the work, the four affect params as last felt, and a "focus" stab;
        a crossing line a "pair" stab; the end a "rest" stab."""
        if event in ("seed", "attention"):
            pid = data.get("pid", "")
            messages = self._focus(pid, data.get("title"), data.get("via"))
            if event == "attention":
                messages = messages + event_messages(event, data)
            if self._affect is not None:
                messages = messages + affect_messages(self._affect)
            return messages + [(f"{ROOT}/stab", ["focus", str(pid)])]
        if event == "affect":
            self._affect = data.get("axes") or {}
        messages = event_messages(event, data)
        if event == "voice" and data.get("transition") and data.get("text"):
            messages = messages + [(f"{ROOT}/stab", ["pair", str(data.get("pid", ""))])]
        if event == "done":
            messages = messages + [(f"{ROOT}/stab", ["rest", ""])]
        return messages

    def delayed(self, event: str, data: dict) -> list:
        """What this event sends later: [(seconds, [(address, args)])]. The
        end of a dream sends /dreaming/sssh once the wall has reset to the
        atlas; a new dream beginning first cancels one still pending."""
        if event == "done":
            return [(self.current_sssh_delay(), [(f"{ROOT}/sssh", ["atlas"])])]
        return []

    def handle(self, event: str, data: dict) -> None:
        try:
            self._emit(self.messages(event, data))
            later = self.delayed(event, data)
            if event == "seed" and self._sssh_timer is not None:
                self._sssh_timer.cancel()  # the next dream began first
                self._sssh_timer = None
            for delay, messages in later:
                timer = threading.Timer(delay, self._emit, args=(messages,))
                timer.daemon = True
                timer.start()
                self._sssh_timer = timer
        except Exception as exc:  # noqa: BLE001 - one odd record must not
            print(f"osc: skipped {event}: {exc}", flush=True)  # kill the bus


def osc_timeline(records: dict, events: list[dict], profile: str = "web",
                 corrections=None, sssh_delay=SSSH_DELAY_S) -> list[dict]:
    """What a recorded dream sent (or would send) to OSC, point by point:
    [{t, event, messages: [[address, args], ...]}] for every event that
    produces a message, under the given profile and corrections, with the
    delayed `sssh` placed where it fires after the end (or dropped where the
    next dream began first). A fresh translator walks the events in order,
    so the journey history matches a live run; nothing is emitted."""
    translator = OscTranslator(records, [], profile=profile,
                               corrections=corrections, sssh_delay=sssh_delay)
    out, pending = [], None
    for entry in events:
        t, event, data = entry.get("t", 0.0), entry.get("event"), entry.get("data") or {}
        try:
            messages = translator.messages(event, data)
            later = translator.delayed(event, data)
        except Exception as exc:  # noqa: BLE001 - shown, not fatal
            messages, later = [(f"{ROOT}/error", [f"{type(exc).__name__}: {exc}"])], []
        if event == "seed":
            if pending is not None and pending["t"] > t:
                pending = None  # cancelled: the next dream began first
            elif pending is not None:
                out.append(pending); pending = None
        if messages:
            out.append({"t": t, "event": event,
                        "messages": [[address, list(args)]
                                     for address, args in messages]})
        for delay, delayed_messages in later:
            pending = {"t": round(t + delay, 3), "event": "sssh",
                       "messages": [[address, list(args)]
                                    for address, args in delayed_messages]}
    if pending is not None:
        out.append(pending)
    out.sort(key=lambda p: p["t"])
    return out


