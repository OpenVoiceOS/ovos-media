"""OCP keyword registration for the liked-songs catalog.

Two consumers learn about keywords: the local Aho-Corasick NER matcher
inside ovos-workshop, and the OCP pipeline classifier, which is told over
the bus. The "ner" extra (``ahocorasick_ner``) is optional, and its
absence is not a pure speed optimization —
``OVOSCommonPlaybackSkill.ocp_voc_match`` hard-depends on it, so without
it "play my liked songs" style searches match nothing. The bus half does
not need it, but in ovos-workshop the emit happens after the per-language
NER registration inside the same method, so an ImportError there stops
the classifier from ever hearing about the keywords. That half is
replicated here so the classifier still learns them.
"""
from typing import Callable, List, Optional

from ovos_bus_client.message import Message
from ovos_utils.log import LOG
from ovos_utils.ocp import MediaType

PLAYLIST_KEYWORDS = ["favorite", "liked", "favorites",
                     "favorite songs", "favorite tracks",
                     "favorite music", "my favorite songs",
                     "my favorite tracks", "my favorite music",
                     "liked songs", "liked tracks", "liked music",
                     "my liked songs", "my liked tracks", "my liked music"]


def normalize_title(title: str) -> str:
    """Strip the parenthesised/bracketed decoration song titles carry
    ("Song (Live) - Remaster") down to the bare name people say."""
    return title.split("|")[0].split("(")[0].split("[")[0] \
        .split("{")[0].split("-")[0].strip()


class KeywordRegistrar:
    """Registers OCP keywords on behalf of a skill."""

    def __init__(self, bus, skill_id: str, native_langs: List[str],
                 cache_dir: str,
                 ner_register: Optional[Callable] = None) -> None:
        self.bus = bus
        self.skill_id = skill_id
        self.native_langs = native_langs
        self.cache_dir = cache_dir
        # OVOSCommonPlaybackSkill.register_ocp_keyword; absent when there is
        # no skill to register against, in which case only the bus half runs
        self.ner_register = ner_register

    def register_liked_songs(self, likes) -> None:
        """Register the liked song titles and the playlist synonyms."""
        titles = [normalize_title(t) for t in likes.titles()]
        try:
            if self.ner_register is None:
                raise ImportError("no NER-backed registration available")
            self.ner_register(MediaType.MUSIC, "song_name", titles)
            self.ner_register(MediaType.MUSIC, "playlist_name",
                              PLAYLIST_KEYWORDS)
        except ImportError:
            LOG.warning("ahocorasick_ner not installed - OCP local keyword "
                        "NER matching disabled, and 'search_db' (eg. 'play "
                        "my liked songs') will find nothing until it is "
                        "installed. Install the 'ner' extra to fix this. "
                        "The classifier is still informed of the keywords "
                        "via the bus so media-type disambiguation still "
                        "works.")
            self.emit(MediaType.MUSIC, "song_name", titles)
            self.emit(MediaType.MUSIC, "playlist_name", PLAYLIST_KEYWORDS)

    def emit(self, media_type: MediaType, label: str,
             samples: List[str]) -> None:
        """Tell the OCP pipeline classifier about a set of keyword samples.

        Mirrors the NER-independent tail half of
        ``OVOSCommonPlaybackSkill.register_ocp_keyword``: same message name
        and same payload shape, so the classifier cannot tell the
        difference.
        """
        samples = list(set(samples))
        for lang in self.native_langs:
            if len(samples) >= 20:
                csv_path = f"{self.cache_dir}/{self.skill_id}_{label}_{lang}.csv"
                with open(csv_path, "w") as f:
                    f.write("label,sample")
                    for s in samples:
                        f.write(f"\n{label},{s}")
                self.bus.emit(
                    Message('ovos.common_play.register_keyword',
                            {"skill_id": self.skill_id,
                             "label": label,
                             "csv": csv_path,
                             "media_type": media_type}))
            else:
                self.bus.emit(
                    Message('ovos.common_play.register_keyword',
                            {"skill_id": self.skill_id,
                             "label": label,
                             "samples": samples,
                             "media_type": media_type}))
