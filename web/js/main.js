// Initialize the UI Store and make it globally accessible
window.addEventListener('DOMContentLoaded', () => {
    // Create the UI store
    const uiStore = new UIStore();
    
    // Make it accessible globally for cursor-handler.js
    window.uiStore = uiStore;
}); 