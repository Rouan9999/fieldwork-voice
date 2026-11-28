"""Voice presets. Rouan (Nov 28, 2026): Fish Drama 3, speed 1.0 (was 0.95: too slow),
warm/confident direction (was calm/tentative: sounded unsure), light fillers.

Top 3 (each roofer picks one):
  catherine      https://fish.audio/app/m/7fe8fcea5c3d4fbb9d153493a1e8be0e/
  relaxed_young  https://fish.audio/app/text-to-speech/?modelId=b88bf358091e4e22924af8e0580beafb
  soft_breathy   https://fish.audio/app/text-to-speech/?modelId=da3e7f40f72041eab8d986603b94d63b
Tried, not in top 3: raspy_storyteller https://fish.audio/m/da1eeaa6379e491da2ea7d203d2d4b05
Still to find: male / "country" presets.

All are community uploads (the uploader owns them; rights unclear). OK for
tests and demos. Before a paying client goes live, use a voice we have rights to.
"""

MODEL = "drama-3"
SPEED = 1.0

PRESETS = {
    "catherine": "7fe8fcea5c3d4fbb9d153493a1e8be0e",       # "Kthy": calm, professional, slightly muffled
    "relaxed_young": "b88bf358091e4e22924af8e0580beafb",   # "Female": relaxed, friendly, casual
    "soft_breathy": "da3e7f40f72041eab8d986603b94d63b",    # "UGC Female": soft, breathy, relaxed
}
DEFAULT = "catherine"


def voice_id(name: str | None) -> str:
    return PRESETS.get(name or DEFAULT, PRESETS[DEFAULT])
