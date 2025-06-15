
class ArcList {
    constructor() {
        // Configuration parameters
        this.SCROLL_SPEED = 0.15; // Adjustable scroll smoothness (0.1 = slow, 0.3 = fast)
        this.SCROLL_STEP = 0.2; // How much to scroll per key press (configurable angle/amount)
        this.SNAP_DELAY = 1000; // Milliseconds to wait before snapping to closest item
        this.VISIBLE_ITEMS = 9;
        this.MIDDLE_INDEX = Math.floor(this.VISIBLE_ITEMS / 2);
        
        // State
        this.currentIndex = 0;
        this.targetIndex = 0;
        this.lastScrollTime = 0;
        this.animationFrame = null;
        
        // DOM elements
        this.container = document.getElementById('arc-container');
        this.currentItemDisplay = document.getElementById('current-item');
        this.totalItemsDisplay = document.getElementById('total-items');
        
        // Generate items
        this.items = this.generateItems();
        
        // Initialize
        this.init();
    }
    
    generateItems() {
        const adjectives = ['Amazing', 'Brilliant', 'Creative', 'Dynamic', 'Epic', 'Fantastic', 'Glorious', 'Incredible', 'Luminous', 'Majestic'];
        const nouns = ['Galaxy', 'Phoenix', 'Thunder', 'Crystal', 'Shadow', 'Flame', 'Storm', 'Ocean', 'Mountain', 'Star'];
        
        return Array.from({ length: 100 }, (_, index) => {
            const name = `${adjectives[Math.floor(Math.random() * adjectives.length)]} ${nouns[Math.floor(Math.random() * nouns.length)]}`;
            return {
                id: Math.floor(Math.random() * 10000000000).toString().padStart(10, '0'),
                name: name.length > 30 ? name.substring(0, 30) : name,
                image: `https://picsum.photos/128/128?random=${index + 1}`
            };
        });
    }
    
    init() {
        this.setupEventListeners();
        this.startAnimation();
        this.updateCounter();
        this.totalItemsDisplay.textContent = this.items.length;
    }
    
    setupEventListeners() {
        document.addEventListener('keydown', (e) => this.handleKeyPress(e));
        
        // Auto-snap timer
        this.snapTimer = null;
        this.setupSnapTimer();
    }
    
    handleKeyPress(e) {
        const now = Date.now();
        this.lastScrollTime = now;
        
        if (e.key === 'ArrowUp') {
            this.targetIndex = Math.max(0, this.targetIndex - this.SCROLL_STEP);
            this.setupSnapTimer();
        } else if (e.key === 'ArrowDown') {
            this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + this.SCROLL_STEP);
            this.setupSnapTimer();
        }
    }
    
    setupSnapTimer() {
        if (this.snapTimer) {
            clearTimeout(this.snapTimer);
        }
        
        this.snapTimer = setTimeout(() => {
            if (Date.now() - this.lastScrollTime >= this.SNAP_DELAY) {
                const closestIndex = Math.round(this.targetIndex);
                const clampedIndex = Math.max(0, Math.min(this.items.length - 1, closestIndex));
                this.targetIndex = clampedIndex;
            }
        }, this.SNAP_DELAY);
    }
    
    startAnimation() {
        const animate = () => {
            // Smooth scrolling animation
            const diff = this.targetIndex - this.currentIndex;
            if (Math.abs(diff) < 0.01) {
                this.currentIndex = this.targetIndex;
            } else {
                this.currentIndex += diff * this.SCROLL_SPEED;
            }
            
            this.render();
            this.updateCounter();
            this.animationFrame = requestAnimationFrame(animate);
        };
        
        animate();
    }
    
    getVisibleItems() {
        const visibleItems = [];
        const startIndex = Math.max(0, Math.floor(this.currentIndex) - this.MIDDLE_INDEX);
        
        for (let i = 0; i < this.VISIBLE_ITEMS; i++) {
            const itemIndex = startIndex + i;
            if (itemIndex >= 0 && itemIndex < this.items.length) {
                const relativePosition = i - this.MIDDLE_INDEX - (this.currentIndex - Math.floor(this.currentIndex));
                const absPosition = Math.abs(relativePosition);
                
                // Skip items that would be beyond the list boundaries
                const actualItemPosition = this.currentIndex + relativePosition;
                if (actualItemPosition < -0.5 || actualItemPosition >= this.items.length - 0.5) {
                    continue;
                }
                
                // Arc positioning calculations - items curve to the right
                const maxRadius = 250;
                const x = Math.abs(relativePosition) * maxRadius * 0.3; // All items move to the right
                const y = relativePosition * 80; // Vertical spacing
                
                // Dynamic scaling (1.6 at center, 0.7 at edges)
                const scale = Math.max(0.7, 1.6 - (absPosition * 0.225));
                
                // Opacity and blur effects
                const opacity = Math.max(0.4, 1 - absPosition * 0.15);
                const blur = absPosition * 2;
                
                visibleItems.push({
                    ...this.items[itemIndex],
                    index: itemIndex,
                    x,
                    y,
                    scale,
                    opacity,
                    blur,
                    isSelected: Math.abs(relativePosition) < 0.5
                });
            }
        }
        
        return visibleItems;
    }
    
    render() {
        const visibleItems = this.getVisibleItems();
        
        // Clear container
        this.container.innerHTML = '';
        
        // Render visible items
        visibleItems.forEach(item => {
            const itemElement = document.createElement('div');
            itemElement.className = `arc-item ${item.isSelected ? 'selected' : ''}`;
            itemElement.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
            itemElement.style.opacity = item.opacity;
            itemElement.style.filter = `blur(${item.blur}px)`;
            
            itemElement.innerHTML = `
                <div class="item-name ${item.isSelected ? 'selected' : 'unselected'}">
                    ${item.name}
                </div>
                <div class="item-image-container ${item.isSelected ? 'selected' : ''}">
                    <img src="${item.image}" alt="${item.name}" class="item-image" loading="lazy">
                    <div class="item-overlay ${item.isSelected ? 'selected' : ''}"></div>
                </div>
            `;
            
            this.container.appendChild(itemElement);
        });
    }
    
    updateCounter() {
        this.currentItemDisplay.textContent = Math.floor(this.currentIndex) + 1;
    }
    
    destroy() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
        if (this.snapTimer) {
            clearTimeout(this.snapTimer);
        }
    }
}

// Initialize the app when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new ArcList();
});
