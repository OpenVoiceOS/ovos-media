# OVOS media service

WIP - nothing to see here yet

## Architecture

![imagem](https://github.com/NeonJarbas/ovos-media/assets/59943014/7dc1d635-4340-43db-a38d-294cfedab70f)

## MPRIS integration

Integration with MPRIS allows OCP to control external players

![imagem](https://github.com/NeonJarbas/ovos-media/assets/33701864/856c0228-8fc5-4ee6-a19d-4290f2e07258)


## Install

`pip install ovos-media` to install this package and the default plugins.

# Configuration

under mycroft.conf

```javascript
{
  // Configure ovos-media service
  // similarly to wakewords, configure any number of playback handlers
  // playback handlers might be local applications or even remote devices
  "media": {

    // order of preference to try playback handlers
    // if unavailable or unable to handle a uri, the next in list is used
    // NB: users may request specific handlers in the utterance

    // keys are the strings defined in "audio_players"
    "preferred_audio_services": ["gui", "vlc", "mplayer", "cli"],

    // keys are the strings defined in "web_players"
    "preferred_web_services": ["gui", "browser"],

    // keys are the strings defined in "video_players"
    "preferred_video_services": ["gui", "vlc"],

    // PlaybackType.AUDIO handlers
    "audio_players": {
        // vlc player uses a headless vlc instance to handle uris
        "vlc": {
            // the plugin name
            "module": "ovos-media-audio-plugin-vlc",

            // friendly names a user may use to refer to this playback handler
            // those will be parsed by OCP and used to initiate
            // playback in the request playback handler
            "aliases": ["VLC"],

            // deactivate a plugin by setting to false
            "active": true
        },
        // command line player uses configurable shell commands with file uris as arguments
        "cli": {
            // the plugin name
            "module": "ovos-media-audio-plugin-cli",

            // friendly names a user may use to refer to this playback handler
            // those will be parsed by OCP and used to initiate
            // playback in the request playback handler
            "aliases": ["Command Line"],

            // deactivate a plugin by setting to false
            "active": true
        },
        // gui uses mycroft-gui natively to handle uris
        "gui": {
            // the plugin name
            "module": "ovos-media-audio-plugin-gui",

            // friendly names a user may use to refer to this playback handler
            // those will be parsed by OCP and used to initiate
            // playback in the request playback handler
            "aliases": ["GUI", "Graphical User Interface"],

            // deactivate a plugin by setting to false
            "active": true
        }
    },

    // PlaybackType.VIDEO handlers
    "video_players": {
        // vlc player uses a headless vlc instance to handle uris
        "vlc": {
            // the plugin name
            "module": "ovos-media-video-plugin-vlc",

            // friendly names a user may use to refer to this playback handler
            // those will be parsed by OCP and used to initiate
            // playback in the request playback handler
            "aliases": ["VLC"],

            // deactivate a plugin by setting to false
            "active": true
        },
        // gui uses mycroft-gui natively to handle uris
        "gui": {
            // the plugin name
            "module": "ovos-media-video-plugin-gui",

            // friendly names a user may use to refer to this playback handler
            // those will be parsed by OCP and used to initiate
            // playback in the request playback handler
            "aliases": ["GUI", "Graphical User Interface"],

            // deactivate a plugin by setting to false
            "active": true
        }
    },

    // PlaybackType.WEBVIEW handlers
    "web_players": {
        // open url in the native browser
        "browser": {
            // the plugin name
            "module": "ovos-media-web-plugin-browser",

            // friendly names a user may use to refer to this playback handler
            // those will be parsed by OCP and used to initiate
            // playback in the request playback handler
            "aliases": ["Browser", "Local Browser", "Default Browser"],

            // deactivate a plugin by setting to false
            "active": true
        },
        // gui uses mycroft-gui natively to handle uris
        "gui": {
            // the plugin name
            "module": "ovos-media-web-plugin-gui",

            // friendly names a user may use to refer to this playback handler
            // those will be parsed by OCP and used to initiate
            // playback in the request playback handler
            "aliases": ["GUI", "Graphical User Interface"],

            // deactivate a plugin by setting to false
            "active": true
        }
    }
  }
}
```
