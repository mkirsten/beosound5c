"""Audio file constants shared by file-playback components."""

AUDIO_EXTENSIONS = {'.flac', '.mp3', '.wma', '.aac', '.wav', '.m4a', '.ogg', '.opus'}

# Extensions each target format can serve without transcoding.
# Sonos rejects audio/flac via play_uri (UPnP Error 714).
# Bluesound accepts FLAC natively (up to 192kHz/24-bit).
PASSTHROUGH_SETS = {
    'mp3':  {'.mp3', '.ogg', '.wav'},
    'flac': {'.flac', '.mp3', '.ogg', '.wav'},
}

# ffmpeg codec arguments per target format
TRANSCODE_CODECS = {
    'mp3':  ['-c:a', 'libmp3lame', '-q:a', '0'],
    'flac': ['-c:a', 'flac'],
}

# Backward compat alias
STREAMABLE_EXTENSIONS = PASSTHROUGH_SETS['mp3']

ARTWORK_NAMES = ['folder', 'cover', 'front']
ARTWORK_EXTS = ['.jpg', '.jpeg', '.png']
