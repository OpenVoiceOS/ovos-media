"""Role A — ovos-media as an MPRIS player.

``org.mpris.MediaPlayer2.OCP`` on the session bus, so KDE Connect, playerctl
and the GNOME Shell media widget drive the virtual player like any other. This
half is always active when ``enable_mpris`` is set and never depends on any
external player existing.

Property getters answer from the :class:`~ovos_media.player.dispatcher.PlayerSnapshot`
the dispatcher publishes after every command, not from live player attributes:
these run on the D-Bus thread, and the snapshot is the state the player has
agreed to be read from another thread. The two exceptions are documented where
they are made.

Every getter must return. A getter that raises kills ``Properties.GetAll`` for
the whole interface rather than that one property, and the values these read
come from the bus without validation, so Metadata falls back to ``{}``,
Position to ``0`` and Volume to ``1.0`` instead of propagating a bad value.
"""
import hashlib

from dbus_next.service import (ServiceInterface, method, signal, dbus_property,
                               PropertyAccess)
from dbus_next.signature import Variant

from ovos_bus_client.message import Message
from ovos_utils.log import LOG
from ovos_utils.ocp import PlaybackType, PlayerState, LoopState

from ovos_media.bus.schemas import is_real_number


# what MPRIS calls "no track is loaded"
NO_TRACK = '/org/mpris/MediaPlayer2/TrackList/NoTrack'


def submit_to_player(player, fn):
    """Run *fn* on the player's dispatcher.

    The MPRIS watcher and the D-Bus interfaces live on their own threads.
    Everything they change about the player goes through this boundary, so
    the player still has a single writer. A player without a dispatcher — a
    stub, or one assembled piecemeal — is called inline.
    """
    from ovos_media.player.dispatcher import Dispatcher  # avoids an import cycle
    dispatcher = getattr(player, "dispatcher", None)
    if isinstance(dispatcher, Dispatcher):
        dispatcher.submit(fn)
    else:
        fn()


class MprisExporter:
    """The two ``org.mpris.MediaPlayer2*`` interfaces and their lifecycle."""

    BUS_NAME = 'org.mpris.MediaPlayer2.OCP'
    OBJECT_PATH = '/org/mpris/MediaPlayer2'

    def __init__(self, player):
        self._ocp_player = player
        self.mediaPlayer2Interface = _MediaPlayer2Interface(
            player, 'org.mpris.MediaPlayer2')
        self.mediaPlayer2PlayerInterface = _MediaPlayer2PlayerInterface(
            player, 'org.mpris.MediaPlayer2.Player')
        self.playlistsInterface = _MediaPlayer2PlaylistsInterface(
            player, 'org.mpris.MediaPlayer2.Playlists')

    async def export(self, dbus):
        dbus.export(self.OBJECT_PATH, self.mediaPlayer2Interface)
        dbus.export(self.OBJECT_PATH, self.mediaPlayer2PlayerInterface)
        dbus.export(self.OBJECT_PATH, self.playlistsInterface)
        await dbus.request_name(self.BUS_NAME)
        LOG.info(f"MPRIS exported as {self.BUS_NAME} on the session bus")

    def update_props(self, props):
        self.mediaPlayer2PlayerInterface.emit_properties_changed(props)


