"""The B&O Mozart and ASE clients speak the shapes the products actually use.

Pinned from the Mozart OpenAPI (mozart-api.yaml), the decompiled B&O app
(private/apk/FINDINGS.md) and the emulator the app and Home Assistant were
watched against (bs9000-gateway's bo-emulator/):

  * Mozart: PlaybackState.state is RenderingState {"value": "started"} — not a
    bare string; metadata uses artistName/albumName; art[] carries small/
    medium/large or NxN keys; the command enum is play|pause|stop|skip|prev;
    POST /playback/uri takes {"location": url}; the level is read out of
    VolumeState {"level": {"level": n}}; products serve on port 80.
  * ASE: frames are {"notification": {type, data}}; VOLUME levels run in the
    product's 0-90 range; NOW_PLAYING_NET_RADIO carries image[] (not
    trackImage[]); the app steps legacy sources with List/StepUp|StepDown.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.modules.setdefault("hid", type(sys)("hid"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services"))

import players.ase as ase  # noqa: E402
import players.mozart as mozart  # noqa: E402
from lib.volume_adapters import ase as ase_vol  # noqa: E402
from lib.volume_adapters import mozart as mozart_vol  # noqa: E402


# ── Mozart ────────────────────────────────────────────────────────────────

class TestMozartRenderState:
    def test_rendering_state_object(self):
        assert mozart.render_state({"value": "started"}) == "playing"
        assert mozart.render_state({"value": "buffering"}) == "playing"
        assert mozart.render_state({"value": "paused"}) == "paused"
        for v in ("idle", "stopped", "ended", "error", "unknown", None):
            assert mozart.render_state({"value": v}) == "stopped"

    def test_bare_string_tolerated(self):
        assert mozart.render_state("started") == "playing"
        assert mozart.render_state("") == "stopped"


class TestMozartMetadata:
    def test_openapi_field_names(self):
        md = {"title": "Saffron", "artistName": "LOFI BEATS", "albumName": "Study Beats",
              "art": [{"url": "http://x/s.jpg", "size": "small"},
                      {"url": "http://x/l.jpg", "size": "large"},
                      {"url": "http://x/m.jpg", "size": "medium"}]}
        f = mozart.metadata_fields(md)
        assert (f["title"], f["artist"], f["album"]) == ("Saffron", "LOFI BEATS", "Study Beats")
        assert f["art_url"] == "http://x/l.jpg"

    def test_net_radio_pixel_keys_pick_largest(self):
        art = [{"url": "http://x/128", "key": "128x128"}, {"url": "http://x/1024", "key": "1024x1024"},
               {"url": "http://x/512", "key": "512x512"}]
        assert mozart.pick_art_url(art) == "http://x/1024"

    def test_single_unlabelled_entry(self):
        assert mozart.pick_art_url([{"url": "http://x/only"}]) == "http://x/only"
        assert mozart.pick_art_url([]) == ""
        assert mozart.pick_art_url(None) == ""

    def test_duration_prefers_progress_then_seconds_then_millis(self):
        assert mozart.duration_seconds({"progress": {"totalDuration": 240}}) == 240
        assert mozart.duration_seconds({"metadata": {"totalDurationSeconds": 200}}) == 200
        assert mozart.duration_seconds({"metadata": {"totalDuration": 180000}}) == 180
        assert mozart.duration_seconds({}) is None

    def test_source_type_is_an_object(self):
        blob = mozart.source_blob({"id": "beolink", "name": "Kitchen", "type": {"value": "beolink"}})
        assert "beolink" in blob and "kitchen" in blob


class TestMozartWire:
    def test_port_80_and_command_enum(self):
        assert mozart.MOZART_PORT == 80
        assert mozart_vol.MOZART_PORT == 80
        src = Path(mozart.__file__).read_text()
        for cmd in ("/playback/command/skip", "/playback/command/prev"):
            assert cmd in src
        assert "/playback/command/skipTo" not in src
        assert '{"location": url}' in src

    def test_volume_state_unwrap(self):
        assert mozart_vol.volume_level_from_state({"level": {"level": 37}, "muted": {"muted": False}}) == 37
        assert mozart_vol.volume_level_from_state({"level": 12}) == 12
        assert mozart_vol.volume_level_from_state(None) == 0

    def test_power_state_enum(self):
        assert mozart_vol.power_is_on({"value": "on"})
        for v in ("networkStandby", "standby", "shutdown", "storage"):
            assert not mozart_vol.power_is_on({"value": v})


# ── ASE ───────────────────────────────────────────────────────────────────

def _frame(ntype, data):
    return {"notification": {"id": 1, "timestamp": "2026-09-02T20:00:00.000Z",
                             "type": ntype, "kind": "renderer", "data": data}}


class TestAseNotifications:
    def test_envelope(self):
        assert ase.unwrap_notification(_frame("VOLUME", {"a": 1})) == ("VOLUME", {"a": 1})
        assert ase.unwrap_notification({"type": "source", "data": {}}) == ("SOURCE", {})
        assert ase.unwrap_notification("junk") == ("", {})

    def test_stored_music_uses_largest_track_image(self):
        m = ase.media_from_notification("NOW_PLAYING_STORED_MUSIC", {
            "name": "Say No to This", "artist": "Jasmine Cephas-Jones", "album": "Hamilton",
            "duration": 240,
            "trackImage": [{"url": "http://x/s", "size": "small"}, {"url": "http://x/l", "size": "large"}]})
        assert m["title"] == "Say No to This" and m["art_url"] == "http://x/l" and m["duration"] == 240

    def test_net_radio_uses_image_and_live_description(self):
        m = ase.media_from_notification("NOW_PLAYING_NET_RADIO", {
            "name": "P3", "liveDescription": "Morgonpasset",
            "image": [{"url": "http://x/p3", "size": "medium"}]})
        assert (m["title"], m["artist"], m["art_url"]) == ("P3", "Morgonpasset", "http://x/p3")

    def test_legacy_track_number_and_channel(self):
        assert ase.media_from_notification("NOW_PLAYING_LEGACY", {"trackNumber": "3"})["title"] == "Track 3"
        assert ase.media_from_notification("NOW_PLAYING_LEGACY", {"trackNumber": ""}) is None
        m = ase.media_from_notification("NUMBER_AND_NAME", {"number": 12, "name": "SVT1"})
        assert (m["title"], m["artist"]) == ("SVT1", "12")

    def test_non_media_frames_carry_no_media(self):
        assert ase.media_from_notification("VOLUME", {"speaker": {"level": 40}}) is None
        assert ase.media_from_notification("SOURCE", {"primary": "CD:x@y"}) is None

    def test_playback_state_sources(self):
        assert ase.playback_state_from("PROGRESS_INFORMATION", {"state": "play", "position": 3}) == "playing"
        assert ase.playback_state_from("PROGRESS_INFORMATION", {"state": "pause"}) == "paused"
        assert ase.playback_state_from("SOURCE", {"primaryExperience": {"state": "stop"}}) == "stopped"
        assert ase.playback_state_from("SHUTDOWN", {"reason": "standby"}) == "stopped"
        assert ase.playback_state_from("VOLUME", {"speaker": {"level": 1}}) is None

    def test_active_source_id(self):
        jid = "1790.1179011.23722957@products.bang-olufsen.com"
        assert ase.active_source_id({"primary": f"CD:{jid}"}) == f"CD:{jid}"
        assert ase.active_source_id({"primaryExperience": {"source": {"id": f"RADIO:{jid}"}}}) == f"RADIO:{jid}"
        assert ase.active_source_id({}) == ""


class TestAseVolumeRange:
    def test_volume_frame_scales_0_90_to_percent(self):
        pct, rmax = ase.volume_percent({"speaker": {"level": 45, "muted": False,
                                                    "range": {"minimum": 0, "maximum": 90}}})
        assert (pct, rmax) == (50, 90)

    def test_missing_range_assumes_90(self):
        assert ase.volume_percent({"speaker": {"level": 90}}) == (100, 90)
        assert ase.volume_percent({"speaker": {"level": 90}}, default_max=100) == (90, 100)
        assert ase.volume_percent({}) == (None, 90)

    def test_adapter_scaling_round_trips(self):
        assert ase_vol.percent_to_level(50, 90) == 45
        assert ase_vol.percent_to_level(100, 90) == 90
        assert ase_vol.percent_to_level(0, 90) == 0
        assert ase_vol.level_to_percent(45, 90) == 50
        assert ase_vol.range_max_from_volume({"volume": {"speaker": {"range": {"maximum": 90}}}}) == 90
        assert ase_vol.range_max_from_volume({}) == 90

    def test_level_body_is_flat(self):
        assert ase_vol.level_from_body({"level": 30}) == 30
        assert ase_vol.level_from_body({"speaker": {"level": 31}}) == 31
        assert ase_vol.level_from_body({}) is None

    def test_power_state(self):
        assert ase_vol.power_state_from({"standby": {"powerState": "on"}}) == "on"
        assert ase_vol.power_state_from({"standby": {"powerState": "allStandby"}}) == "allStandby"
        assert ase_vol.power_state_from(None) == ""


class TestAseLegacySources:
    def test_legacy_prefix_match_on_full_ids(self):
        jid = "1790.1179011.23722957@products.bang-olufsen.com"
        for sid in (f"CD:{jid}", f"RADIO:{jid}", f"TP1:{jid}", f"AUX_A:{jid}", "LINEIN2", "PHONO"):
            assert ase.is_legacy_source(sid), sid

    def test_streaming_sources_are_not_legacy(self):
        for sid in ("DEEZER:x@y", "TUNEIN_RADIO_X", "spotify", "bluetooth", "", None):
            assert not ase.is_legacy_source(sid), sid

    def test_wire(self):
        src = Path(ase.__file__).read_text()
        assert "/BeoZone/Zone/List/StepUp" in src and "/BeoZone/Zone/Stream/Forward" in src
        assert "total=None" in src  # the long-poll must not be cut by a total timeout
