// Main application
const App = {
  // Current page
  currentPage: null,
  currentPageModule: null,

  // WebSocket connection
  ws: null,
  reconnectAttempts: 0,
  MAX_RECONNECT_ATTEMPTS: 20,
  INITIAL_RECONNECT_DELAY: 1000,
  
  // HA config
  HA_URL: 'http://homeassistant.local:8123',
  HA_TOKEN: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMDY0NDFjNDRjOWM0YTQ3ODk1OWVmMjcwYzY2MTU2ZiIsImlhdCI6MTc0NTI2ODYzNywiZXhwIjoyMDYwNjI4NjM3fQ.ldZPYpESQgL_dQj026faUhBzqTgJBVH4oYSrXtWzfC0',
  ENTITY: 'media_player.medierum',
  
  // Core elements
  elements: {
    nav: null,
    navItems: null,
    pointer: null,
    artCont: null,
    pageCont: null,
    artwork: null,
    titleEl: null,
    albumEl: null,
    artistEl: null,
    wsLog: null,
    vg: null,
    ctx: null,
    loadingIndicator: null
  },
  
  // Default event handlers
  defaultHandlers: {
    button: function(data) {
      if (data.button === 'left') App.prevTrack();
      else if (data.button === 'right') App.nextTrack();
      else if (data.button === 'go') App.onGo();
    },
    nav: function(data) {
      // Get the current page name
      const currentPage = App.currentPage;
      
      // Handle scrolling for Home Assistant pages
      if (currentPage === 'doorcam' || currentPage === 'home-status') {
        const scrollContainer = document.getElementById(`${currentPage}-scroll-container`);
        if (scrollContainer) {
          // Amount to scroll - increased for more noticeable scrolling
          const scrollAmount = 80; // pixels to scroll
          
          if (data.direction === 'clock') {
            // Scroll down
            App.logMsg(`Scrolling down: current=${scrollContainer.scrollTop}, adding ${scrollAmount}`);
            scrollContainer.scrollBy({
              top: scrollAmount,
              behavior: 'smooth'
            });
            
            // Force repaint to ensure scroll happens
            setTimeout(() => {
              App.logMsg(`After scroll down: ${scrollContainer.scrollTop}`);
              
              // Update scroll position indicator
              const scrollIndicator = document.getElementById(`${currentPage}-scroll-indicator`);
              const scrollPosition = document.getElementById(`${currentPage}-scroll-position`);
              if (scrollIndicator && scrollPosition) {
                const maxScroll = scrollContainer.scrollHeight - scrollContainer.clientHeight;
                const scrollPercentage = maxScroll > 0 ? scrollContainer.scrollTop / maxScroll : 0;
                const indicatorHeight = scrollIndicator.clientHeight - scrollPosition.clientHeight;
                const newTop = scrollIndicator.offsetTop + (indicatorHeight * scrollPercentage);
                scrollPosition.style.top = `${newTop}px`;
              }
            }, 100);
            
            // Show brief feedback message
            const instruction = document.getElementById(`${currentPage}-instruction`);
            if (instruction) {
              instruction.textContent = 'Scrolling down...';
              instruction.style.opacity = '1';
              setTimeout(() => {
                instruction.style.opacity = '0';
              }, 1000);
            }
          } else {
            // Scroll up
            App.logMsg(`Scrolling up: current=${scrollContainer.scrollTop}, subtracting ${scrollAmount}`);
            scrollContainer.scrollBy({
              top: -scrollAmount,
              behavior: 'smooth'
            });
            
            // Force repaint to ensure scroll happens
            setTimeout(() => {
              App.logMsg(`After scroll up: ${scrollContainer.scrollTop}`);
              
              // Update scroll position indicator
              const scrollIndicator = document.getElementById(`${currentPage}-scroll-indicator`);
              const scrollPosition = document.getElementById(`${currentPage}-scroll-position`);
              if (scrollIndicator && scrollPosition) {
                const maxScroll = scrollContainer.scrollHeight - scrollContainer.clientHeight;
                const scrollPercentage = maxScroll > 0 ? scrollContainer.scrollTop / maxScroll : 0;
                const indicatorHeight = scrollIndicator.clientHeight - scrollPosition.clientHeight;
                const newTop = scrollIndicator.offsetTop + (indicatorHeight * scrollPercentage);
                scrollPosition.style.top = `${newTop}px`;
              }
            }, 100);
            
            // Show brief feedback message
            const instruction = document.getElementById(`${currentPage}-instruction`);
            if (instruction) {
              instruction.textContent = 'Scrolling up...';
              instruction.style.opacity = '1';
              setTimeout(() => {
                instruction.style.opacity = '0';
              }, 1000);
            }
          }
          return; // Exit after handling scroll
        }
      }
      
      // Default navigation behavior if not handled above
      App.logMsg(`Nav event with no handler: ${JSON.stringify(data)}`);
    },
    laser: function(data) {
      App.onLaser(data.position);
    },
    playback: function() {
      App.fetchMedia();
    },
    volume: function(data) {
      App.logMsg(`Volume message received: speed=${data.speed}, direction=${data.direction || 'none'}`);
      App.adjustVolume(data.speed, data.direction);
    }
  },
  
  // Track current volume level
  currentVolume: 0,
  
  // Initialize the application
  init: function() {
    // Cache DOM elements
    this.cacheElements();
    
    // Setup laser pointer
    this.setupLaserPointer();
    
    // Set up navigation event listeners
    this.setupNavigation();
    
    // Connect WebSocket
    this.connectWebSocket();
    
    // Start fetching media info
    this.fetchMedia();
    setInterval(() => this.fetchMedia(), 5000);
    
    // Load default page
    this.loadPage('playing-now');
  },
  
  // Cache DOM elements for faster access
  cacheElements: function() {
    this.elements.nav = document.getElementById('nav');
    this.elements.navItems = [...document.querySelectorAll('#nav li')];
    this.elements.pointer = document.getElementById('laser-pointer');
    this.elements.artCont = document.getElementById('artwork-container');
    this.elements.pageCont = document.getElementById('page-container');
    this.elements.artwork = document.getElementById('artwork');
    this.elements.titleEl = document.getElementById('track-title');
    this.elements.albumEl = document.getElementById('track-album');
    this.elements.artistEl = document.getElementById('track-artist');
    this.elements.wsLog = document.getElementById('ws-log');
    this.elements.vg = document.getElementById('vol-gauge');
    this.elements.ctx = this.elements.vg.getContext('2d');
    this.elements.loadingIndicator = document.getElementById('loading-indicator');
  },
  
  // Setup the laser pointer
  setupLaserPointer: function() {
    // Initial position
    const firstNav = this.elements.navItems[0];
    const navRect = this.elements.nav.getBoundingClientRect();
    const itemRect = firstNav.getBoundingClientRect();
    this.elements.pointer.style.top = (itemRect.top - navRect.top + 4) + 'px';
  },
  
  // Set up navigation event listeners
  setupNavigation: function() {
    this.elements.navItems.forEach((item, index) => {
      item.addEventListener('click', () => {
        const page = item.getAttribute('data-page');
        this.loadPage(page);
      });
    });
  },
  
  // Load a page by name
  loadPage: function(pageName) {
    // Show loading indicator
    this.elements.loadingIndicator.style.display = 'block';
    
    // Update navigation highlight
    this.updateNavigation(pageName);
    
    // Store current page
    this.currentPage = pageName;
    
    // Reset current page module
    if (this.currentPageModule && typeof this.currentPageModule.unload === 'function') {
      this.currentPageModule.unload();
    }
    this.currentPageModule = null;
    
    // Special case for "Playing now" - it's part of the main UI
    if (pageName === 'playing-now') {
      this.fadeToArtwork();
      this.elements.loadingIndicator.style.display = 'none';
      return;
    }
    
    // Check if we're running from file:// protocol
    const isFileProtocol = window.location.protocol === 'file:';
    
    if (isFileProtocol) {
      // When running from file://, use a different approach to load pages
      this.loadPageForFileProtocol(pageName);
    } else {
      // For HTTP/HTTPS, use fetch as before
      this.loadPageWithFetch(pageName);
    }
  },
  
  // Load page content using fetch (for HTTP/HTTPS)
  loadPageWithFetch: function(pageName) {
    // For all pages, use fetch as before
    fetch(`pages/${pageName}.html`)
      .then(response => {
        if (!response.ok) {
          // Instead of throwing an error, handle missing pages gracefully
          if (response.status === 404) {
            throw new Error('PAGE_NOT_FOUND');
          } else {
            throw new Error(`Failed to load page: ${response.status}`);
          }
        }
        return response.text();
      })
      .then(html => {
        // Insert the HTML into the page container
        this.elements.pageCont.innerHTML = html;
        
        // For HA pages, set the iframe src
        if (pageName === 'doorcam') {
          const iframe = document.getElementById('doorcam-frame');
          if (iframe) {
            // Using an optimized format that works better with scrolling
            iframe.src = `${this.HA_URL}/dashboard-cameras/home?auth=${this.HA_TOKEN}&kiosk`;
            
            // Add event listener to ensure iframe is loaded fully
            iframe.addEventListener('load', () => {
              this.logMsg('Doorcam iframe loaded, initializing scrolling');
              // Ensure iframe has proper height after loading
              iframe.style.height = '600vh';
              iframe.style.minHeight = '1200px';
            });
          }
          // Don't set the page title for doorcam
          this.fadeToPage('');
          
          // Set up scroll buttons
          this.setupScrolling('doorcam');
        } else if (pageName === 'home-status') {
          const iframe = document.getElementById('home-status-frame');
          if (iframe) {
            // Using an optimized format that works better with scrolling
            iframe.src = `${this.HA_URL}/dashboard-basement/basement?auth=${this.HA_TOKEN}&kiosk`;
            
            // Add event listener to ensure iframe is loaded fully
            iframe.addEventListener('load', () => {
              this.logMsg('Home status iframe loaded, initializing scrolling');
              // Ensure iframe has proper height after loading
              iframe.style.height = '600vh';
              iframe.style.minHeight = '1200px';
            });
          }
          // Don't set the page title for home-status
          this.fadeToPage('');
          
          // Set up scroll buttons
          this.setupScrolling('home-status');
        } else {
          // For other pages, set the title as normal
          this.fadeToPage(this.getPageTitle(pageName));
        }
        
        // Import and initialize the page module
        this.loadPageModule(pageName);
      })
      .catch(err => {
        console.warn('Failed to load page:', err.message);
        this.elements.loadingIndicator.style.display = 'none';
        
        // Special handling for page not found
        if (err.message === 'PAGE_NOT_FOUND') {
          this.showUnderConstructionPage(pageName);
        } else {
          // For other errors, show error message
          this.showErrorPage(err.message);
        }
      });
  },
  
  // Set up scrolling for Home Assistant pages
  setupScrolling: function(pageName) {
    // Get the scroll container and buttons
    const scrollContainer = document.getElementById(`${pageName}-scroll-container`);
    const scrollUpButton = document.getElementById(`${pageName}-scroll-up`);
    const scrollDownButton = document.getElementById(`${pageName}-scroll-down`);
    const instruction = document.getElementById(`${pageName}-instruction`);
    const scrollIndicator = document.getElementById(`${pageName}-scroll-indicator`);
    const scrollPosition = document.getElementById(`${pageName}-scroll-position`);
    
    if (scrollContainer && scrollUpButton && scrollDownButton) {
      this.logMsg(`Setting up scrolling for ${pageName}`);
      
      // Force scroll container to be scrollable
      scrollContainer.style.cssText = 'width: 95%; height: 95%; overflow-y: scroll !important; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; position: relative;';
      
      // Add the background element for scrolling if it doesn't exist
      if (!scrollContainer.querySelector('.scroll-content-background')) {
        const bgDiv = document.createElement('div');
        bgDiv.className = 'scroll-content-background';
        scrollContainer.appendChild(bgDiv);
        this.logMsg('Added background element to improve scrolling');
      }
      
      // Make sure the container has enough height to be scrollable
      const iframe = scrollContainer.querySelector('iframe');
      if (iframe) {
        // Important: Set direct styling on iframe to ensure scrollability
        iframe.style.height = '600vh'; // Much taller than viewport to ensure scrolling is possible (increased from 300%)
        iframe.style.minHeight = '1200px'; // Ensure minimum height
        iframe.style.width = '100%';
        iframe.style.border = 'none';
        iframe.style.pointerEvents = 'none'; // Ensure iframe doesn't capture events
        
        // Set a tall enough container to allow scrolling
        const contentDiv = document.createElement('div');
        contentDiv.style.cssText = 'position: absolute; top: 0; left: 0; width: 100%; height: 600vh; z-index: -1;';
        scrollContainer.appendChild(contentDiv);
        
        // Force initial load of iframe content
        const currentSrc = iframe.src;
        if (currentSrc) {
          // If src is already set, briefly reload it to ensure content is fully loaded
          setTimeout(() => {
            this.logMsg(`Reloading iframe to ensure content is available for scrolling`);
            iframe.src = currentSrc;
          }, 500);
        }
        
        this.logMsg(`Enhanced iframe styling for better scrolling control`);
      }
      
      // Set up scroll indicator
      const updateScrollIndicator = () => {
        if (scrollPosition && scrollIndicator) {
          const maxScroll = scrollContainer.scrollHeight - scrollContainer.clientHeight;
          const scrollPercentage = maxScroll > 0 ? scrollContainer.scrollTop / maxScroll : 0;
          const indicatorHeight = scrollIndicator.clientHeight - scrollPosition.clientHeight;
          const newTop = scrollIndicator.offsetTop + (indicatorHeight * scrollPercentage);
          
          scrollPosition.style.top = `${newTop}px`;
          this.logMsg(`Scroll position updated: ${Math.round(scrollPercentage * 100)}%`);
        }
      };
      
      // Add scroll event listener
      scrollContainer.addEventListener('scroll', () => {
        updateScrollIndicator();
        this.logMsg(`Container scrolled to ${scrollContainer.scrollTop}px`);
      });
      
      // Add click handlers to the buttons with more aggressive scrolling
      scrollUpButton.addEventListener('click', () => {
        const scrollAmount = 150; // Increased for more noticeable scrolling
        this.logMsg(`Scroll up button: current=${scrollContainer.scrollTop}, subtracting ${scrollAmount}`);
        
        // Force scroll the container
        scrollContainer.scrollBy({
          top: -scrollAmount,
          behavior: 'smooth'
        });
        
        // Ensure the scroll happens with a fallback
        setTimeout(() => {
          if (scrollContainer.scrollTop === 0) {
            scrollContainer.scrollTop = 0; // Reset to top if needed
          }
          updateScrollIndicator();
        }, 100);
      });
      
      scrollDownButton.addEventListener('click', () => {
        const scrollAmount = 150; // Increased for more noticeable scrolling
        this.logMsg(`Scroll down button: current=${scrollContainer.scrollTop}, adding ${scrollAmount}`);
        
        // Force scroll the container
        scrollContainer.scrollBy({
          top: scrollAmount,
          behavior: 'smooth'
        });
        
        // Ensure the scroll happens with a fallback
        setTimeout(() => {
          updateScrollIndicator();
        }, 100);
      });
      
      // Add keyboard event listeners for testing scrolling
      document.addEventListener('keydown', (e) => {
        if (this.currentPage !== pageName) return;
        
        if (e.key === 'ArrowUp') {
          scrollContainer.scrollBy({
            top: -150,
            behavior: 'smooth'
          });
          updateScrollIndicator();
          this.logMsg(`Arrow up: scrolled to ${scrollContainer.scrollTop}`);
        } else if (e.key === 'ArrowDown') {
          scrollContainer.scrollBy({
            top: 150,
            behavior: 'smooth'
          });
          updateScrollIndicator();
          this.logMsg(`Arrow down: scrolled to ${scrollContainer.scrollTop}`);
        }
      });
      
      // Reset the nav handler
      const self = this;
      this.defaultHandlers.nav = function(data) {
        // Get the current page name
        const currentPage = self.currentPage;
        
        // Handle scrolling for Home Assistant pages
        if (currentPage === 'doorcam' || currentPage === 'home-status') {
          const currentScrollContainer = document.getElementById(`${currentPage}-scroll-container`);
          if (currentScrollContainer) {
            // Amount to scroll - increased for more obvious scrolling
            const scrollAmount = 150;
            
            if (data.direction === 'clock') {
              // Scroll down
              self.logMsg(`Scrolling down: current=${currentScrollContainer.scrollTop}, adding ${scrollAmount}`);
              currentScrollContainer.scrollBy(0, scrollAmount);
              
              // Force scroll update
              setTimeout(() => {
                self.logMsg(`After scroll down: ${currentScrollContainer.scrollTop}`);
                // Update the scroll indicator
                const currentScrollIndicator = document.getElementById(`${currentPage}-scroll-indicator`);
                const currentScrollPosition = document.getElementById(`${currentPage}-scroll-position`);
                if (currentScrollIndicator && currentScrollPosition) {
                  const maxScroll = currentScrollContainer.scrollHeight - currentScrollContainer.clientHeight;
                  const scrollPercentage = maxScroll > 0 ? currentScrollContainer.scrollTop / maxScroll : 0;
                  const indicatorHeight = currentScrollIndicator.clientHeight - currentScrollPosition.clientHeight;
                  const newTop = currentScrollIndicator.offsetTop + (indicatorHeight * scrollPercentage);
                  currentScrollPosition.style.top = `${newTop}px`;
                }
              }, 50);
              
              // Show brief feedback message
              const currentInstruction = document.getElementById(`${currentPage}-instruction`);
              if (currentInstruction) {
                currentInstruction.textContent = 'Scrolling down...';
                currentInstruction.style.opacity = '1';
                setTimeout(() => {
                  currentInstruction.style.opacity = '0';
                }, 1000);
              }
            } else {
              // Scroll up
              self.logMsg(`Scrolling up: current=${currentScrollContainer.scrollTop}, subtracting ${scrollAmount}`);
              currentScrollContainer.scrollBy(0, -scrollAmount);
              
              // Force scroll update
              setTimeout(() => {
                self.logMsg(`After scroll up: ${currentScrollContainer.scrollTop}`);
                // Update the scroll indicator
                const currentScrollIndicator = document.getElementById(`${currentPage}-scroll-indicator`);
                const currentScrollPosition = document.getElementById(`${currentPage}-scroll-position`);
                if (currentScrollIndicator && currentScrollPosition) {
                  const maxScroll = currentScrollContainer.scrollHeight - currentScrollContainer.clientHeight;
                  const scrollPercentage = maxScroll > 0 ? currentScrollContainer.scrollTop / maxScroll : 0;
                  const indicatorHeight = currentScrollIndicator.clientHeight - currentScrollPosition.clientHeight;
                  const newTop = currentScrollIndicator.offsetTop + (indicatorHeight * scrollPercentage);
                  currentScrollPosition.style.top = `${newTop}px`;
                }
              }, 50);
              
              // Show brief feedback message
              const currentInstruction = document.getElementById(`${currentPage}-instruction`);
              if (currentInstruction) {
                currentInstruction.textContent = 'Scrolling up...';
                currentInstruction.style.opacity = '1';
                setTimeout(() => {
                  currentInstruction.style.opacity = '0';
                }, 1000);
              }
            }
            return; // Exit after handling scroll
          }
        }
        
        // Default navigation behavior if not handled above
        self.logMsg(`Nav event with no handler: ${JSON.stringify(data)}`);
      };
      
      // Hide the instruction after a few seconds
      if (instruction) {
        setTimeout(() => {
          instruction.style.opacity = '0';
        }, 3000);
      }
      
      // Add a small initial scroll to ensure the container is scrollable
      setTimeout(() => {
        // Use scrollBy for initial scrolling
        scrollContainer.scrollBy(0, 1);
        updateScrollIndicator();
        this.logMsg(`Initial scroll set using scrollBy to initialize scrolling`);
        
        // Force another scroll after a moment to ensure it "sticks"
        setTimeout(() => {
          // Use scrollBy again for secondary scroll
          scrollContainer.scrollBy(0, 4);
          updateScrollIndicator();
          this.logMsg(`Secondary scroll using scrollBy to ensure scrolling is active`);
          
          // Add a third scroll attempt after a longer delay
          setTimeout(() => {
            scrollContainer.scrollBy(0, 5);
            updateScrollIndicator();
            this.logMsg(`Third scroll attempt to ensure scrolling is fully initialized`);
          }, 1000);
        }, 500);
      }, 1000);
      
      // Initialize the scroll indicator position
      updateScrollIndicator();
      
      this.logMsg(`Scrolling set up for ${pageName} with visual indicator`);
    }
  },
  
  // Load page when using file:// protocol
  loadPageForFileProtocol: function(pageName) {
    // For file:// protocol, we'll use a predefined set of pages
    // If the page exists in our list, load it, otherwise show the under construction page
    switch(pageName) {
      case 'playlists':
        // For playlists, we know the content exists
        // Manually set the HTML for the playlists page
        this.elements.pageCont.innerHTML = 
        `<div class="playlist-page">
          <h1>Playlists</h1>
          <style>
            .playlist-page {
              width: 100%;
              height: 100%;
              display: flex;
              flex-direction: column;
              align-items: center;
              color: white;
              font-family: sans-serif;
              overflow: hidden;
            }
            
            #playlist-items {
              list-style-type: none;
              padding: 0;
              margin: 20px 0;
              width: 80%;
              max-height: 80%;
              overflow-y: scroll;
              display: flex;
              flex-direction: column;
              align-items: center;
              
              /* Hide scrollbars on all browsers */
              scrollbar-width: none; /* Firefox */
              -ms-overflow-style: none; /* Internet Explorer and Edge */
            }
            
            /* Hide scrollbar for Chrome, Safari and Opera */
            #playlist-items::-webkit-scrollbar {
              display: none;
              width: 0;
              background: transparent;
            }
            
            #playlist-items li {
              font-size: 16px;
              color: white;
              padding: 12px 20px;
              margin: 4px 0;
              width: 100%;
              text-align: center;
              transition: transform 0.3s ease, font-size 0.3s ease, color 0.3s ease;
              cursor: pointer;
            }
            
            #playlist-items li.selected {
              font-size: 24px;
              color: cyan;
              transform: scale(1.05);
            }
          </style>
          
          <ul id="playlist-items">
            <!-- Playlist items will be populated by the JS module -->
          </ul>
        </div>`;
        
        this.fadeToPage(this.getPageTitle(pageName));
        
        // Instead of importing, create the module object directly
        console.log('Setting up embedded Playlists module');
        
        // Create the playlists page module inline
        this.currentPageModule = {
          // Reference to the App object
          app: this,
          
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
            
            // Custom slow scroll animation instead of scrollIntoView
            const selectedItem = items[index];
            const container = this.elements.playlistItems;
            
            if (selectedItem && container) {
              // Get positions
              const containerRect = container.getBoundingClientRect();
              const selectedRect = selectedItem.getBoundingClientRect();
              
              // Calculate where we want to scroll to (center the item)
              const targetScrollTop = container.scrollTop + 
                (selectedRect.top - containerRect.top) - 
                (containerRect.height / 2) + 
                (selectedRect.height / 2);
              
              // Current scroll position
              const startScrollTop = container.scrollTop;
              const distance = targetScrollTop - startScrollTop;
              
              // Animation parameters - longer duration for slower scrolling
              const duration = 2500; // milliseconds (much slower than before)
              const startTime = performance.now();
              
              // Animation function
              const animateScroll = (currentTime) => {
                const elapsedTime = currentTime - startTime;
                
                if (elapsedTime < duration) {
                  // Easing function for smoother start/stop (ease-in-out)
                  const progress = this.easeInOutCubic(elapsedTime / duration);
                  container.scrollTop = startScrollTop + (distance * progress);
                  requestAnimationFrame(animateScroll);
                } else {
                  // Ensure we end exactly at target
                  container.scrollTop = targetScrollTop;
                }
              };
              
              // Start animation
              requestAnimationFrame(animateScroll);
            }
          },
          
          // Easing function for smoother scrolling
          easeInOutCubic: function(t) {
            return t < 0.5 
              ? 4 * t * t * t 
              : 1 - Math.pow(-2 * t + 2, 3) / 2;
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
        
        // Initialize the module
        if (typeof this.currentPageModule.init === 'function') {
          this.currentPageModule.init(this);
        }
        this.elements.loadingIndicator.style.display = 'none';
        break;
      
      case 'doorcam':
      case 'home-status':
      case 'scenes':
      case 'security':
      case 'control':
      case 'settings':
        // For these pages, try to load the HTML files directly
        fetch(`pages/${pageName}.html`)
          .then(response => {
            if (!response.ok) {
              throw new Error('PAGE_NOT_FOUND');
            }
            return response.text();
          })
          .then(html => {
            // Insert the HTML into the page container
            this.elements.pageCont.innerHTML = html;
            
            // For HA pages, show a message about file protocol limitations
            if (pageName === 'doorcam' || pageName === 'home-status') {
              this.elements.pageCont.innerHTML = `
                <div style="width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; color: white; font-family: sans-serif;">
                  <div style="width: 80%; max-width: 700px; margin: 20px auto; background: #111; padding: 20px; border-radius: 8px; text-align: center;">
                    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="margin-bottom: 16px;">
                      <path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z" stroke="#FFC107" stroke-width="2"/>
                      <path d="M12 8V12" stroke="#FFC107" stroke-width="2" stroke-linecap="round"/>
                      <circle cx="12" cy="16" r="1" fill="#FFC107"/>
                    </svg>
                    <h2 style="color: #FFC107; margin-bottom: 16px;">Browser Security Restriction</h2>
                    <p style="margin-bottom: 16px; line-height: 1.5;">
                      Due to browser security restrictions, Home Assistant pages cannot be embedded when running from a local file.
                    </p>
                    <h3 style="margin: 16px 0;">To view this page:</h3>
                    <div style="text-align: left; background: #222; padding: 16px; border-radius: 4px; margin-bottom: 16px;">
                      <p style="margin-bottom: 8px; font-weight: bold;">Option 1: Use a local web server</p>
                      <code style="display: block; background: #333; padding: 8px; border-radius: 4px; margin-bottom: 16px; overflow-x: auto; white-space: nowrap;">
                        cd /Users/kirsten/Development/beosound5c<br>
                        python server.py
                      </code>
                      <p style="margin-bottom: 8px;">Then visit: <a href="http://localhost:8000/web/index.html" style="color: cyan; text-decoration: none;">http://localhost:8000/web/index.html</a></p>
                    </div>
                    <div style="text-align: left; background: #222; padding: 16px; border-radius: 4px;">
                      <p style="margin-bottom: 8px; font-weight: bold;">Option 2: Access Home Assistant directly</p>
                      <p>Visit your Home Assistant instance directly at:</p>
                      <a href="${this.HA_URL}${pageName === 'doorcam' ? '/dashboard-iframe/doorcam' : '/dashboard-iframe/home-status'}?auth=${this.HA_TOKEN}" style="color: cyan; text-decoration: none; display: block; margin-top: 8px;">
                        ${this.HA_URL}${pageName === 'doorcam' ? '/dashboard-iframe/doorcam' : '/dashboard-iframe/home-status'}
                      </a>
                    </div>
                  </div>
                </div>
              `;
              
              // Don't show title for these pages
              this.fadeToPage('');
            } else {
              // For other pages, set the title as normal
              this.fadeToPage(this.getPageTitle(pageName));
            }
            
            // Set up scrolling for HA pages even when showing the error message
            if (pageName === 'doorcam' || pageName === 'home-status') {
              // Add scroll buttons and container if they were not included in the error message
              const addScrollButtons = !this.elements.pageCont.querySelector('.scroll-button');
              
              if (addScrollButtons) {
                // Create basic scroll container
                const container = this.elements.pageCont.querySelector('div[style*="width: 100%; height: 100%"]');
                if (container) {
                  container.classList.add('iframe-scroll-container');
                  container.id = `${pageName}-scroll-container`;
                  
                  // Add scroll buttons
                  const scrollUpButton = document.createElement('button');
                  scrollUpButton.className = 'scroll-button scroll-up';
                  scrollUpButton.id = `${pageName}-scroll-up`;
                  scrollUpButton.textContent = '▲';
                  scrollUpButton.style.cssText = 'position: absolute; right: 20px; top: 20px; z-index: 1000; padding: 10px 15px; background-color: rgba(0,0,0,0.5); color: white; border: none; border-radius: 5px; cursor: pointer;';
                  
                  const scrollDownButton = document.createElement('button');
                  scrollDownButton.className = 'scroll-button scroll-down';
                  scrollDownButton.id = `${pageName}-scroll-down`;
                  scrollDownButton.textContent = '▼';
                  scrollDownButton.style.cssText = 'position: absolute; right: 20px; top: 70px; z-index: 1000; padding: 10px 15px; background-color: rgba(0,0,0,0.5); color: white; border: none; border-radius: 5px; cursor: pointer;';
                  
                  const instruction = document.createElement('div');
                  instruction.className = 'scroll-instruction';
                  instruction.id = `${pageName}-instruction`;
                  instruction.textContent = 'Use wheel to scroll up/down';
                  instruction.style.cssText = 'position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); background-color: rgba(0,0,0,0.7); color: white; padding: 10px; border-radius: 5px; z-index: 1000; opacity: 1; transition: opacity 0.5s;';
                  
                  this.elements.pageCont.appendChild(scrollUpButton);
                  this.elements.pageCont.appendChild(scrollDownButton);
                  this.elements.pageCont.appendChild(instruction);
                  
                  // Set up scrolling
                  this.setupScrolling(pageName);
                }
              } else {
                // Set up scrolling with existing buttons
                this.setupScrolling(pageName);
              }
            }
            
            this.elements.loadingIndicator.style.display = 'none';
          })
          .catch(err => {
            // If we can't load the page, show the under construction page
            this.showUnderConstructionPage(pageName);
          });
        break;
        
      default:
        // For other pages, show the under construction page
        this.showUnderConstructionPage(pageName);
        break;
    }
  },
  
  // Load the JavaScript module for a page
  loadPageModule: function(pageName) {
    console.log(`Attempting to load JS module for page: ${pageName}`);
    import(`./pages/${pageName}.js`)
      .then(module => {
        console.log(`Successfully loaded module for: ${pageName}`);
        this.currentPageModule = module.default;
        if (typeof this.currentPageModule.init === 'function') {
          this.currentPageModule.init(this);
        }
        this.elements.loadingIndicator.style.display = 'none';
      })
      .catch(err => {
        console.warn(`Page module error for ${pageName}:`, err);
        // If the HTML loaded but JS module didn't, we can still show the page
        this.elements.loadingIndicator.style.display = 'none';
      });
  },
  
  // Show the "Under Construction" page
  showUnderConstructionPage: function(pageName) {
    this.elements.pageCont.innerHTML = `
      <div style="color: white; text-align: center; padding: 40px;">
        <h2>${this.getPageTitle(pageName)}</h2>
        <div style="margin: 30px 0;">
          <svg width="100" height="100" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z" stroke="cyan" stroke-width="2"/>
            <path d="M12 8V12" stroke="cyan" stroke-width="2" stroke-linecap="round"/>
            <circle cx="12" cy="16" r="1" fill="cyan"/>
          </svg>
        </div>
        <p style="font-size: 18px; margin-bottom: 10px;">Page Under Construction</p>
        <p style="opacity: 0.7; max-width: 400px; margin: 0 auto;">This page is still being developed. Please check back later.</p>
      </div>
    `;
    this.fadeToPage(`${this.getPageTitle(pageName)}`);
    this.elements.loadingIndicator.style.display = 'none';
  },
  
  // Show an error page
  showErrorPage: function(errorMessage) {
    this.elements.pageCont.innerHTML = `
      <div style="color: white; text-align: center; padding: 40px;">
        <h2>Error Loading Page</h2>
        <p style="color: #ff6b6b; margin: 20px 0;">${errorMessage}</p>
        <p>Please try again later.</p>
      </div>
    `;
    this.fadeToPage('Error');
    this.elements.loadingIndicator.style.display = 'none';
  },
  
  // Get page title from page name (used for display)
  getPageTitle: function(pageName) {
    // Convert from kebab-case to Title Case
    return pageName.split('-').map(word => 
      word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
  },
  
  // Update navigation highlighting
  updateNavigation: function(pageName) {
    this.elements.navItems.forEach(item => {
      if (item.getAttribute('data-page') === pageName) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });
  },
  
  // Connect to WebSocket
  connectWebSocket: function() {
    this.ws = new WebSocket('ws://localhost:8765');
    
    this.ws.onopen = () => {
      this.logMsg('WS open');
      this.reconnectAttempts = 0; // Reset attempts on successful connection
    };
    
    this.ws.onclose = () => {
      this.logMsg('WS close');
      this.attemptReconnect();
    };
    
    this.ws.onerror = e => {
      this.logMsg('WS err');
      this.attemptReconnect();
    };
    
    this.ws.onmessage = ev => {
      const {type, data} = JSON.parse(ev.data);
      this.logMsg(`${type}:${JSON.stringify(data)}`);
      this.handleWebSocketMessage(type, data);
    };
  },
  
  // Handle incoming WebSocket messages
  handleWebSocketMessage: function(type, data) {
    // Check if current page module has a handler
    if (this.currentPageModule && typeof this.currentPageModule.handleEvent === 'function') {
      const handled = this.currentPageModule.handleEvent(type, data);
      
      // If the page handled the event, don't use default handler
      if (handled) return;
    }
    
    // Use default handler if available
    if (this.defaultHandlers[type]) {
      this.defaultHandlers[type](data);
    }
  },
  
  // Attempt to reconnect to WebSocket
  attemptReconnect: function() {
    if (this.reconnectAttempts >= this.MAX_RECONNECT_ATTEMPTS) {
      this.logMsg('Max reconnection attempts reached');
      return;
    }

    const delay = this.INITIAL_RECONNECT_DELAY * Math.pow(2, this.reconnectAttempts);
    this.reconnectAttempts++;
    
    this.logMsg(`Attempting to reconnect in ${delay/1000} seconds...`);
    
    setTimeout(() => {
      this.logMsg(`Reconnection attempt ${this.reconnectAttempts}`);
      this.connectWebSocket();
    }, delay);
  },
  
  // Fade to artwork view (Playing Now)
  fadeToArtwork: function() {
    this.elements.pageCont.style.opacity = '0';
    this.elements.pageCont.style.pointerEvents = 'none';
    this.elements.artCont.style.opacity = '1';
  },
  
  // Fade to a specific page
  fadeToPage: function(pageTitle) {
    // Set page title if provided
    if (pageTitle) {
      const titleEl = document.querySelector('#page-container h1');
      if (titleEl) {
        titleEl.textContent = pageTitle;
      }
    }
    
    this.elements.pageCont.style.pointerEvents = 'auto';
    this.elements.artCont.style.opacity = '0';
    this.elements.pageCont.style.opacity = '1';
  },
  
  // Handle laser position event
  onLaser: function(pos) {
    if (pos > 70 && pos < 102) {
      this.elements.nav.classList.remove('hidden');
    } else {
      this.elements.nav.classList.add('hidden');
    }
    
    // Calculate which menu item to highlight based on position
    let eff = (100-pos) * 4;
    if (eff > 100) eff = 100;
    
    const idx = Math.round(eff * (this.elements.navItems.length-1) / 100);
    
    // Special case for Home status (last item)
    if (pos > 70 && pos < 102 && idx === this.elements.navItems.length-1) {
      const pageName = this.elements.navItems[idx].getAttribute('data-page');
      // If we're not already on Home status, load it
      if (this.currentPage !== pageName) {
        this.loadPage(pageName);
      }
    } else {
      // Update pointer position
      const navRect = this.elements.nav.getBoundingClientRect();
      const itemRect = this.elements.navItems[idx].getBoundingClientRect();
      this.elements.pointer.style.top = (itemRect.top - navRect.top + 4) + 'px';
      
      // Load page if not already on it
      const pageName = this.elements.navItems[idx].getAttribute('data-page');
      if (this.currentPage !== pageName) {
        this.loadPage(pageName);
      }
    }
  },
  
  // Fetch media information from Home Assistant
  fetchMedia: async function() {
    try {
      this.logMsg(`Fetching media state from ${this.HA_URL}/api/states/${this.ENTITY}`);
      const r = await fetch(`${this.HA_URL}/api/states/${this.ENTITY}`, {
        headers: { 'Authorization': 'Bearer ' + this.HA_TOKEN }
      });
      
      const d = await r.json();
      const pic = this.HA_URL + d.attributes.entity_picture;
      
      // Store current volume for use with the volume wheel
      const prevVolume = this.currentVolume;
      this.currentVolume = d.attributes.volume_level || 0;
      
      if (prevVolume !== this.currentVolume) {
        this.logMsg(`Volume updated from fetch: ${Math.round(this.currentVolume * 100)}%`);
      }
      
      // Log all relevant media info for debugging
      this.logMsg(`Media state: ${d.state}, volume: ${Math.round(this.currentVolume * 100)}%`);
      
      // update metadata
      this.elements.titleEl.textContent = d.attributes.media_title || '—';
      this.elements.albumEl.textContent = d.attributes.media_album_name || '—';
      this.elements.artistEl.textContent = d.attributes.media_artist || '—';
      
      // update artwork with crossfade
      if (this.elements.artwork.src !== pic) {
        this.elements.artwork.style.opacity = 0;
        this.elements.artwork.addEventListener('transitionend', function once() {
          this.removeEventListener('transitionend', once);
          this.src = pic;
          this.style.opacity = 1;
        });
      }
    } catch(e) { 
      this.logMsg(`Media fetch error: ${e.message}`);
      console.warn(e); 
    }
  },
  
  // Adjust volume
  adjustVolume: async function(speed, direction) {
    try {
      this.logMsg(`Current volume: ${Math.round(this.currentVolume * 100)}%, speed: ${speed}`);
      
      // Determine adjustment factor based on speed and direction
      let adjustment = direction === 'clock' ? 0.05 : -0.05;
      this.logMsg(`Using direction value: adjustment = ${adjustment}`);
      
      let newVolume = this.currentVolume + adjustment;
      
      // Keep volume within 0-1 range
      newVolume = Math.max(0, Math.min(1, newVolume));
      this.logMsg(`Adjusting volume from ${Math.round(this.currentVolume * 100)}% to ${Math.round(newVolume * 100)}%`);
      
      // Only update if there's an actual change
      if (newVolume !== this.currentVolume) {
        // Update Sonos volume via Home Assistant API
        this.logMsg(`Sending volume_set API call to ${this.HA_URL} for entity ${this.ENTITY}`);
        try {
          const response = await fetch(`${this.HA_URL}/api/services/media_player/volume_set`, {
            method: 'POST',
            headers: {
              'Authorization': 'Bearer ' + this.HA_TOKEN,
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({
              entity_id: this.ENTITY,
              volume_level: newVolume
            })
          });
          
          const responseStatus = response.status;
          this.logMsg(`Volume API response status: ${responseStatus}`);
          
          if (responseStatus >= 200 && responseStatus < 300) {
            // Success - update local volume immediately for responsiveness
            this.currentVolume = newVolume;
            this.logMsg(`Volume set successfully to ${Math.round(newVolume * 100)}%`);
          } else {
            const responseText = await response.text();
            this.logMsg(`Volume API error: ${responseStatus}, ${responseText.substring(0, 50)}...`);
          }
        } catch (apiError) {
          this.logMsg(`Volume API fetch error: ${apiError.message}`);
        }
        
        // Fetch latest state to sync with actual volume
        setTimeout(() => this.fetchMedia(), 500);
      } else {
        this.logMsg('No volume change needed (already at limit)');
      }
    } catch (e) {
      this.logMsg(`Volume error: ${e.message}`);
      console.error('Volume adjustment error:', e);
    }
  },
  
  // Previous track control
  prevTrack: async function() {
    await fetch(`${this.HA_URL}/api/services/media_player/media_previous_track`, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + this.HA_TOKEN,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({entity_id: this.ENTITY})
    });
    this.fetchMedia();
  },
  
  // Next track control
  nextTrack: async function() {
    await fetch(`${this.HA_URL}/api/services/media_player/media_next_track`, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + this.HA_TOKEN,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({entity_id: this.ENTITY})
    });
    this.fetchMedia();
  },
  
  // Play/Pause control
  onGo: async function() {
    // If the current page module has a custom Go handler, use it
    if (this.currentPageModule && typeof this.currentPageModule.handleGo === 'function') {
      const handled = this.currentPageModule.handleGo();
      if (handled) return;
    }
    
    // Special handling for Home Assistant pages - toggle scroll buttons
    if (this.currentPage === 'doorcam' || this.currentPage === 'home-status') {
      const scrollButtons = this.elements.pageCont.querySelectorAll('.scroll-button');
      if (scrollButtons.length > 0) {
        const isVisible = scrollButtons[0].style.opacity !== '0';
        
        scrollButtons.forEach(button => {
          button.style.opacity = isVisible ? '0' : '1';
          button.style.pointerEvents = isVisible ? 'none' : 'auto';
        });
        
        // Show instruction about button visibility
        const instruction = document.getElementById(`${this.currentPage}-instruction`);
        if (instruction) {
          instruction.style.opacity = '1';
          instruction.textContent = isVisible ? 'Scroll buttons hidden' : 'Scroll buttons visible';
          setTimeout(() => {
            instruction.style.opacity = '0';
          }, 2000);
        }
        
        this.logMsg(isVisible ? 'Scroll buttons hidden' : 'Scroll buttons shown');
        return;
      }
    }
    
    // Default behavior - toggle play/pause
    await fetch(`${this.HA_URL}/api/services/media_player/media_play_pause`, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + this.HA_TOKEN,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({entity_id: this.ENTITY})
    });
    this.fetchMedia();
  },
  
  // Logging function
  logMsg: function(m) {
    const recent = [];
    const log = this.elements.wsLog;
    recent.unshift(m);
    if (recent.length > 3) recent.pop();
    log.innerText = recent.join('\n');
  }
};

// Initialize App when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
  App.init();
});