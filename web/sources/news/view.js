/**
 * News Source Preset — iframe-based ArcList V2 browser
 *
 * Displays Guardian news articles grouped by section.
 * Uses softarc/news.html with ArcList V2 page views for article text.
 */

const _newsController = (() => {
    const NEWS_URL = () => window.AppConfig?.newsServiceUrl || 'http://localhost:8776';

    function sendToIframe(type, data) {
        if (!window.IframeMessenger) return false;
        return IframeMessenger.sendToRoute('menu/news', type, data);
    }

    return {
        get isActive() { return true; },

        updateMetadata() {},

        handleNavEvent(data) {
            return sendToIframe('nav', { data });
        },

        handleButton(button) {
            if (sendToIframe('button', { button })) return true;
            return false;
        },
    };
})();

window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.news = {
    controller: _newsController,
    item: { title: 'NEWS', path: 'menu/news' },
    after: 'menu/playing',
    view: {
        title: 'NEWS',
        content: '<div id="news-container" style="width:100%;height:100%;"></div>'
    },

    onAdd() {},

    onMount() {
        const container = document.getElementById('news-container');
        if (!container || container.querySelector('iframe')) return;
        const iframe = document.createElement('iframe');
        iframe.id = 'preload-news';
        iframe.src = 'softarc/news.html';
        iframe.style.cssText = 'width:100%;height:100%;border:none;border-radius:8px;box-shadow:0 5px 15px rgba(0,0,0,0.3);';
        container.appendChild(iframe);
        if (window.IframeMessenger) {
            IframeMessenger.registerIframe('menu/news', 'preload-news');
        }
    },

    onRemove() {
        if (window.IframeMessenger) {
            IframeMessenger.unregisterIframe('menu/news');
        }
        const container = document.getElementById('news-container');
        if (container) container.innerHTML = '';
    },
};
