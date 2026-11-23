"""Lettered sets of lights ("Game Room Overhead Light A and B"), ceiling words that mean the same thing, and
names said in the speaker's own room."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.types import Entity, House

COLOR = {"supported_color_modes": ["color_temp", "hs"], "brightness": 200}


def light(i, name, area, members=None, state="on"):
    attrs = dict(COLOR, **({"entity_id": members} if members else {}))
    return Entity(f"light.{i}", name, area, state, attributes=attrs)


def house():
    ents = [
        light("den_cans", "Den Recessed Lights", "den", members=["light.den_can_a", "light.den_can_b"]),
        light("den_can_a", "Den Can Light A", "den"), light("den_can_b", "Den Can Light B", "den"),
        light("den_lamp", "Reading Lamp", "den"),
        light("game_a", "Game Room Overhead Light A", "game", state="off"),
        light("game_b", "Game Room Overhead Light B", "game", state="off"),
        light("game_strip", "Cabinet Strip", "game"),
        light("shop_over", "Shop Overhead Lights", "shop", members=["light.shop_a", "light.shop_b"]),
        light("shop_a", "Shop Overhead Light A", "shop"), light("shop_b", "Shop Overhead Light B", "shop"),
    ]
    areas = {"den": ("Den", None), "game": ("Game Room", None), "shop": ("Shop", None)}
    return House({}, areas, {e.id: e for e in ents})


def run(said, area="den"):
    out = asyncio.run(Pipeline(house()).ahandle(said, area, "c", "s"))
    return sorted({a.entity_id for a in out.actions}), out


@pytest.mark.parametrize("said, want", [
    ("make the game room overhead lights blue", ["light.game_a", "light.game_b"]),
    ("turn on the game room ceiling lights", ["light.game_a", "light.game_b"]),
    ("make the den overhead lights green", ["light.den_cans"]),
    ("make the den can lights green", ["light.den_cans"]),
    ("turn on the overhead lights", ["light.den_cans"]),  # said in the den: the den's, not the shop's
    ("turn on the shop overhead lights", ["light.shop_over"]),
    ("turn on game room overhead light a", ["light.game_a"]),
])
def test_sets_and_ceiling_words(said, want):
    assert run(said)[0] == want


def test_turn_these_on_means_this_room():
    assert run("turn these on")[0] == ["light.den_can_a", "light.den_can_b", "light.den_lamp"]


def test_a_set_said_elsewhere_still_needs_its_room_name():
    acts, out = run("turn on the overhead lights", area="game")
    assert acts == ["light.game_a", "light.game_b"]
