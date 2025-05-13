window.addEventListener('DOMContentLoaded', () => {
  attemptLogin();
});


// my refresh token is AQDzX78w7LHSFOrJR6_cpjSn5GM452xYf0pCgSYH1MFXerEtHV0Hk7hIlTKW2F9Q_D5IIRPZ9e4e3W529Kiy_9TUKcaT1Fnc-5tJTL7DvzJ6kwBRQH2CKSz5w1NN_f90FLc
const HA_URL   = 'http://homeassistant.local:8123';
const HA_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMDY0NDFjNDRjOWM0YTQ3ODk1OWVmMjcwYzY2MTU2ZiIsImlhdCI6MTc0NTI2ODYzNywiZXhwIjoyMDYwNjI4NjM3fQ.ldZPYpESQgL_dQj026faUhBzqTgJBVH4oYSrXtWzfC0';
const ENTITY   = 'media_player.medierum';

async function attemptLogin() {
  document.getElementById('login').style.display   = 'block';
  document.getElementById('content').style.display = 'none';

  let token = null;
  while (!token) {
    const r = await fetch('/token');
    if (r.status === 200) {
      token = (await r.json()).access_token;
      break;
    }
    await new Promise(r => setTimeout(r, 1000));
  }

  console.log('🔥 Spotify access token (hard-code this!):', token);

  document.getElementById('login').style.display   = 'none';
  document.getElementById('content').style.display = 'flex';

  loadPlaylists(token);
}

async function loadPlaylists(token) {
  const res = await fetch('https://api.spotify.com/v1/me/playlists', {
    headers: { 'Authorization': 'Bearer ' + token }
  });
  if (!res.ok) return console.error('Playlist fetch failed', await res.text());
  const data = await res.json();
  const container = document.getElementById('playlists');
  container.innerHTML = '';

  data.items.forEach(pl => {
    const div = document.createElement('div');
    div.className = 'item';
    const imgUrl = (pl.images && pl.images[0] && pl.images[0].url) || '';
    div.innerHTML = `
      <img src="${imgUrl}" alt="cover">
      <span>${pl.name}</span>
    `;
    div.onclick = () => {
      playPlaylist(pl.uri);
      loadTracks(token, pl.id, pl.uri);
    };
    container.appendChild(div);
  });
}

async function loadTracks(token, playlistId, playlistUri) {
  const res = await fetch(`https://api.spotify.com/v1/playlists/${playlistId}/tracks`, {
    headers: { 'Authorization': 'Bearer ' + token }
  });
  if (!res.ok) return console.error('Tracks fetch failed', await res.text());
  const data = await res.json();
  const cont = document.getElementById('tracks');
  cont.innerHTML = '';

  data.items.forEach(item => {
    const t = item.track;
    const imgUrl = (t.album && t.album.images && t.album.images[0] && t.album.images[0].url) || '';
    const div = document.createElement('div');
    div.className = 'item';
    div.innerHTML = `
      <img src="${imgUrl}" alt="album">
      <div>
        <div><strong>${t.name}</strong></div>
        <div>${t.artists.map(a=>a.name).join(', ')}</div>
      </div>
    `;
    div.onclick = () => playTrackThenQueue(t.uri, playlistUri);
    cont.appendChild(div);
  });
}

async function playPlaylist(uri) {
  await fetch(`${HA_URL}/api/services/media_player/play_media`, {
    method:'POST',
    headers:{
      'Authorization':'Bearer '+HA_TOKEN,
      'Content-Type':'application/json'
    },
    body: JSON.stringify({
      entity_id: ENTITY,
      media_content_type: 'playlist',
      media_content_id: uri
    })
  });
}

async function playTrackThenQueue(trackUri, playlistUri) {
  // 1) play the track
  let resp = await fetch(`${HA_URL}/api/services/media_player/play_media`, {
    method: 'POST',
    headers: {
      'Authorization': 'Bearer '+HA_TOKEN,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      entity_id: ENTITY,
      media_content_type: 'track',
      media_content_id: trackUri
    })
  });
  if (!resp.ok) {
    console.error('▶️ play_media failed:', resp.status, await resp.text());
    return;
  }

  // 2) enqueue the playlist using the generic play_media with enqueue:true
  resp = await fetch(`${HA_URL}/api/services/media_player/play_media`, {
      method: 'POST',
      headers:{
        'Authorization':'Bearer '+HA_TOKEN,
        'Content-Type':'application/json',
      },
      body: JSON.stringify({
        entity_id:         ENTITY,
        media_content_type:'playlist',
        media_content_id:  playlistUri,
        enqueue:           true
      })
    });
  if (!resp.ok) {
    console.error('➕ add_to_queue failed:', resp.status, await resp.text());
  }
}