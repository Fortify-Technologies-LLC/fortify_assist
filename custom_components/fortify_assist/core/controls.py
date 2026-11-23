"""Rich device control: colours, white tones, levels, relative changes, modes.

Turns "make it blue", "a bit brighter", "set the fan to high", "open the blinds halfway" into
concrete service calls for the device that was resolved, using only what that device reports
it supports. Pure functions: no entity lookup here, the resolver decides which devices.
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import Action, Entity

COLORS = {"red", "orange", "yellow", "green", "blue", "purple", "pink", "cyan", "magenta", "violet", "teal",
          "lime", "gold", "aqua", "turquoise", "lavender", "coral", "indigo", "white"}
WHITES = {"warm white": 2700, "soft white": 2700, "neutral white": 4000, "natural white": 4000,
          "cool white": 5000, "cold white": 5500, "daylight": 6500, "candlelight": 2000}
HVAC_MODES = {"heat", "cool", "auto", "dry", "fan only", "heat cool"}
# level words by device kind: percent of brightness, fan speed, cover opening, speaker volume
LEVELS = {
    "light": {"max": 100, "maximum": 100, "full": 100, "high": 80, "medium": 50, "half": 50, "low": 20,
              "lowest": 1, "min": 1, "minimum": 1},
    "fan": {"max": 100, "maximum": 100, "full": 100, "high": 100, "medium": 66, "half": 50, "low": 33,
            "lowest": 20, "min": 20, "minimum": 20},
    "cover": {"max": 100, "maximum": 100, "full": 100, "fully": 100, "halfway": 50, "half": 50},
    "media_player": {"max": 100, "maximum": 100, "full": 100, "high": 80, "medium": 50, "half": 50, "low": 20,
                     "lowest": 5, "min": 5, "minimum": 5},
}
LEVEL_WORDS = {w for table in LEVELS.values() for w in table}
OPTION_WORDS = COLORS | set(WHITES) | HVAC_MODES | LEVEL_WORDS
ADJUST_WORDS = {"brighter", "dimmer", "warmer", "cooler", "hotter", "colder", "louder", "quieter", "faster",
                "slower", "up", "down"}
DEFAULT_STEPS = {"light": 20, "media": 10, "temp": 2, "kelvin": 600, "fan": 20}
COLOR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}
KELVIN_RANGE = (2000, 6500)


# Apps people ask a TV for, by the name they say: what to show, and the Android TV / Google TV package.
APPS = {"plex": ("Plex", "com.plexapp.android"), "netflix": ("Netflix", "com.netflix.ninja"),
        "youtube": ("YouTube", "com.google.android.youtube.tv"),
        "youtube tv": ("YouTube TV", "com.google.android.youtube.tvunplugged"),
        "hulu": ("Hulu", "com.hulu.livingroomplus"), "disney plus": ("Disney+", "com.disney.disneyplus"),
        "prime video": ("Prime Video", "com.amazon.amazonvideo.livingroom"),
        "spotify": ("Spotify", "com.spotify.tv.android"), "max": ("Max", "com.wbd.stream"),
        "peacock": ("Peacock", "com.peacocktv.peacockandroid"), "paramount plus": ("Paramount+", "com.cbs.ott"),
        "apple tv": ("Apple TV", "com.apple.atve.androidtv.appletv"), "jellyfin": ("Jellyfin", "org.jellyfin.androidtv"),
        "kodi": ("Kodi", "org.xbmc.kodi"), "twitch": ("Twitch", "tv.twitch.android.app"),
        "pluto tv": ("Pluto TV", "tv.pluto.android"), "tubi": ("Tubi", "com.tubitv"),
        "crunchyroll": ("Crunchyroll", "com.crunchyroll.crunchyroid")}
APP_ALIASES = {"you tube": "youtube", "you tube tv": "youtube tv", "disney": "disney plus", "amazon prime": "prime video",
               "prime": "prime video", "amazon": "prime video", "hbo": "max", "hbo max": "max",
               "paramount": "paramount plus", "pluto": "pluto tv"}
APP_NAMES = {key: shown for shown, package in APPS.values() for key in (package, shown)}


@dataclass
class Result:
    actions: list[Action]
    problem: str = ""  # the capability that is missing, e.g. "color"


def _modes(e: Entity) -> set[str]:
    return set(e.attributes.get("supported_color_modes") or [])


def launch(e: Entity, app: str) -> Result:
    """Open an app on a TV, the way that TV's integration takes it: from its source list when the app is
    there (Roku, webOS, Fire TV...), by package on Android TV and Google TV, by title on a TV that lists
    only its inputs as sources (Bravia)."""
    if e.domain != "media_player" or app not in APPS:
        return Result([], "apps")
    shown, package = APPS[app]
    wanted = {app, shown.lower(), shown.lower().replace("+", " plus")}
    sources = [s for s in e.attributes.get("source_list") or [] if isinstance(s, str)]
    for source in sources:
        if source.strip().lower() in wanted:
            return Result([Action.make("media_player.select_source", e.id, {"source": source})])
    by_title = bool(sources) and "app_id" not in e.attributes
    return Result([Action.make("media_player.play_media", e.id, {
        "media_content_type": "app", "media_content_id": shown if by_title else package})])


VACUUM_CLEAN_AREA = 16384  # VacuumEntityFeature.CLEAN_AREA: the robot can be sent to chosen rooms


def can_clean_area(e: Entity) -> bool:
    try:
        return e.domain == "vacuum" and bool(int(e.attributes.get("supported_features") or 0) & VACUUM_CLEAN_AREA)
    except (TypeError, ValueError):
        return False


def can_color(e: Entity) -> bool:
    modes = _modes(e)
    return not modes or bool(modes & COLOR_MODES)  # unknown capabilities are given the benefit of the doubt


def can_white(e: Entity) -> bool:
    modes = _modes(e)
    return not modes or "color_temp" in modes or bool(modes & COLOR_MODES)


def can_dim(e: Entity) -> bool:
    modes = _modes(e)
    return not modes or modes != {"onoff"}


def _act(service: str, e: Entity, **data) -> Action:
    return Action.make(service, e.id, data)


def _num(v: float):
    return int(v) if float(v).is_integer() else v


def _kelvin_bounds(e: Entity) -> tuple[int, int]:
    lo = e.attributes.get("min_color_temp_kelvin") or KELVIN_RANGE[0]
    hi = e.attributes.get("max_color_temp_kelvin") or KELVIN_RANGE[1]
    return int(lo), int(hi)


def set_level(e: Entity, percent: float) -> Result:
    """'to 40 percent': brightness, fan speed, cover position or volume, by device kind."""
    d = e.domain
    if d == "light":
        return Result([_act("light.turn_on", e, brightness_pct=_num(percent))]) if can_dim(e) else Result([], "brightness")
    if d == "fan":
        return Result([_act("fan.set_percentage", e, percentage=_num(percent))])
    if d == "cover":
        return Result([_act("cover.set_cover_position", e, position=_num(percent))])
    if d == "media_player":
        return Result([_act("media_player.volume_set", e, volume_level=round(percent / 100, 4))])
    return Result([], "level")


def set_option(e: Entity, option: str) -> Result:
    """A colour, a white tone, a level word or a thermostat mode."""
    d = e.domain
    if d == "light":
        if option in WHITES:
            if not can_white(e):
                return Result([], "white tone")
            return Result([_act("light.turn_on", e, color_temp_kelvin=WHITES[option])])
        if option in COLORS:
            if option == "white" and not can_color(e):  # plain "white" on a tunable-white lamp
                return Result([_act("light.turn_on", e, color_temp_kelvin=4000)]) if can_white(e) else Result([], "color")
            if not can_color(e):
                return Result([], "color")
            return Result([_act("light.turn_on", e, color_name=option)])
    if option in LEVELS.get(d, {}):
        return set_level(e, LEVELS[d][option])
    if d == "climate" and option in HVAC_MODES:
        modes = e.attributes.get("hvac_modes")
        if modes and option not in modes:
            return Result([], f"{option} mode")
        return Result([_act("climate.set_hvac_mode", e, hvac_mode=option)])
    return Result([], option)


def adjust(e: Entity, direction: str, amount: float | None = None, word: str | None = None) -> Result:
    """Relative change. direction is 'up' or 'down'; `word` is what the user said, if a descriptive one."""
    sign = 1 if direction == "up" else -1
    d = e.domain
    if d == "light":
        if word in ("warmer", "cooler") or word == "colder":
            if not can_white(e):
                return Result([], "white tone")
            lo, hi = _kelvin_bounds(e)
            now = e.attributes.get("color_temp_kelvin")
            step = amount or DEFAULT_STEPS["kelvin"]
            if now is None:
                target = 2700 if word == "warmer" else 5000
            else:
                target = float(now) + (-step if word == "warmer" else step)
            return Result([_act("light.turn_on", e, color_temp_kelvin=int(min(max(target, lo), hi)))])
        if not can_dim(e):
            return Result([], "brightness")
        return Result([_act("light.turn_on", e, brightness_step_pct=_num(sign * (amount or DEFAULT_STEPS["light"])))])
    if d == "media_player":
        service = "media_player.volume_up" if sign > 0 else "media_player.volume_down"
        return Result([_act(service, e)])
    if d == "fan":
        service = "fan.increase_speed" if sign > 0 else "fan.decrease_speed"
        return Result([_act(service, e)])
    if d == "climate":
        now = e.attributes.get("temperature")
        if now is None:
            return Result([], "temperature setting")
        step = amount or DEFAULT_STEPS["temp"]
        return Result([_act("climate.set_temperature", e, temperature=_num(float(now) + sign * step))])
    if d == "cover":
        return Result([_act("cover.open_cover" if sign > 0 else "cover.close_cover", e)])
    return Result([], "that")
