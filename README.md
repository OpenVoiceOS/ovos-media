# OVOS media service

Media playback service for OpenVoiceOS

* [Install](#install)
* [Architecture](#architecture)
* [Plugins](#plugins)
  - [Media Plugins](#media-plugins)
  - [OCP Plugins](#ocp-plugins)
* [MPRIS integration](#mpris-integration)
* [Pipeline](#pipeline)
  - [ocp high](#ocp-high)
  - [ocp medium](#ocp-medium)
  - [ocp fallback](#ocp-fallback)
* [Favorite Songs](#favorite-songs)
* [Configuration](#configuration)

## Install

`pip install ovos-media` to install this package and the default plugins.

In order to use ovos-media you need to enable the OCP pipeline in ovos-core and to disable the old audio service 


disabling old OCP
```json
{
  "enable_old_audioservice": false
}
```


## Architecture

![imagem](https://github.com/NeonJarbas/ovos-media/assets/59943014/7dc1d635-4340-43db-a38d-294cfedab70f)

## Plugins

WIP

### Media Plugins

these plugins handle the actual track playback. OCP virtual player delegates media playback to these plugins

| plugin  | audio | video | web | remote | notes |
|---------------------------------------------------------------------------------------------|----|----|---|----|-------------------------------------------|
| [ovos-media-plugin-simple](https://github.com/OpenVoiceOS/ovos-media-plugin-simple)         | ✔️ | ❌ | ❌ | ❌ | default for audio                         |
| [ovos-media-plugin-qt5](https://github.com/OpenVoiceOS/ovos-media-plugin-qt5)               | ✔️ | ✔️ | ✔️ | ❌ | WIP - recommended for embedded ovos-shell |
| [ovos-media-plugin-mplayer](https://github.com/OpenVoiceOS/ovos-media-plugin-mplayer)       | ✔️ | ✔️ | ❌ | ❌ | recommended for video                     |
| [ovos-media-plugin-vlc](https://github.com/OpenVoiceOS/ovos-media-plugin-vlc)               | ✔️ | ✔️ | ❌ | ❌ |                                           |
| [ovos-media-plugin-chromecast](https://github.com/OpenVoiceOS/ovos-media-plugin-chromecast) | ✔️ | ✔️ | ❌ | ✔️ | extra: [cast_control](https://github.com/alexdelorenzo/cast_control) for MPRIS interface   |
| [ovos-media-plugin-spotify](https://github.com/OpenVoiceOS/ovos-media-plugin-spotify) | ✔️ | ❌ | ❌ | ✔️ | needs premium account<br>extra: [spotifyd](https://github.com/Spotifyd/spotifyd) for native spotify player  |
| ![imagem](https://github.com/OpenVoiceOS/ovos-media/assets/33701864/90f31b0a-dd56-457d-a3cf-7fc08b460038) [ovos-media-plugin-xdg](https://github.com/NeonGeckoCom/ovos-media-plugin-xdg) | ✔️ | ✔️ | ✔️ | ❌ | [xdg-open](https://man.archlinux.org/man/xdg-open.1) is for use inside a desktop session only |
| ![imagem](https://github.com/OpenVoiceOS/ovos-media/assets/33701864/90f31b0a-dd56-457d-a3cf-7fc08b460038) [ovos-media-plugin-webbrowser](https://github.com/NeonGeckoCom/ovos-media-plugin-webbrowser) | ❌ | ❌ | ✔️ | ❌ | [webbrowser](https://docs.python.org/3/library/webbrowser.html) is for use inside a desktop session only |


### OCP Plugins

handle extracting playable streams and metadata, skills might require specific plugins and will be ignored if plugins are missing

these plugins are used when a `sei//` is requested explicitly by a skill, or when a url pattern matches

| plugin  | descripton | Stream Extractor Ids | url pattern | 
|-------------------------------------------------------------------------------------|--------------------------|-------------------------------------------------|-----------------------------------------------------|
| [ovos-ocp-rss-plugin](https://github.com/OpenVoiceOS/ovos-ocp-rss-plugin)           | rss feeds                | `rss//`                                         |                                                     | 
| [ovos-ocp-bandcamp-plugin](https://github.com/OpenVoiceOS/ovos-ocp-bandcamp-plugin) | bandcamp urls            | `bandcamp//`                                    | `"bandcamp." in url`                                |
| [ovos-ocp-youtube-plugin](https://github.com/OpenVoiceOS/ovos-ocp-youtube-plugin)   | youtube urls             | `youtube//` , `ydl//`, `youtube.channel.live//` | `"youtube.com/" in url or "youtu.be/" in url`       |
| [ovos-ocp-m3u-plugin](https://github.com/OpenVoiceOS/ovos-ocp-m3u-plugin)           | .pls and .m3u formats    |`m3u//` , `pls//`                                | `".pls" in uri or ".m3u" in uri`                    |
| [ovos-ocp-news-plugin](https://github.com/OpenVoiceOS/ovos-ocp-news-plugin)         |  dedicated news websites |  `news//`                                       | `any([uri.startswith(url) for url in URL_MAPPINGS])`|



## MPRIS integration

Integration with MPRIS allows OCP to control external players

![imagem](https://github.com/NeonJarbas/ovos-media/assets/33701864/856c0228-8fc5-4ee6-a19d-4290f2e07258)


## Pipeline

Enabling OCP pipeline

```javascript
{
  // Intent Pipeline / plugins config
  "intents" : {
    // the pipeline is a ordered set of frameworks to send an utterance too
    // if one of the frameworks fails the next one is used, until an answer is found
    "pipeline": [
        "converse",
        "ocp_high",
        "...",
        "common_qa",
        "ocp_medium",
        "...",
        "ocp_fallback",
        "fallback_low"
    ]
  }
}
```


The dataset used to train the classifiers can be found [here](https://github.com/NeonJarbas/OCP-dataset)

Training code for classifiers used in the OCP pipeline can be found [here](https://github.com/OpenVoiceOS/ovos-classifiers/tree/dev/scripts/training/ocp)

Details on the classifiers can be found [here](https://github.com/OpenVoiceOS/ovos-core/tree/dev/ovos_core/intent_services/models)

### ocp high

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

### ocp medium

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

### ocp fallback

Uses [keyword matching](https://en.wikipedia.org/wiki/Aho%E2%80%93Corasick_algorithm) and requires at least 1 keyword

OCP skills can provide these keywords at runtime, additional keywords for things such as media_genre were collected via SPARQL queries to wikidata

The list of bundled keywords can be found [here](https://github.com/OpenVoiceOS/ovos-core/blob/dev/ovos_core/intent_services/models/ocp_entities_v0.csv)

Skill names are automatically added as keywords, this ensures that if the skill name is present in an utterance the ocp_fallback pipeline will catch it

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

## Favorite Songs

You can like a song that is currently playing via GUI and intent "I like that song"

![like](https://github.com/OpenVoiceOS/ovos-media/assets/33701864/27aee29a-ca3b-4c73-992e-9fd5ef513f4d)

Liked songs can be played via intent "play my favorite songs" or GUI

![favs](https://github.com/OpenVoiceOS/ovos-media/assets/33701864/cdf7a682-c417-43f7-a4ae-589b07de55cf)


## Configuration

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
