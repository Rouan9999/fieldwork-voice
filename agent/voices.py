"""Voice presets. Locked defaults (Rouan, Nov 26): Fish Drama 3, speed 0.95,
calm/unhurried direction, code-added fillers and short pauses.

All four Fish voices are community uploads (uploader owns them; rights unclear).
OK for testing and demos. Before a paying client goes live, use a voice we have
rights to (Fish-made voice or our own clone).
"""

MODEL = "drama-3"
SPEED = 0.95

PRESETS = {
    "catherine": "7fe8fcea5c3d4fbb9d153493a1e8be0e",          # "Kthy": calm, professional, slightly muffled
    "relaxed_young": "b88bf358091e4e22924af8e0580beafb",      # "Female": relaxed, friendly, casual
    "soft_breathy": "da3e7f40f72041eab8d986603b94d63b",       # "UGC Female": soft, breathy, relaxed
    "raspy_storyteller": "da1eeaa6379e491da2ea7d203d2d4b05",  # "Authentic Conversational Storyteller": raspy, everyday
}
DEFAULT = "catherine"


def voice_id(name: str | None) -> str:
    return PRESETS.get(name or DEFAULT, PRESETS[DEFAULT])
