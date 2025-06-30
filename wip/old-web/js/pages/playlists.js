// Playlists page module
const PlaylistsPage = {
  // Reference to the App object
  app: null,
  
  // DOM elements specific to this page
  elements: {
    container: null,
    playlistItems: null
  },
  
  // Data for the page
  data: {
    playlists: [
      'Chill Vibes',
      'Workout Mix',
      'Top Hits',
      'Jazz Classics',
      'Indie Essentials',
      'Party Anthems',
      'Relaxing Piano',
      'Rock Legends',
      'Country Roads',
      'Hip Hop Beats',
      'Classical Moods',
      'Reggae Rhythms',
      'Electronic Dance',
      'Soulful Sounds',
      'Acoustic Favorites',
      'Latin Grooves',
      'Blues Masters',
      'Pop Classics',
      'Folk Tales',
      'Ambient Atmospheres'
    ],
    selectedIndex: 0
  },
  
  // Initialize the page
  init: function(app) {
    this.app = app;
    
    // Cache DOM elements
    this.elements.container = document.querySelector('.playlist-page');
    this.elements.playlistItems = document.getElementById('playlist-items');
    
    // Populate playlists
    this.populatePlaylists();
    
    // Select first playlist
    this.selectPlaylist(0);
    
    this.app.logMsg('Playlists page initialized');
  },
  
  // Populate the playlist items
  populatePlaylists: function() {
    this.elements.playlistItems.innerHTML = '';
    
    this.data.playlists.forEach((playlist, index) => {
      const li = document.createElement('li');
      li.textContent = playlist;
      li.addEventListener('click', () => this.selectPlaylist(index));
      this.elements.playlistItems.appendChild(li);
    });
  },
  
  // Select a playlist by index
  selectPlaylist: function(index) {
    // Ensure index is within bounds
    index = Math.max(0, Math.min(index, this.data.playlists.length - 1));
    
    // Store selected index
    this.data.selectedIndex = index;
    
    // Update UI
    const items = this.elements.playlistItems.querySelectorAll('li');
    items.forEach((item, i) => {
      item.classList.toggle('selected', i === index);
    });
    
    // Scroll selected item into view
    items[index].scrollIntoView({ behavior: 'smooth', block: 'center' });
  },
  
  // Handle wheel/nav events
  handleNav: function(direction) {
    let newIndex = this.data.selectedIndex;
    
    if (direction === 'clock') {
      newIndex = Math.min(newIndex + 1, this.data.playlists.length - 1);
    } else {
      newIndex = Math.max(newIndex - 1, 0);
    }
    
    this.selectPlaylist(newIndex);
    return true; // Event handled
  },
  
  // Handle go button
  handleGo: function() {
    const selectedPlaylist = this.data.playlists[this.data.selectedIndex];
    this.app.logMsg(`Selected playlist: ${selectedPlaylist}`);
    // In a real implementation, this would play the selected playlist
    // For now, just provide feedback
    const selectedItem = this.elements.playlistItems.querySelector('li.selected');
    
    // Flash effect
    if (selectedItem) {
      selectedItem.style.color = 'yellow';
      setTimeout(() => {
        selectedItem.style.color = 'cyan';
      }, 300);
    }
    
    return true; // Event handled
  },
  
  // Handle WebSocket events
  handleEvent: function(type, data) {
    if (type === 'nav') {
      return this.handleNav(data.direction);
    }
    
    if (type === 'button' && data.button === 'go') {
      return this.handleGo();
    }
    
    return false; // Event not handled
  },
  
  // Clean up when leaving the page
  unload: function() {
    // No specific cleanup needed for this page
    this.app.logMsg('Playlists page unloaded');
  }
};

export default PlaylistsPage; 