class _MediaPlayer2Interface(ServiceInterface):
    def __init__(self, player, name='org.mpris.MediaPlayer2'):
        self._identity = "OCP"
        self._desktopEntry = "OCP"
        self._supportedMimeTypes = ["audio/mpeg", "audio/x-mpeg", "video/mpeg", "video/x-mpeg", "video/mpeg-system",
                                    "video/x-mpeg-system", "video/mp4", "audio/mp4", "video/x-msvideo",
                                    "video/quicktime", "application/ogg", "application/x-ogg", "video/x-ms-asf",
                                    "video/x-ms-asf-plugin", "application/x-mplayer2", "video/x-ms-wmv",
                                    "video/x-google-vlc-plugin", "audio/wav", "audio/x-wav", "audio/3gpp", "video/3gpp",
                                    "audio/3gpp2", "video/3gpp2", "video/divx", "video/flv", "video/x-flv",
                                    "video/x-matroska", "audio/x-matroska", "application/xspf+xml"]
        self._supportedUriSchemes = ["file", "http", "https", "rtsp", "realrtsp", "pnm", "ftp", "mtp", "smb", "mms",
                                     "mmsu", "mmst", "mmsh", "unsv", "itpc", "icyx", "rtmp", "rtp", "dccp", "dvd",
                                     "vcd"]
        self._canQuit = False
        self._hasTrackList = False
        self._ocp_player = player
        self._hasTrackList = len(self._ocp_player.playlist) > 0
        super().__init__(name)

    def update_props(self, props):
        self.emit_properties_changed(props)

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> 's':
        return self._identity

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> 's':
        return self._desktopEntry

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> 'as':
        return self._supportedMimeTypes

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> 'as':
        return self._supportedUriSchemes

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> 'b':
        # no org.mpris.MediaPlayer2.TrackList interface is exported yet;
        # claiming True here is exactly the "advertises an interface that
        # isn't there" defect this module exists to fix. False until a
        # TrackList stub ships alongside the stored-collections work.
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> 'b':
        return self._canQuit

    @dbus_property(access=PropertyAccess.READ)
    def CanSetFullscreen(self) -> 'b':
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Fullscreen(self) -> 'b':
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> 'b':
        return False

    @method()
    def Quit(self):
        if self._canQuit:
            self._ocp_player.shutdown()


class _MediaPlayer2PlaylistsInterface(ServiceInterface):
    """A minimal ``org.mpris.MediaPlayer2.Playlists`` stub.

    Optional per the MPRIS spec, but a desktop shell probing for it without
    finding it makes dbus_next log an UNKNOWN_INTERFACE error traceback via
    the root logger on every real desktop. Registering the interface with no
    playlists silences that noise and reserves the surface stored playlists
    can fill in later, without promising anything is stored today.
    """

    def __init__(self, player, name='org.mpris.MediaPlayer2.Playlists'):
        self._ocp_player = player
        super().__init__(name)

    @dbus_property(access=PropertyAccess.READ)
    def PlaylistCount(self) -> 'u':
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def Orderings(self) -> 'as':
        return ["Alphabetical"]

    @dbus_property(access=PropertyAccess.READ)
    def ActivePlaylist(self) -> '(b(oss))':
        # dbus_next marshals a STRUCT from a list, not a tuple; a tuple here
        # passes at the Python level and blows up SignatureBodyMismatchError
        # the moment anything (ObjectManager.InterfacesAdded, GetAll) tries
        # to put it on the wire.
        return [False, ["/", "", ""]]

    @method()
    def ActivatePlaylist(self, playlist_id: 'o'):
        pass

    @method()
    def GetPlaylists(self, index: 'u', max_count: 'u', order: 's', reverse: 'b') -> 'a(oss)':
        return []


