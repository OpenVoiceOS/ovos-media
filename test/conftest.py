"""The suite must read only its own configuration.

ovos-config resolves the user config path (XDG_CONFIG_HOME) at import
time and falls back to the host system locale for its language, so the
redirect has to happen at collection start, before any test module
imports an OVOS package, and it has to pin the language explicitly -
otherwise the host machine's ~/.config/mycroft/mycroft.conf or its
locale leaks into every skill and player under test and the
dialog/intent resources resolve against the host language instead of
the repo's en-us fixtures. Run the suite with
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1: the ovoscope pytest plugin imports the
OVOS stack when pytest starts, before this file runs, and the language
it freezes then wins over this redirect.
"""
import json
import os
import tempfile

_conf_dir = tempfile.mkdtemp(prefix="ovos-media-test-xdg-")
os.environ["XDG_CONFIG_HOME"] = _conf_dir
os.makedirs(os.path.join(_conf_dir, "mycroft"), exist_ok=True)
with open(os.path.join(_conf_dir, "mycroft", "mycroft.conf"), "w") as f:
    # media.enable_mpris now defaults to True in the player itself, which
    # is correct for a real install; a test that builds a player from a
    # config lacking the key must not fall through to that default, or
    # every such test starts a real D-Bus thread and claims
    # org.mpris.MediaPlayer2.OCP on whatever session bus the runner has.
    # Tests exercising MPRIS behaviour opt in explicitly with their own
    # config instead of relying on this fallback.
    json.dump({"lang": "en-us", "media": {"enable_mpris": False}}, f)
