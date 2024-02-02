# OVOS media service

Media playback service for OpenVoiceOS

## Install

`pip install ovos-media` to install this package and the default plugins.

In order to use ovos-media you need to enable the OCP pipeline in ovos-core and to disable the old audio service 


disabling old OCP
```json
{
  "enable_old_audioservice": false
}
```

enabling OCP pipeline
```javascript
{
  // Intent Pipeline / plugins config
  "intents" : {
    // the pipeline is a ordered set of frameworks to send an utterance too
    // if one of the frameworks fails the next one is used, until an answer is found
    "pipeline": [
        "converse",
        "ocp_high",
        "padatious_high",
        "adapt",
        "common_qa",
        "ocp_medium",
        "fallback_high",
        "padatious_medium",
        "fallback_medium",
        "ocp_fallback",
        "fallback_low"
    ]
  }
}
```

## Architecture

![imagem](https://github.com/NeonJarbas/ovos-media/assets/59943014/7dc1d635-4340-43db-a38d-294cfedab70f)

## MPRIS integration

Integration with MPRIS allows OCP to control external players

![imagem](https://github.com/NeonJarbas/ovos-media/assets/33701864/856c0228-8fc5-4ee6-a19d-4290f2e07258)

## Pipeline

Enabling pipelines

### ocp_high

Before regular intent stage, taking into account current OCP state  (media ready to play / playing)

Only matches if user unambiguously wants to trigger OCP

uses padacioso for exact matches

- play {query}
- previous  (media needs to be loaded)
- next  (media needs to be loaded)
- pause  (media needs to be loaded)
- play / resume (media needs to be loaded)
- stop (media needs to be loaded)

```python
from ocp_nlp.intents import OCPPipelineMatcher

ocp = OCPPipelineMatcher()
print(ocp.match_high("play metallica", "en-us"))
# IntentMatch(intent_service='OCP_intents',
#   intent_type='ocp:play',
#   intent_data={'media_type': <MediaType.MUSIC: 2>, 'query': 'metallica',
#                'entities': {'album_name': 'Metallica', 'artist_name': 'Metallica'},
#                'conf': 0.96, 'lang': 'en-us'},
#   skill_id='ovos.common_play', utterance='play metallica')

```

### ocp_mediun

uses a binary classifier to detect if a query is about media playback

```python
from ocp_nlp.intents import OCPPipelineMatcher

ocp = OCPPipelineMatcher()

print(ocp.match_high("put on some metallica", "en-us"))
# None

print(ocp.match_medium("put on some metallica", "en-us"))
# IntentMatch(intent_service='OCP_media',
#   intent_type='ocp:play',
#   intent_data={'media_type': <MediaType.MUSIC: 2>,
#                'entities': {'album_name': 'Metallica', 'artist_name': 'Metallica', 'movie_name': 'Some'},
#                'query': 'put on some metallica',
#                'conf': 0.9578441098114333},
#   skill_id='ovos.common_play', utterance='put on some metallica')
```

### ocp_fallback

Uses keyword matching and requires at least 1 keyword

OCP skills can provide these keywords at runtime, additional keywords for things such as media_genre were collected via SPARQL queries to wikidata

```python
from ocp_nlp.intents import OCPPipelineMatcher

ocp = OCPPipelineMatcher()

print(ocp.match_medium("i wanna hear metallica", "en-us"))
# None

print(ocp.match_fallback("i wanna hear metallica", "en-us"))
#  IntentMatch(intent_service='OCP_fallback',
#    intent_type='ocp:play',
#    intent_data={'media_type': <MediaType.MUSIC: 2>,
#                 'entities': {'album_name': 'Metallica', 'artist_name': 'Metallica'},
#                 'query': 'i wanna hear metallica',
#                 'conf': 0.5027561091821287},
#    skill_id='ovos.common_play', utterance='i wanna hear metallica')

```


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