class _MediaPlayer2PlayerInterface(ServiceInterface):
    def __init__(self, player, name):
        super().__init__(name)
        self._ocp_player = player

    @property
    def _snapshot(self):
        """What the player has published for off-thread readers."""
        return self._ocp_player.snapshot

    def _track_id(self) -> str:
        """The object path naming the current track.

        MPRIS identifies a track by an object path so a client can prove the
        position it sets belongs to the track it was looking at, rather than
        to whatever started playing in the meantime. ovos-media has no track
        identity of its own, so the path is derived from the current uri: it
        is stable while that track plays and different for the next one,
        which is the whole property SetPosition needs.
        """
        uri = getattr(self._ocp_player.now_playing, "uri", None) or ""
        if not uri:
            return NO_TRACK
        digest = hashlib.sha1(uri.encode("utf-8")).hexdigest()[:16]
        return f"/org/mpris/MediaPlayer2/Track/{digest}"

    def _position_ms(self) -> int:
        """Current position in milliseconds, 0 for anything unusable."""
        now_playing = self._ocp_player.now_playing
        if not now_playing:
            return 0
        position = now_playing.position
        if not is_real_number(position):
            return 0
        return max(0, min(int(position), 2 ** 63 - 1))

    def _seekable(self) -> bool:
        """Whether a seek would reach a player that can honour it.

        ``PlaybackType.UNDEFINED`` is deliberately not consulted: nothing is
        loaded, so the routing table's "make sure nothing is playing anywhere"
        fan-out is the wrong answer to "can this be seeked". Advertising
        CanSeek while idle would put a live seekbar in front of the user with
        no track behind it.
        """
        if self._snapshot.playback_type == PlaybackType.UNDEFINED:
            return False
        return bool(self._routes("seek"))

    def _seek_to(self, milliseconds: int) -> None:
        """Seek on the player's own thread, then tell the bus where we landed."""
        def seek():
            self._ocp_player.seek(milliseconds)
            # the spec wants the position after the seek; the backends report
            # theirs asynchronously over ovos.common_play.playback_time, so
            # the requested position is the only one available here, and it is
            # what every controller redraws its seekbar to anyway
            self.Seeked(milliseconds * 1000)

        submit_to_player(self._ocp_player, seek)

    def _routes(self, verb):
        """The concrete players *verb* would reach right now.

        The routing table in :mod:`ovos_media.player.roster` already records
        which verbs reach which players per playback type; the Can* properties
        read it instead of keeping a second, drifting record of the same thing.
        """
        roster = getattr(self._ocp_player, "roster", None)
        if roster is None:
            return []
        try:
            return roster.route(verb, self._snapshot.playback_type)
        except Exception as e:
            LOG.warning(f"failed to resolve mpris route for {verb}: {e}")
            return []

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> 'a{sv}':
        # read live rather than from the snapshot: the snapshot carries
        # now_playing serialized as a plain dict, and rebuilding a MediaEntry
        # from it to call mpris_metadata would move this guard's failure mode
        # from "bad metadata" to "bad serialization" without removing it
        if self._ocp_player.now_playing:
            # mpris_metadata wraps length in a Variant('d', ...); a bus-fed
            # length can arrive malformed (missing/None/wrong type - same
            # ungated MediaEntry.update ingestion path as Position above),
            # and a failing property getter kills Properties.GetAll for the
            # whole Player interface, not just this property - fall back to
            # empty metadata rather than let that happen.
            try:
                meta = dict(self._ocp_player.now_playing.mpris_metadata)
                # the track identity SetPosition validates against; upstream
                # mpris_metadata does not carry one, and without it every
                # SetPosition would have to be refused
                meta["mpris:trackid"] = Variant('o', self._track_id())
                return meta
            except Exception as e:
                LOG.warning(f"failed to build mpris metadata: {e}")
                return {}
        return {}

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> 's':
        state = self._snapshot.player_state
        if state == PlayerState.PLAYING:
            return "Playing"
        if state == PlayerState.PAUSED:
            return "Paused"
        return "Stopped"

    @dbus_property()
    def LoopStatus(self) -> 's':
        if self._snapshot.loop_state == LoopState.REPEAT_TRACK:
            return "Track"  # MPRIS 2.2 spec: "None", "Track", or "Playlist"
        if self._snapshot.loop_state == LoopState.REPEAT:
            return "Playlist"
        return "None"

    @LoopStatus.setter
    def LoopStatus_setter(self, val: 's'):
        if val == "Track":
            submit_to_player(self._ocp_player, lambda: setattr(self._ocp_player, "loop_state", LoopState.REPEAT_TRACK))
        elif val == "Playlist":
            submit_to_player(self._ocp_player, lambda: setattr(self._ocp_player, "loop_state", LoopState.REPEAT))
        else:
            submit_to_player(self._ocp_player, lambda: setattr(self._ocp_player, "loop_state", LoopState.NONE))

    @dbus_property()
    def Shuffle(self) -> 'b':
        return self._snapshot.shuffle

    @Shuffle.setter
    def Shuffle_setter(self, val: 'b'):
        submit_to_player(self._ocp_player, lambda: setattr(self._ocp_player, "shuffle", val))

    @dbus_property()
    def Volume(self) -> 'd':
        # a failing property getter kills Properties.GetAll for the whole
        # Player interface, not just this property (same class of failure
        # the Metadata/Position guards prevent) - the bus response is
        # unvalidated, so fall back to full volume rather than let a
        # missing/None/non-numeric "percent" propagate.
        msg = self._ocp_player.bus.wait_for_response(Message("mycroft.volume.get"), timeout=0.5)
        if msg:
            try:
                return float(msg.data["percent"])
            except Exception as e:
                LOG.warning(f"failed to parse volume percent: {e}")
                return 1.0
        return 1.0

    @Volume.setter
    def Volume_setter(self, val: 'd'):
        self._ocp_player.bus.emit(Message("mycroft.volume.set", {"percent": val}))

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> 'd':
        return 1

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> 'x':
        # the one live read: position advances between commands, and a
        # snapshot value would leave every MPRIS client showing a seekbar
        # frozen at whatever the last command saw. The dispatcher sanctions
        # this read for exactly that reason.
        if self._ocp_player.now_playing:
            # now_playing.position is in milliseconds (repo-wide ms contract,
            # produced by ovos-plugin-manager templates); MPRIS Position is
            # in microseconds, hence * 1000 (not * 1e6, which would treat
            # position as seconds). MPRIS2 spec requires signature 'x'
            # (int64), so cast to int - strict clients (playerctl, GNOME
            # Shell) misparse/reject a 'd' (double) wire value.
            position = self._ocp_player.now_playing.position
            # a bus-fed position can arrive malformed (missing/None/wrong
            # type/NaN/inf - MediaEntry.update sets attrs directly, with no
            # guard on this ingestion path); never let a bad value break
            # Properties.GetAll for the whole Player interface, fall back to
            # 0 like "no now_playing"
            if not is_real_number(position):
                return 0
            # dbus_next marshals 'x' as a signed 64-bit int; a value outside
            # that range raises during marshalling (after this getter has
            # already returned), so clamp into range rather than let a huge
            # finite position (eg. a bogus seekbar sync) blow up the whole
            # Properties.GetAll response
            value = int(position * 1000)
            return max(0, min(value, 2 ** 63 - 1))
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> 'b':
        return self._snapshot.player_state == PlayerState.PAUSED

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> 'b':
        if self._snapshot.player_state != PlayerState.PLAYING:
            return False
        # an external MPRIS player is paused through the manager, which has
        # no row in the routing table
        if self._snapshot.playback_type == PlaybackType.MPRIS:
            return True
        return bool(self._routes("pause"))

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> 'b':
        return self._seekable()

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> 'b':
        return self._ocp_player.can_next

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> 'b':
        return self._ocp_player.can_prev

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> 'b':
        return True

    @method()
    def Previous(self):
        submit_to_player(self._ocp_player, self._ocp_player.play_prev)

    @method()
    def Next(self):
        submit_to_player(self._ocp_player, self._ocp_player.play_next)

    @method()
    def Stop(self):
        submit_to_player(self._ocp_player, self._ocp_player.stop)

    @method()
    def Play(self):
        submit_to_player(self._ocp_player, self._ocp_player.resume)

    @method()
    def Pause(self):
        submit_to_player(self._ocp_player, self._ocp_player.pause)

    @method()
    def Seek(self, offset_us: 'x'):
        """Move the position by *offset_us* microseconds, forwards or back."""
        if not self._seekable():
            LOG.debug("Seek requested with nothing seekable loaded, ignoring")
            return
        self._seek_to(max(0, self._position_ms() + int(offset_us / 1000)))

    @method()
    def SetPosition(self, track_id: 'o', position_us: 'x'):
        """Move to *position_us* microseconds into *track_id*.

        The spec requires the call to be ignored when the track has changed
        since the client read the metadata, which is what stops a stale
        seekbar drag from jumping the track that replaced it.
        """
        if not self._seekable():
            LOG.debug("SetPosition requested with nothing seekable loaded, ignoring")
            return
        if track_id != self._track_id():
            LOG.debug(f"SetPosition for a track that is no longer playing "
                      f"({track_id}), ignoring")
            return
        self._seek_to(max(0, int(position_us / 1000)))

    @signal()
    def Seeked(self, position_us) -> 'x':
        return position_us

    @method()
    def PlayPause(self):
        submit_to_player(self._ocp_player, self._play_pause)

    def _play_pause(self):
        if self._snapshot.player_state == PlayerState.PAUSED:
            self._ocp_player.resume()
        else:
            self._ocp_player.pause()
