"""Role B — awareness of the other MPRIS players on the machine.

Opt-in through ``manage_external_players``. When it is on, ovos-media watches
the session bus for Spotify, VLC, Firefox and friends, mirrors whichever one is
playing onto the virtual player as ``PlaybackType.MPRIS`` now-playing, and
applies the takeover policy: an external player that starts playing wins, and
everything ovos-media was running gives way to it.

Each external player also joins the roster as a
:class:`~ovos_media.player.adapters.MprisPlayerAdapter`, so the virtual player's
picture of "everything that can play media here" includes the players it does
not own.

Adapters registered here are marked ``external``. That matters for
:meth:`~ovos_media.player.OCPMediaPlayer.handle_MPRIS_takeover`, which stops
every roster adapter so nothing keeps playing under the external player that
just took over: including the MPRIS adapters in that sweep would stop the
external player the takeover exists to yield to, so the sweep skips them.
"""
import asyncio
import os.path
from threading import Event

from dbus_next.message import Message as DbusMessage, MessageType as DbusMessageType

from ovos_utils.log import LOG
from ovos_utils.ocp import TrackState, PlaybackType, PlayerState, LoopState, MediaState

from ovos_media.mpris.exporter import submit_to_player

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "qt5", "images")


class ExternalPlayerManager:
    """Watches the other MPRIS players and applies the takeover policy."""

    def __init__(self, player, loop, config=None, manage_players=False):
        self._ocp_player = player
        self.loop = loop
        self.config = config or {}
        # honor the manage_players argument, letting config override it; use
        # self.config (never the raw param) so config=None can't crash here
        self.manage_players = self.config.get("manage_external_players", manage_players)
        self.ignored_players = self.config.get("ignored_players", [
            "org.mpris.MediaPlayer2.OCP",
            "org.mpris.MediaPlayer2.plasma-browser-integration"  # browsers already show up as individual players
        ])

        self.main_player = None
        self.players = {}
        self.player_meta = {}
        self.adapters = {}
        self._player_fails = {}
        # set while a stop-everything request is outstanding. The player reads
        # it to avoid asking twice, and the reflection below stands down while
        # it is set: mirroring an external player onto a player we are in the
        # middle of stopping would undo the stop.
        self.stop_event = Event()

    # --- reflection onto the virtual player -------------------------------
    def _update_ocp(self):
        if self.stop_event.is_set() or not self.manage_players:
            return

        if self._ocp_player and self.player_meta.get(self.main_player):
            submit_to_player(self._ocp_player, self._apply_external_player_state)

    def _apply_external_player_state(self):
        """Mirror the polled external player onto OCP. Runs as one
        dispatcher command so no other command interleaves with it."""
        data = self.player_meta.get(self.main_player)
        if data:

            # reset ocp, it will display metadata of current track
            render = False
            if self._ocp_player.active_skill != self.main_player:
                self._ocp_player.reset()
                self._ocp_player.active_skill = self.main_player
                render = True

            # player state
            state = data.get("state") or "Playing"
            if state == "Paused":
                self._ocp_player.set_player_state(PlayerState.PAUSED)
                self._ocp_player.set_media_state(MediaState.BUFFERED_MEDIA)
            elif state == "Playing":
                self._ocp_player.set_player_state(PlayerState.PLAYING)
                self._ocp_player.set_media_state(MediaState.BUFFERED_MEDIA)
            else:
                self._ocp_player.set_player_state(PlayerState.STOPPED)
                self._ocp_player.set_media_state(MediaState.END_OF_MEDIA)

            state = data.get("loop_state") or 0
            if state == 1:
                self._ocp_player.loop_state = data["loop_state"] = LoopState.REPEAT
            elif state == 2:
                self._ocp_player.loop_state = data["loop_state"] = LoopState.REPEAT_TRACK
            else:
                self._ocp_player.loop_state = data["loop_state"] = LoopState.NONE

            self._ocp_player.shuffle = data.get("shuffle") or self._ocp_player.shuffle
            self._ocp_player.playback_type = PlaybackType.MPRIS

            # update ocp metadata
            data["skill_id"] = data.get("external_player") or self.main_player
            data["bg_image"] = data.get("image") or data.get("thumbnail")
            data["playback"] = PlaybackType.MPRIS
            data["status"] = TrackState.PLAYING_MPRIS
            data["length"] = data.get("length", 0) / 1000
            data["skill_icon"] = self._icon_for(self.main_player)

            self._ocp_player.set_now_playing(data)

    @staticmethod
    def _icon_for(name: str) -> str:
        """Dedicated icons for some common players, the generic one otherwise."""
        if name == 'org.mpris.MediaPlayer2.spotify':
            icon = "spotify.png"
        elif name.startswith("org.mpris.MediaPlayer2.firefox"):
            icon = "firefox.png"
        elif name.startswith("org.mpris.MediaPlayer2.chromium"):
            icon = "chromium.png"
        elif name == "org.mpris.MediaPlayer2.vlc":
            icon = "vlc.png"
        elif name == "org.mpris.MediaPlayer2.mpv":
            icon = "mpv.png"
        elif name == "org.mpris.MediaPlayer2.audacious":
            icon = "audacious.png"
        else:
            icon = "mpris.png"
        return os.path.join(ICON_DIR, icon)

    # --- roster membership -------------------------------------------------
    def _register_adapter(self, name):
        # imported here, not at module scope: ovos_media.player imports this
        # package, so a module-level import back into it would be a cycle
        from ovos_media.player.adapters import MprisPlayerAdapter
        roster = getattr(self._ocp_player, "roster", None)
        if roster is None or name in self.adapters:
            return
        adapter = MprisPlayerAdapter(self, name)
        self.adapters[name] = adapter
        # the roster is read on the dispatcher thread; join it from there too
        submit_to_player(self._ocp_player, lambda: roster.register(adapter))

    def _unregister_adapter(self, name):
        adapter = self.adapters.pop(name, None)
        roster = getattr(self._ocp_player, "roster", None)
        if adapter is None or roster is None:
            return
        submit_to_player(self._ocp_player, lambda: roster.unregister(adapter.id))

    # --- signal handlers ---------------------------------------------------
    async def handle_new_player(self, data):
        if data['name'] not in self._player_fails:
            LOG.info(f"Found MPRIS Player: {data['name']}")

    async def handle_player_shuffle(self, shuffle):
        LOG.info(f"MPRIS Player Shuffle: {shuffle}")
        if self.manage_players:
            submit_to_player(self._ocp_player,
                             lambda: setattr(self._ocp_player, "shuffle", shuffle))

    async def handle_player_loop_state(self, state):
        LOG.info(f"MPRIS Player Repeat: {state}")
        if self.manage_players:
            loop = {1: LoopState.REPEAT, 2: LoopState.REPEAT_TRACK}.get(
                state, LoopState.NONE)
            submit_to_player(self._ocp_player,
                             lambda: setattr(self._ocp_player, "loop_state", loop))

    async def handle_player_state(self, state):
        LOG.info(f"MPRIS Player State: {state}")
        if self.manage_players and self._ocp_player:
            submit_to_player(self._ocp_player,
                             lambda: self._apply_external_player_transport(state))

    def _apply_external_player_transport(self, state):
        if state == "Paused":
            self._ocp_player.set_player_state(PlayerState.PAUSED)
        elif state == "Playing":
            self._ocp_player.handle_MPRIS_takeover()
            self._ocp_player.playback_type = PlaybackType.MPRIS
            self._ocp_player.set_player_state(PlayerState.PLAYING)
        else:
            self._ocp_player.set_player_state(PlayerState.STOPPED)

    async def handle_lost_player(self, name):
        LOG.info(f"Lost MPRIS Player: {name}")
        if name in self.player_meta:
            self.player_meta.pop(name)
        if name in self.players:
            self.players.pop(name)
        self._unregister_adapter(name)

    async def handle_sync_player(self, data):
        if data.get("state") == 'Playing':
            await self._set_main_player(data["external_player"])
        elif data["external_player"] == self.main_player:
            self._update_ocp()

    async def _set_main_player(self, name):
        old_main = self.main_player
        self.main_player = name
        if name != old_main:
            LOG.info(f"Active MPRIS player: {name}")
        # if there are multiple external players playing, stop the
        # previous ones!
        if self.manage_players:
            self._update_ocp()
            for p, dta in self.players.items():
                if p == name:
                    continue
                try:
                    if self.player_meta[name]["state"] == "Playing":
                        await self._stop_player(p)
                except:
                    LOG.error(f"failed to stop: {p}")

    # --- driving one external player --------------------------------------
    async def _play_prev(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            if self.player_meta[name]["state"] == "Playing":
                LOG.debug(f"player previous {name}")
                player = self.players[name].get_interface('org.mpris.MediaPlayer2.Player')
                await player.call_previous()
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._play_prev(name, max_tries)
            else:
                LOG.warning(f"player {name} does not support Previous")

    async def _play_next(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            if self.player_meta[name]["state"] == "Playing":
                LOG.debug(f"player next {name}")
                player = self.players[name].get_interface('org.mpris.MediaPlayer2.Player')
                await player.call_next()
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._play_next(name, max_tries)
            else:
                LOG.warning(f"player {name} does not support Next")

    async def _pause_player(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            if self.player_meta[name]["state"] == "Playing":
                LOG.debug(f"pausing player {name}")
                player = self.players[name].get_interface(
                    'org.mpris.MediaPlayer2.Player')
                await player.call_pause()
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._pause_player(name, max_tries)
            else:
                LOG.warning(f"player {name} can not be paused")

    async def _shuffle_enable(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            LOG.debug(f"enabling shuffle for player {name}")
            player = self.players[name].get_interface(
                'org.mpris.MediaPlayer2.Player')
            await player.set_shuffle(True)
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._shuffle_enable(name, max_tries)
            else:
                LOG.warning(f"player {name} cant control shuffle")

    async def _shuffle_disable(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            LOG.debug(f"disabling shuffle for player {name}")
            player = self.players[name].get_interface(
                'org.mpris.MediaPlayer2.Player')
            await player.set_shuffle(False)
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._shuffle_disable(name, max_tries)
            else:
                LOG.warning(f"player {name} cant control shuffle")

    async def _repeat_disable(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            LOG.debug(f"disabling repeat for player {name}")
            player = self.players[name].get_interface(
                'org.mpris.MediaPlayer2.Player')
            await player.set_loop_status("None")
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._repeat_disable(name, max_tries)
            else:
                LOG.warning(f"player {name} cant control repeat state")

    async def _repeat_enable(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            LOG.debug(f"enabling repeat for player {name}")
            player = self.players[name].get_interface(
                'org.mpris.MediaPlayer2.Player')
            await player.set_loop_status("Playlist")
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._repeat_enable(name, max_tries)
            else:
                LOG.warning(f"player {name} cant control repeat state")

    async def _repeat_track_enable(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            LOG.debug(f"enabling repeat for player {name}")
            player = self.players[name].get_interface(
                'org.mpris.MediaPlayer2.Player')
            await player.set_loop_status("Track")
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._repeat_track_enable(name, max_tries)
            else:
                LOG.warning(f"player {name} cant control repeat state")

    async def _resume_player(self, name, max_tries=1):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            if self.player_meta[name]["state"] != "Playing":
                LOG.debug(f"resuming player {name}")
                player = self.players[name].get_interface(
                    'org.mpris.MediaPlayer2.Player')
                await player.call_play()
        except:
            max_tries -= 1
            if max_tries > 0:
                await self._resume_player(name, max_tries)
            else:
                LOG.warning(f"player {name} can not be resumed")

    async def _stop_player(self, name, max_tries=2):
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        if name not in self.player_meta:
            # not yet queried (race with scan_players), nothing to stop
            LOG.debug(f"player {name} has no metadata yet, skipping stop")
            return
        try:
            if self.player_meta[name]["state"] == "Playing":
                LOG.info(f"Stopping MPRIS player: {name}")
                player = self.players[name].get_interface(
                    'org.mpris.MediaPlayer2.Player')
                await player.call_stop()
        except Exception:
            max_tries -= 1
            if max_tries > 0:
                await self._stop_player(name, max_tries)
            else:
                # stop failed - leave state untouched so a later _stop_all
                # pass retries it instead of silently treating it as stopped
                LOG.warning(f"player {name} can not be stopped")
            return
        if name == self.main_player:
            self.main_player = None
        self.player_meta[name]["state"] = "Stopped"

    async def _stop_all(self):
        # snapshot before iterating - each await below can yield to the
        # event loop, where a concurrent handle_lost_player() (dispatched by
        # on_properties_changed on the same loop) may pop from self.players
        # mid-iteration and raise "dictionary changed size during iteration"
        for p in list(self.players):
            await self._stop_player(p)

    async def _pause_all(self):
        for p in list(self.players):
            await self._pause_player(p)

    # --- commands posted from the player's thread -------------------------
    async def do_stop_all(self):
        try:
            await self._stop_all()
        finally:
            self.stop_event.clear()

    async def do_pause_all(self):
        await self._pause_all()

    async def do_play_prev(self):
        await self._play_prev(self.main_player)

    async def do_play_next(self):
        await self._play_next(self.main_player)

    async def do_resume(self):
        await self._resume_player(self.main_player)

    async def do_toggle_shuffle(self):
        meta = self.player_meta.get(self.main_player) or {}
        if meta.get("shuffle", self._ocp_player.shuffle):
            await self._shuffle_enable(self.main_player)
        else:
            await self._shuffle_disable(self.main_player)

    async def do_toggle_repeat(self):
        meta = self.player_meta.get(self.main_player) or {}
        state = meta.get("loop_state") or self._ocp_player.loop_state
        if state == LoopState.NONE:
            await self._repeat_enable(self.main_player)
        elif state == LoopState.REPEAT:
            await self._repeat_track_enable(self.main_player)
        elif state == LoopState.REPEAT_TRACK:
            await self._repeat_disable(self.main_player)

    # --- discovery ---------------------------------------------------------
    async def scan_players(self):
        reply = await self.loop.dbus.call(
            DbusMessage(destination='org.freedesktop.DBus',
                        path='/org/freedesktop/DBus',
                        interface='org.freedesktop.DBus',
                        member='ListNames'))

        if reply.message_type == DbusMessageType.ERROR:
            raise Exception(reply.body[0])

        players = []
        for name in reply.body[0]:
            if "org.mpris.MediaPlayer2" in name:
                if name.startswith("org.mpris.MediaPlayer2.kdeconnect.") or \
                        name in self.players or \
                        name in self.ignored_players:
                    continue
                await self.handle_new_player({"name": name})

                try:
                    introspection = await self.loop.dbus.introspect(
                        name, '/org/mpris/MediaPlayer2')
                    self.players[name] = self.loop.dbus.get_proxy_object(
                        name, '/org/mpris/MediaPlayer2', introspection)
                    self._register_adapter(name)
                    self._create_player_handler(name)
                    await self.query_player(name)
                except:
                    LOG.exception(f"Failed to introspect player: {name}")

        return players

    def _create_player_handler(self, name):
        player = self.players[name]
        try:
            properties = player.get_interface(
                'org.freedesktop.DBus.Properties')
        except:
            # chromium
            LOG.warning(f"Player {name} does not allow reading properties")
            return

        # listen to signals
        async def on_properties_changed(interface_name,
                                        changed_properties,
                                        invalidated_properties):
            for changed, variant in changed_properties.items():
                player_name = properties.bus_name
                if player_name in self.ignored_players:
                    continue
                meta = self.player_meta.setdefault(player_name, {"external_player": player_name})
                if changed == "PlaybackStatus":
                    await self.handle_player_state(variant.value)
                    state = meta.get("state")
                    if state != variant.value or not state:
                        meta["state"] = variant.value
                        await self.handle_sync_player(
                            {"state": variant.value,
                             "external_player": player_name})
                elif changed == "Metadata":
                    ocp_data = self._meta2dict(name, variant.value)
                    LOG.info(f"MPRIS info: {ocp_data}")
                    await self.update_player_meta(player_name, variant.value)
                    if name == self.main_player:
                        self._update_ocp()
                elif changed == "Shuffle":
                    meta["shuffle"] = variant.value
                    await self.handle_player_shuffle(variant.value)
                elif changed == "LoopStatus":
                    if variant.value == "Track":
                        state = LoopState.REPEAT_TRACK
                    elif variant.value == "Playlist":
                        state = LoopState.REPEAT
                    else:
                        state = LoopState.NONE
                    meta["loop_state"] = state
                    await self.handle_player_loop_state(state)
                # else:
                #    LOG.debug(f'{changed} - {variant.value}')

        properties.on_properties_changed(on_properties_changed)

    def _meta2dict(self, name, meta):
        ocp_data = {"external_player": name}

        # these are injected when player is queried
        ocp_data["state"] = meta.get("state")
        ocp_data["loop_state"] = meta.get("loop_state")

        for k, v in meta.items():
            if k == "xesam:title":
                ocp_data["title"] = v.value
            elif k == "xesam:artist":
                artist = v.value
                if isinstance(artist, (list, tuple)):
                    if artist:
                        ocp_data["artist"] = artist[0]
                elif isinstance(artist, str):
                    ocp_data["artist"] = artist
            elif k == "xesam:album":
                ocp_data["album"] = v.value
            elif k == "mpris:artUrl":
                ocp_data["image"] = v.value
            elif k == "mpris:length":
                ocp_data["length"] = v.value
            elif k == "xesam:url":
                ocp_data["uri"] = v.value

        # dict2entry refuses a track without a uri, and not every player
        # reports xesam:url - fall back to a synthetic identifier so the
        # reflected now-playing can always be constructed
        if not ocp_data.get("uri"):
            ocp_data["uri"] = f"mpris://{name}"

        # some players dont report state directly (eg, firefox)
        if not ocp_data["state"] and ocp_data.get("title"):
            ocp_data["state"] = "Playing"
        return ocp_data

    async def update_player_meta(self, name, meta):
        ocp_data = self._meta2dict(name, meta)
        if name not in self.player_meta:
            LOG.info(f"MPRIS info: {ocp_data}")
        self.player_meta[name] = ocp_data
        if self.main_player is None and ocp_data.get("state", "") == "Playing":
            LOG.info(f"Active MPRIS player: {name}")
            await self._set_main_player(name)
        await self.handle_sync_player(ocp_data)

    async def query_player(self, name):
        if self._player_fails.get(name, 0) >= 3:
            # do not keep querying players that dont expose full mpris functionality
            return
        if name not in self.players:
            LOG.error(f"Invalid player: {name}")
            return
        try:
            player = self.players[name].get_interface(
                'org.mpris.MediaPlayer2.Player')
            meta = await player.get_metadata()
            meta["external_player"] = name
            try:
                meta["state"] = await player.get_playback_status()
            except:  # dbus_next.errors.DBusError
                pass
            try:
                loop_status = await player.get_loop_status()
                if loop_status == "None":
                    # The playback will stop when there are no more tracks to play
                    meta["loop_state"] = LoopState.NONE
                elif loop_status == "Track":
                    # The current track will start again from the begining once it has finished playing
                    meta["loop_state"] = LoopState.REPEAT_TRACK
                elif loop_status == "Playlist":
                    # The playback loops through a list of tracks
                    meta["loop_state"] = LoopState.REPEAT
            except AttributeError:
                pass  # not all players expose this
            await self.update_player_meta(name, meta)
            self._player_fails[name] = 0
        except Exception as e:  # chromium / player closed
            if name not in self._player_fails:
                self._player_fails[name] = 0
            self._player_fails[name] += 1
            if self._player_fails[name] > 3:
                LOG.debug(f"failed to query player {name}")
                await self.handle_lost_player(name)

    async def tick(self):
        """One pass of the watch loop, also the loop's pacing.

        With external-player management off this is just the idle sleep: the
        exporter needs the loop alive to serve D-Bus, but nothing polls.
        """
        poll_interval = self.config.get("mpris_poll_interval", 1)
        if not self.manage_players:
            await asyncio.sleep(poll_interval)
            return

        await self.scan_players()
        await asyncio.sleep(poll_interval)

        # sync player meta, not all players send all events properly...
        # eg, firefox videos do not send events if they autoplay, only if
        # you click the play button
        for player in list(self.players.keys()):
            await self.query_player(player)
        await asyncio.sleep(poll_interval)
