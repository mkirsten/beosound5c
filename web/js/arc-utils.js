// Arc Math Utilities
// Shared positioning math for all arc-based list views (softarc, CD, Spotify).
// Single source of truth — all arc item positioning flows through here.

const ArcMath = (() => {

    /**
     * Read softarc constants from the centralized Constants object.
     * Works in both parent page and iframe contexts.
     */
    function getConstants() {
        const _sa = (window.parent?.Constants || window.Constants)?.softarc || {};
        return {
            middleIndex:          _sa.middleIndex ?? 4,
            baseItemSize:         _sa.baseItemSize ?? 128,
            maxRadius:            _sa.maxRadius ?? 220,
            horizontalMultiplier: _sa.horizontalMultiplier ?? 0.35,
            baseXOffset:          _sa.baseXOffset ?? 100,
            scrollSpeed:          _sa.scrollSpeed ?? 0.5,
            scrollStep:           _sa.scrollStep ?? 0.5,
            snapDelay:            _sa.snapDelay ?? 1000,
        };
    }

    /**
     * Compute the visual properties of an item at a given relative position.
     *
     * @param {number} actualRelativePos - Distance from center (fractional, negative=above).
     * @param {Object} [opts] - Override defaults from Constants.softarc.
     * @param {number} [opts.baseXOffset]
     * @param {number} [opts.maxRadius]
     * @param {number} [opts.horizontalMultiplier]
     * @param {number} [opts.baseItemSize]
     * @param {number} [opts.scaleFactor=0.15]  - Scale falloff per unit distance.
     * @param {number} [opts.scaleFloor=0.4]    - Minimum scale.
     * @param {number} [opts.padding=20]         - Vertical gap between items.
     * @returns {{ x: number, y: number, scale: number, opacity: number }}
     */
    function getItemPosition(actualRelativePos, opts = {}) {
        const c = getConstants();
        const absPos            = Math.abs(actualRelativePos);
        const baseXOffset       = opts.baseXOffset ?? c.baseXOffset;
        const maxRadius         = opts.maxRadius ?? c.maxRadius;
        const horizontalMult    = opts.horizontalMultiplier ?? c.horizontalMultiplier;
        const baseItemSize      = opts.baseItemSize ?? c.baseItemSize;
        const scaleFactor       = opts.scaleFactor ?? 0.15;
        const scaleFloor        = opts.scaleFloor ?? 0.4;
        const padding           = opts.padding ?? 20;

        const scale = Math.max(scaleFloor, 1.0 - absPos * scaleFactor);
        const x     = baseXOffset + absPos * maxRadius * horizontalMult;
        const y     = actualRelativePos * (baseItemSize * scale + padding);

        return { x, y, scale, opacity: 1 };
    }

    /**
     * Compute visible items with positions for an arc list.
     *
     * @param {number} currentIndex - Smooth-scrolling center index (fractional).
     * @param {Array}  items        - Full item array.
     * @param {Object} [opts]       - Same as getItemPosition opts, plus:
     * @param {number} [opts.middleIndex] - Number of items shown above/below center.
     * @returns {Array} Items with added { index, relativePosition, x, y, scale, opacity, isSelected }.
     */
    function getVisibleItems(currentIndex, items, opts = {}) {
        const c = getConstants();
        const middleIndex = opts.middleIndex ?? c.middleIndex;
        const centerIndex = Math.round(currentIndex);
        const result = [];

        for (let rel = -middleIndex; rel <= middleIndex; rel++) {
            const idx = centerIndex + rel;
            if (idx < 0 || idx >= items.length) continue;

            const actualRel = rel - (currentIndex - centerIndex);
            const pos = getItemPosition(actualRel, opts);

            result.push({
                ...items[idx],
                index: idx,
                relativePosition: actualRel,
                x: pos.x,
                y: pos.y,
                scale: pos.scale,
                opacity: pos.opacity,
                isSelected: Math.abs(actualRel) < 0.5,
            });
        }
        return result;
    }

    /**
     * Apply arc transform to a DOM element.
     */
    function applyTransform(element, item) {
        element.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
        if (item.opacity !== undefined && item.opacity < 1) {
            element.style.opacity = item.opacity;
        }
    }

    return { getConstants, getItemPosition, getVisibleItems, applyTransform };
})();

// Export for both module and script contexts
if (typeof window !== 'undefined') window.ArcMath = ArcMath;
