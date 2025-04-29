class UIStore {
    constructor() {
        this.volume = 50;
        this.wheelPointerAngle = 180;
        this.topWheelPosition = 0;
        this.isNowPlayingOverlayActive = false;
        this.selectedMenuItem = -1;
        
        this.menuItems = [
            {title: 'HOME', path: 'menu'},
            {title: 'SETTINGS', path: 'menu/settings'},
            {title: 'SECURITY', path: 'menu/security'},
            {title: 'SCENES', path: 'menu/scenes'},
            {title: 'MUSIC', path: 'menu/music'},
            {title: 'NOW PLAYING', path: 'menu/nowplaying'}
        ];

        // Constants
        this.radius = 1000;
        this.angleStep = 7;
        
        // Initialize views first
        this.views = {
            'menu': {
                title: 'HOME',
                content: ''
            },
            'menu/music': {
                title: 'N.RADIO',
                content: `
                    <div class="arc-content-flow">
                        <div class="flow-items">
                            <div class="flow-item">Radio Station 1</div>
                            <div class="flow-item">Radio Station 2</div>
                            <div class="flow-item">Radio Station 3</div>
                            <div class="flow-item">Radio Station 4</div>
                            <div class="flow-item">Radio Station 5</div>
                            <div class="flow-item">Radio Station 6</div>
                            <div class="flow-item">Radio Station 7</div>
                            <div class="flow-item">Radio Station 8</div>
                            <div class="flow-item">Radio Station 9</div>
                            <div class="flow-item">Radio Station 10</div>
                        </div>
                    </div>`
            },
            'menu/settings': {
                title: 'N.MUSIC',
                content: `
                    <div class="arc-content-flow">
                        <div class="flow-items">
                            <div class="flow-item">Music Track 1</div>
                            <div class="flow-item">Music Track 2</div>
                            <div class="flow-item">Music Track 3</div>
                            <div class="flow-item">Music Track 4</div>
                            <div class="flow-item">Music Track 5</div>
                            <div class="flow-item">Music Track 6</div>
                            <div class="flow-item">Music Track 7</div>
                            <div class="flow-item">Music Track 8</div>
                            <div class="flow-item">Music Track 9</div>
                            <div class="flow-item">Music Track 10</div>
                        </div>
                    </div>`
            },
            'menu/nowplaying': {
                title: 'NOW PLAYING',
                content: `
                    <div id="now-playing" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-color: red; color: white;">
                        Now Playing View
                    </div>`
            }
        };

        // Set initial route
        this.currentRoute = 'menu';
        this.currentView = null;

        // Initialize UI
        this.initializeUI();
        this.setupEventListeners();
        this.updateView();
    }

    initializeUI() {
        // Draw initial arcs
        const mainArc = document.getElementById('mainArc');
        mainArc.setAttribute('d', arcs.drawArc(arcs.cx, arcs.cy, this.radius, 158, 202));

        const volumeArc = document.getElementById('volumeArc');
        this.updateVolumeArc();

        // Setup menu items
        this.renderMenuItems();
        this.updatePointer();
    }

    updateVolumeArc() {
        const volumeArc = document.getElementById('volumeArc');
        const startAngle = 95;
        const endAngle = 265;
        const volumeAngle = ((this.volume - 0) * (endAngle - startAngle)) / (100 - 0) + startAngle;
        volumeArc.setAttribute('d', arcs.drawArc(arcs.cx, arcs.cy, 270, startAngle, volumeAngle));
    }

    updatePointer() {
        const pointerDot = document.getElementById('pointerDot');
        const pointerLine = document.getElementById('pointerLine');
        
        const point = arcs.getArcPoint(this.radius, 0, this.wheelPointerAngle);
        const transform = `rotate(${this.wheelPointerAngle - 90}deg)`;
        
        [pointerDot, pointerLine].forEach(element => {
            element.setAttribute('cx', point.x);
            element.setAttribute('cy', point.y);
            element.style.transformOrigin = `${point.x}px ${point.y}px`;
            element.style.transform = transform;
        });
    }

    renderMenuItems() {
        const menuContainer = document.getElementById('menuItems');
        menuContainer.innerHTML = '';
        
        this.menuItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.textContent = item.title;
            
            const itemAngle = this.getStartItemAngle() + index * this.angleStep;
            const position = arcs.getArcPoint(this.radius, 20, itemAngle);
            
            Object.assign(itemElement.style, {
                position: 'absolute',
                left: `${position.x - 100}px`,
                top: `${position.y - 25}px`,
                width: '100px',
                height: '50px',
                cursor: 'pointer'
            });

            itemElement.addEventListener('mouseenter', () => {
                this.wheelPointerAngle = itemAngle;
                this.isSelectedItem(index);
                this.handleWheelChange();
            });

            if (this.isSelectedItem(index)) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });
    }

    getStartItemAngle() {
        const totalSpan = this.angleStep * (this.menuItems.length - 1);
        return 180 - totalSpan / 2;
    }

    isSelectedItem(index) {
        const itemAngle = this.getStartItemAngle() + index * this.angleStep;
        const isSelected = Math.abs(this.wheelPointerAngle - itemAngle) <= 2;
        
        if (isSelected && this.selectedMenuItem !== index) {
            this.selectedMenuItem = index;
            this.navigateToView(this.menuItems[index].path);
        }
        return isSelected;
    }

    setupEventListeners() {
        document.addEventListener('keydown', (event) => {
            switch (event.key) {
                case "ArrowUp":
                    this.topWheelPosition = -1;
                    this.handleWheelChange();
                    break;
                case "ArrowDown":
                    this.topWheelPosition = 1;
                    this.handleWheelChange();
                    break;
                case "ArrowLeft":
                    this.volume = Math.max(0, this.volume - 5);
                    this.updateVolumeArc();
                    break;
                case "ArrowRight":
                    this.volume = Math.min(100, this.volume + 5);
                    this.updateVolumeArc();
                    break;
            }
        });

        document.addEventListener('mousemove', (event) => {
            const mainMenu = document.getElementById('mainMenu');
            if (!mainMenu) return;

            const rect = mainMenu.getBoundingClientRect();
            const centerX = arcs.cx - rect.left;
            const centerY = arcs.cy - rect.top;
            
            const dx = event.clientX - rect.left - centerX;
            const dy = event.clientY - rect.top - centerY;
            let angle = Math.atan2(dy, dx) * 180 / Math.PI + 90;
            if (angle < 0) angle += 360;

            if ((angle >= 158 && angle <= 202) || 
                (angle >= 0 && angle <= 30) ||
                (angle >= 330 && angle <= 360)) {
                this.wheelPointerAngle = angle;
                this.handleWheelChange();
            }
        });

        document.addEventListener('wheel', (event) => {
            if (event.deltaY < 0) {
                this.volume = Math.min(100, this.volume + 2);
            } else {
                this.volume = Math.max(0, this.volume - 2);
            }
            this.updateVolumeArc();
        });

        document.getElementById('menuItems').addEventListener('click', (event) => {
            const clickedItem = event.target.closest('.list-item');
            if (!clickedItem) return;

            const index = Array.from(clickedItem.parentElement.children).indexOf(clickedItem);
            const itemAngle = this.getStartItemAngle() + index * this.angleStep;
            this.wheelPointerAngle = itemAngle;
            this.isSelectedItem(index);
            this.handleWheelChange();
        });
    }

    handleWheelChange() {
        if (this.topWheelPosition > 0) {
            if (this.wheelPointerAngle <= 202) {
                this.wheelPointerAngle += this.angleStep;
            }
        } else if (this.topWheelPosition < 0) {
            if (this.wheelPointerAngle >= 158) {
                this.wheelPointerAngle -= this.angleStep;
            }
        }

        // Check for now playing overlay
        if (this.wheelPointerAngle > 203 || this.wheelPointerAngle < 155) {
            if (!this.isNowPlayingOverlayActive) {
                this.isNowPlayingOverlayActive = true;
                this.navigateToView('menu/nowplaying');
            }
        } else if (this.isNowPlayingOverlayActive) {
            this.isNowPlayingOverlayActive = false;
            this.navigateToView(this.menuItems[this.selectedMenuItem]?.path || 'menu');
        }

        this.updatePointer();
        this.renderMenuItems();
        this.topWheelPosition = 0;
    }

    navigateToView(path) {
        console.log('Navigating to path:', path);
        console.log('Available views:', Object.keys(this.views));
        this.currentRoute = path;
        this.updateView();
    }

    updateView() {
        console.log('updateView called with currentRoute:', this.currentRoute);
        console.log('Available views:', Object.keys(this.views));
        
        const contentArea = document.getElementById('contentArea');
        if (!contentArea) {
            console.error('Content area not found');
            return;
        }

        const view = this.views[this.currentRoute];
        if (!view) {
            console.error('View not found for route:', this.currentRoute);
            // Fallback to menu view if route not found
            this.currentRoute = 'menu';
            this.updateView();
            return;
        }

        contentArea.innerHTML = view.content;
        this.setupContentScroll();
    }

    setupContentScroll() {
        const flowContainer = document.querySelector('.arc-content-flow');
        if (!flowContainer) return;

        let scrollPosition = 0;
        const angleStep = 10;
        const radius = 300;

        const updateFlowItems = () => {
            const items = document.querySelectorAll('.flow-item');
            items.forEach((item, index) => {
                const itemAngle = 180 + (index * angleStep) - scrollPosition;
                const position = arcs.getArcPoint(radius, 20, itemAngle);
                
                Object.assign(item.style, {
                    position: 'absolute',
                    left: `${position.x - 200}px`,
                    top: `${position.y - 25}px`,
                    opacity: Math.abs(itemAngle - 180) < 20 ? 1 : 0.5,
                    transform: `scale(${Math.abs(itemAngle - 180) < 20 ? 1 : 0.9})`,
                    fontWeight: Math.abs(itemAngle - 180) < 2 ? 'bold' : 'normal'
                });
            });
        };

        // Handle wheel events for content scrolling
        flowContainer.addEventListener('wheel', (event) => {
            event.preventDefault();
            const totalItems = document.querySelectorAll('.flow-item').length;
            const maxScroll = (totalItems - 1) * angleStep;
            
            if (event.deltaY > 0 && scrollPosition < maxScroll) {
                scrollPosition += angleStep;
            } else if (event.deltaY < 0 && scrollPosition > 0) {
                scrollPosition -= angleStep;
            }
            
            updateFlowItems();
        });

        // Initial position
        updateFlowItems();
    }
} 